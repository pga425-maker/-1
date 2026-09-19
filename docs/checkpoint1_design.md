# [확인 지점 1] 설계안 — 축제 부스 모의 주식 수익률 대결

작성 시점 기준 구현 코드는 아직 없다. 이 문서는 승인 대상 설계안이며,
승인 전에는 price_engine / index_engine 을 포함해 어떤 실행 코드도 작성하지 않는다.

---

## 0. 확인한 실행 환경

| 항목 | 확인값 | 스펙 요구 | 판정 |
|---|---|---|---|
| Python | 3.11.15 | 3.11 | 충족 |
| Node / npm | 22.22.2 / 10.9.7 | Vite 빌드용 | 충족 |
| SQLite | 3.45.1 (표준 라이브러리) | SQLite | 충족 |

미확인 사항: 행사 당일 노트북에 동일 버전이 설치되어 있는지는 확인할 방법이 없다.
README 운영 절차에 버전 확인 단계를 넣는다.

---

## 1. 폴더 구조

스펙 13번 구조를 그대로 따르되, 아래 다섯 개 모듈을 추가로 둔다.
13번 목록에 없는 파일이므로 추가 여부를 승인받아야 한다(결정-10).

```
backend/
  main.py              FastAPI 앱, 라우팅, 정적 파일 서빙
  db.py                (추가) SQLite 연결·스키마 생성·마이그레이션
  models.py            Pydantic 요청/응답 모델 + 도메인 dataclass
  scenario_loader.py   (추가) scenarios/*.json 스캔·검증·파싱
  price_engine.py      순수 함수. 틱당 가격 계산
  index_engine.py      순수 함수. 공포지수·환율 평균회귀
  event_scheduler.py   (추가) 뉴스/공매도 2단계 스케줄 사전 전개
  game_loop.py         틱 루프, 주문 큐, 체결, 브로드캐스트
  ws_hub.py            (추가) WebSocket 연결 관리·브로드캐스트
  scoring.py           수익률·위험조정점수·MDD·순위
  analysis.py          사후 분석 (tick_log / orders / events_fired 만 사용)
  bots.py              (추가) 봇 5종. simulate_tuning 과 self_check 가 공유
  simulate_tuning.py   headless 그리드 서치
  self_check.py        자가 검증 루프
  tests/
    test_price_engine.py  test_index_engine.py  test_event_scheduler.py
    test_scoring.py       test_orders.py        test_concurrency.py
    test_analysis.py      test_scenario_schema.py

frontend/
  src/pages/  Onboard.jsx Play.jsx StockDetail.jsx News.jsx
              Market.jsx Rank.jsx Result.jsx Board.jsx Admin.jsx
  src/components/  (공용 카드·티커·탭바·아이콘 SVG)
  src/fonts/       BlackHanSans / NotoSansKR woff2 (로컬 번들)
  src/theme.js     14번 디자인 토큰 단일 출처

scenarios/  festival_01.json
scripts/    rehearsal.py  run_rehearsal.sh  load_test.py  e2e_smoke.py
docs/       (추가) 설계 문서
run_all_tests.sh  setup.sh  run.sh  README.md
```

`docs/` 와 `setup.sh` / `run.sh` 도 13번 목록에 없는 추가분이다.

---

## 2. DB 스키마

금액은 전부 **원 단위 정수**로 다룬다. 부동소수 누적 오차가 생기면
16번 동시성 합격 기준("현금+평가액 총합이 수수료 오차 내 보존")을 검증할 수 없다.
비율·가격계수만 REAL 을 쓴다.

```sql
CREATE TABLE sessions (
  id              INTEGER PRIMARY KEY,
  scenario_file   TEXT NOT NULL,          -- 예: festival_01.json
  scenario_name   TEXT NOT NULL,
  scenario_sha256 TEXT NOT NULL,          -- 재현성 검증용
  seed            INTEGER NOT NULL,
  status          TEXT NOT NULL,          -- waiting | running | paused | ended
  current_tick    INTEGER NOT NULL DEFAULT 0,
  tick_seconds    REAL    NOT NULL,
  total_ticks     INTEGER NOT NULL,
  starting_cash   INTEGER NOT NULL,
  started_at      TEXT, ended_at TEXT, created_at TEXT NOT NULL
);

CREATE TABLE players (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES sessions(id),
  nickname      TEXT NOT NULL,
  device_token  TEXT NOT NULL,
  cash          INTEGER NOT NULL,         -- 가용 현금 (주문 예약분 차감 후)
  reserved_cash INTEGER NOT NULL DEFAULT 0, -- 대기 주문이 잡아둔 현금
  starting_cash INTEGER NOT NULL,
  join_tick     INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active',  -- active | kicked
  created_at    TEXT NOT NULL,
  UNIQUE(session_id, nickname),
  UNIQUE(session_id, device_token)
);

CREATE TABLE holdings (
  session_id   INTEGER NOT NULL,
  player_id    INTEGER NOT NULL,
  symbol       TEXT    NOT NULL,
  quantity     INTEGER NOT NULL DEFAULT 0,
  reserved_qty INTEGER NOT NULL DEFAULT 0,  -- 대기 매도 주문이 잡아둔 수량
  avg_cost     REAL    NOT NULL DEFAULT 0,  -- 매수 수수료 포함 평균단가
  realized_pnl INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, player_id, symbol)
);

CREATE TABLE orders (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL,
  player_id     INTEGER NOT NULL,
  symbol        TEXT    NOT NULL,
  side          TEXT    NOT NULL,          -- buy | sell
  requested_qty INTEGER NOT NULL,
  filled_qty    INTEGER NOT NULL DEFAULT 0,
  reason        TEXT,                      -- 매수 근거 6종. 매도는 NULL
  submit_tick   INTEGER NOT NULL,
  fill_tick     INTEGER,
  quoted_price  INTEGER NOT NULL,          -- 접수 시점 직전 틱 가격 (참가자가 본 값)
  fill_price    INTEGER,
  fee           INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL,             -- pending|filled|partial|rejected|cancelled
  reject_code   TEXT,                      -- 아래 코드표
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_orders_fill ON orders(session_id, fill_tick, symbol);
CREATE INDEX idx_orders_player ON orders(session_id, player_id);

CREATE TABLE tick_log (
  session_id         INTEGER NOT NULL,
  tick               INTEGER NOT NULL,
  symbol             TEXT    NOT NULL,
  base_drift         REAL NOT NULL,
  event_delta        REAL NOT NULL,
  fear_shock         REAL NOT NULL,
  fx_shock           REAL NOT NULL,
  total_script_delta REAL NOT NULL,
  f                  REAL NOT NULL,
  damp               REAL NOT NULL,
  actual_delta       REAL NOT NULL,
  price              INTEGER NOT NULL,
  buy_qty_submitted  INTEGER NOT NULL,   -- f 계산에 실제로 쓴 값
  sell_qty_submitted INTEGER NOT NULL,
  buy_qty_filled     INTEGER NOT NULL,   -- 체결 후 실측 (감사용, 결정-03 참고)
  sell_qty_filled    INTEGER NOT NULL,
  PRIMARY KEY (session_id, tick, symbol)
);

CREATE TABLE index_log (
  session_id  INTEGER NOT NULL,
  tick        INTEGER NOT NULL,
  fear_index  REAL NOT NULL,
  fear_target REAL NOT NULL,
  fx_rate     REAL NOT NULL,
  fx_target   REAL NOT NULL,
  PRIMARY KEY (session_id, tick)
);

CREATE TABLE events_fired (
  id                INTEGER PRIMARY KEY,
  session_id        INTEGER NOT NULL,
  event_key         TEXT NOT NULL,        -- 시나리오 내 고유 id
  type              TEXT NOT NULL,        -- normal | short_pressure
  difficulty        TEXT,                 -- direct | indirect | counterexample
  sector            TEXT,
  symbol            TEXT,
  headline          TEXT NOT NULL,
  headline_tick     INTEGER NOT NULL,
  impact_start_tick INTEGER NOT NULL,
  impact_end_tick   INTEGER NOT NULL,
  schedule_json     TEXT NOT NULL         -- 확정된 틱별 delta 배열 (샘플링 결과 포함)
);
```

의도적으로 만들지 않는 테이블: **참가자별 틱 단위 자산 스냅샷 테이블**.
12번이 "게임 중 별도 로깅을 추가하지 마라"고 했으므로, 위험조정점수에 필요한
틱별 수익률 통계는 게임 중 메모리에서 Welford 온라인 누적으로만 계산하고,
사후 분석은 `tick_log`(가격) + `orders`(보유 변화) 로 전량 재구성한다.
재구성 결과와 실시간 누적값이 일치하는지를 E2E 테스트에서 대조한다. (결정-11)

`reject_code` 코드표: `NO_CASH`, `NO_QTY`, `OVER_CONCENTRATION`,
`INVALID_QTY`, `UNKNOWN_SYMBOL`, `SESSION_NOT_RUNNING`, `NO_REASON`,
`LAST_TICK_CLOSED`, `PLAYER_KICKED`.

---

## 3. API 스펙

### 참가자
| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| POST | `/api/join` | `{nickname, device_token}` | `{player_token, player_id, snapshot}` / 409 `NICKNAME_TAKEN` / 400 `PROFANITY` |
| GET | `/api/state` | 헤더 `X-Player-Token` | 전체 스냅샷 (아래) |
| POST | `/api/order` | `{symbol, side, qty, reason}` | `{order_id, status:"pending", quoted_price, expected_amount, expected_fee}` / 400 + `reject_code` |
| GET | `/api/orders` | — | 내 주문 이력 |
| GET | `/api/rank?tab=return\|risk\|total` | — | 스냅샷 순위 (5~10틱 갱신) |
| GET | `/api/result` | — | 개인 결과 + 12번 분석 전 항목 |
| WS | `/ws/ticks?token=` | — | 틱 페이로드 |

`/api/state` 스냅샷(재접속·초기 로드 공용):
```json
{ "session": {"status","current_tick","total_ticks","tick_seconds","remaining_seconds"},
  "stocks": [{"symbol","name","sector","price","prev_close","chg_pct","avatar_color",
              "spark":[최근 60틱 가격]}],
  "indices": {"fear":{"value","display_delta"},"fx":{"value","display_delta"}},
  "news": [{"event_key","headline","sector","symbol","difficulty","tick",
            "elapsed_sec","impact_state":"pending|applying|done","is_short_pressure"}],
  "me": {"nickname","cash","reserved_cash","total_asset","return_pct","rank",
         "holdings":[{"symbol","quantity","avg_cost","value","pnl_pct"}],
         "pending_orders":[...]},
  "players_count": 48 }
```

### 관리자 (`X-Admin-Token`, `.env` 의 `ADMIN_PASSWORD` 로 발급)
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/admin/scenarios` | `scenarios/` 폴더 스캔 결과 + 검증 통과 여부 |
| POST | `/api/admin/start` | `{scenario_file, seed?}` |
| POST | `/api/admin/pause` / `/resume` / `/end` | 상태 전환 |
| POST | `/api/admin/kick` | `{player_id}` |
| GET | `/api/admin/export?table=` | CSV (players/orders/tick_log/index_log/events_fired/result) |
| GET | `/api/admin/health` | WS 연결 수, 마지막 틱 소요 ms, 예외 카운터 |
| WS | `/ws/board` | 프로젝터 전용 (집계 페이로드) |

스펙 13번이 명시한 최소 API 집합에 `/api/orders`, `/api/rank`, `/api/admin/scenarios`,
`/api/admin/kick`, `/api/admin/health`, `/ws/board` 를 더했다. 15번 화면 요구사항
(주문 이력, 3탭 순위, 시나리오 선택, 강제 퇴장, 프로젝터 화면)을 만족하려면 필요하다.

### WebSocket 페이로드 설계
매 틱 **공통 페이로드를 브로드캐스트**하고, 개인 데이터는 보내지 않는다.
개인 총자산은 클라이언트가 `보유수량 × 새 가격 + 현금` 으로 계산한다.
보유수량·현금이 바뀌는 순간(체결·거부)에만 해당 연결에 개인 메시지를 따로 보낸다.
50명 × 375틱 전원 개인화 직렬화를 피해 16번 부하 기준(p95 < 300ms)을 확보하기 위한 선택이다.

```json
// 공통 (매 틱)
{"type":"tick","t":123,"ts":1737000000123,
 "p":{"A001":52340,"A002":67810,...},
 "c":{"A001":0.0065,...},
 "fear":31.4,"fx":1298.2,
 "disp":{"fear_delta":4,"fx_delta":-6},          // 5~10틱 스냅샷 비교값
 "news":[신규 헤드라인만],
 "badges":{"A006":{"kind":"short_pressure","phase":"pump"}},
 "rank":[상위 10명]                                // 5~10틱마다만 포함
}
// 개인 (체결/거부 시에만)
{"type":"fills","orders":[{order_id,status,filled_qty,fill_price,fee,reject_code}],
 "cash":...,"reserved_cash":...,"holdings":[...]}
```

---

## 4. price_engine 설계

`backend/price_engine.py` — FastAPI, DB, WebSocket, 난수, 시간에 전부 의존하지 않는다.
**난수도 주입받는다.** `gauss(0, noise_sigma)` 를 엔진 안에서 뽑으면 순수 함수가 아니고
단위테스트로 불변 조건을 결정적으로 검증할 수 없다. 호출자(game_loop / simulate_tuning)가
시드 고정된 `random.Random` 으로 뽑아 `noise` 로 넘긴다.

```python
@dataclass(frozen=True)
class TickInput:
    symbol: str
    prev_price: int
    liquidity: int          # L_i
    beta: float
    fx_exposure: float
    scenario_drift: float   # 시나리오 해당 틱 목표 변동률
    noise: float            # 호출자가 주입한 gauss(0, noise_sigma) 표본
    event_delta: float      # 활성 이벤트 기여분 합 (impact_delay 경과분만)
    fear_change: float      # fear_index[t] - fear_index[t-1], 포인트 단위
    fx_change_pct: float    # (fx[t] - fx[t-1]) / fx[t-1], 소수 (결정-05)
    buy_qty: int
    sell_qty: int
    resistance_coef: float
    dampening_cap: float
    fear_sensitivity: float = 1.0   # 결정-06
    fx_sensitivity: float = 1.0     # 결정-06
    min_price: int = 100

@dataclass(frozen=True)
class TickResult:
    base_drift: float; event_delta: float; fear_shock: float; fx_shock: float
    total_script_delta: float; f: float; damp: float; actual_delta: float
    price: int

def compute_tick(inp: TickInput) -> TickResult: ...
```

계산 순서는 스펙 2번 1)~9) 를 그대로 따른다.

```
base_drift  = scenario_drift + noise
fear_shock  = -beta * fear_sensitivity * (fear_change / 100)
fx_shock    = fx_exposure * fx_sensitivity * fx_change_pct
total       = base_drift + event_delta + fear_shock + fx_shock
f           = clip((buy_qty - sell_qty) / liquidity, -1, 1)     # liquidity==0 -> ValueError
damp        = min(resistance_coef * abs(f), dampening_cap)
actual      = total * (1 - damp)
price       = max(round(prev_price * (1 + actual)), min_price)
```

불변 조건 (`tests/test_price_engine.py`, 난수 입력을 property 방식으로 대량 생성):
1. `sign(actual) == sign(total)` 또는 `actual == 0`
2. `abs(actual) <= abs(total)`
3. `price > 0`, `isfinite(actual)` 및 모든 중간값에 NaN/inf 없음
4. `liquidity == 0` → `ValueError`

불변 조건 1·2는 `0 <= damp < 1` 일 때만 자동 성립한다. `dampening_cap >= 1.0` 이면
방향이 뒤집힌다. 따라서 **시나리오 로더가 `0 <= dampening_cap < 1` 과
`resistance_coef >= 0` 을 로드 시점에 거부**한다. 엔진이 아니라 로더에서 막는 이유는
엔진을 순수하게 유지하고 잘못된 시나리오 파일이 게임 시작 전에 걸리게 하기 위함이다.

`min_price` 에 걸려 `round` 가 가격을 바꾼 경우, 실현 변동률은 `actual` 과 달라진다.
`tick_log` 에는 계산값 `actual_delta` 를 그대로 기록하고, 하한 클램프가 발동한 틱은
`price == min_price` 로 식별 가능하게 둔다(8번 `divergence_flag` 가 이 조건을 쓴다).

---

## 5. index_engine 설계

`backend/index_engine.py` — 역시 순수 함수, 난수 주입.

```python
def build_target_series(segments, total_ticks, initial) -> list[float]
def step(prev, target, theta, noise) -> float
def step_fear(prev, target, theta, noise) -> float     # 0..100 clamp
```

**목표값 램프 보간** (계단식 점프 금지 요구 충족):
- `segments` 는 `[{from, to, target}]`. `target` 은 그 구간이 **끝나는 시점의 목표값**이다.
- 앵커 = 직전 구간의 `target`, 첫 구간의 앵커는 `initial`.
- 구간 내 틱 t: `target[t] = lerp(앵커, seg.target, (t - seg.from) / (seg.to - seg.from))`
- 로더 검증: 구간이 0부터 시작, 빈틈·겹침 없음, 마지막 `to == total_ticks`.
  이 검증이 없으면 6번 타임라인과 시나리오 JSON 이 어긋나도 조용히 지나간다.

예시 `{"from":0,"to":90,"target":20}` + `initial: 20` → 0~90틱 목표 평탄 20.
이어서 `{"from":90,"to":150,"target":38}` → 90~150틱에 걸쳐 20에서 38로 완만한 램프.

**화면 표시 분리**: 계산용 `fear_index` / `fx_rate` 는 매 틱 갱신해 `index_log` 에 남기고,
화면의 `▲4 / ▼6` 은 `snapshot_interval_ticks`(기본 8틱) 전 값과의 차이로 계산해
WS `disp` 필드로 내려보낸다. 값 자체(31.4 → 표시는 31)는 매 틱 갱신해도 되지만,
변화분 화살표만 스냅샷 주기로 갱신한다.

`theta_fear`, `theta_fx`, `fear_noise_sigma`, `fx_noise_sigma`, `snapshot_interval_ticks`
전부 시나리오 JSON 파라미터. 적정값은 19번 자가 검증에서 튜닝하며,
현 시점에서 "이 값이 맞다"고 단정하지 않는다.

---

## 6. 2단계 이벤트 스케줄러 설계

`backend/event_scheduler.py`. 핵심 방침은 **사전 전개(precompute)** 다.
세션 시작 시 시드로 모든 이벤트의 틱별 delta 배열을 한 번에 확정하고
`events_fired.schedule_json` 에 저장한다. 그래야 (가) 불변 조건을 게임 시작 전에 검증할 수 있고
(나) self_check 이 375틱을 돌리지 않고도 스케줄을 검사할 수 있으며
(다) 재접속·서버 재시작 시 동일 스케줄이 복원된다.

```python
class EventScheduler:
    def __init__(self, events, stocks, total_ticks, rng): ...
    def delta_for(self, tick: int, symbol: str) -> float
    def badges_at(self, tick: int) -> dict[str, Badge]
    def headlines_at(self, tick: int) -> list[Headline]
    def state_of(self, event_key, tick) -> "pending" | "applying" | "done"
```

### normal 이벤트
`tick` 에 헤드라인 노출 → `tick + impact_delay_ticks` 부터 `spread_ticks` 에 걸쳐
`delta` 를 분배한다. **`spread_ticks` 는 현재 스펙 JSON 예시에 없는 필드다(결정-02).**
분배는 균등이 아니라 앞이 큰 지수 감쇠형으로 제안한다.

```
w_k ∝ exp(-k / tau),  k = 0..spread_ticks-1,  tau = spread_ticks / 2
delta_k = delta * w_k / sum(w)
```
이유: 균등 분배는 반영이 밋밋해 "반영중" 배지가 보이는 동안 참가자가 변화를 못 느낀다.
실제 시장의 정보 반영도 초기 충격이 크고 꼬리가 길다.

섹터 이벤트는 해당 섹터의 모든 종목에 같은 `delta_k` 를 적용한다.
종목 지정 이벤트(`symbol` 필드)는 그 종목에만 적용한다.

### short_pressure 이벤트 (2단계 펌프-덤프)
```
헤드라인/배지 노출 : tick
펌프 시작          : tick + impact_delay_ticks
펌프 구간          : pump.ticks 틱, 총합 = pump_total
덤프 시작          : 펌프 종료 직후
덤프 구간          : dump.ticks 틱, 총합 = -dump_total
```
`pump.min/max`, `dump.min/max` 는 **구간 전체 누적 총합**으로 해석한다(결정-01).
틱당 값으로 해석하면 8틱 × 0.08 = 누적 +64% 가 되어 스펙 6번 서사와 맞지 않는다.

분배 형태(제안):
- 펌프: 뒤로 갈수록 커지는 가속형. 미끼가 서서히 매력적으로 보여야 함정이 작동한다.
- 덤프: 앞이 큰 급락형. 반응 지연 1~4틱을 가진 참가자가 빠져나가기 어려워야 한다.

**불변 조건(단위테스트로 강제):**
1. `pump_total < dump_total` — 스펙 5번 필수 제약.
   단, 스펙의 샘플링 범위는 펌프 `0.08~0.15`, 덤프 `0.15~0.25` 로 **경계에서 겹친다.**
   펌프 0.15, 덤프 0.15 가 동시에 뽑히면 등호가 되어 조건이 깨진다.
   → 샘플링 후 `dump_total = max(dump_sample, pump_total * (1 + net_loss_margin))` 로
     강제한다. `net_loss_margin` 은 시나리오 파라미터(기본 0.15 제안).
2. 복리 순효과 `prod(1 + d_k) < 1` — 산술 합 조건보다 강한 실제 손실 조건.
   두 조건을 모두 건다. "급등 후 급락 위험" 경고가 거짓말이 되면 안 된다.
3. 펌프 구간 최고가 / 시작가 >= `min_visible_pump`(기본 1.05 제안) — 미끼가 보여야 한다.
   17번 (6) 자가 검증 항목과 같은 기준을 단위테스트에도 건다.

`price_engine` 계산식은 바뀌지 않는다. 스케줄러가 그 틱의 `event_delta` 를 넘길 뿐이다.

### 배지·표시 규칙
| 상태 | 기간 | 배지 |
|---|---|---|
| pending | headline_tick ~ impact_start-1 | 반영 대기 (#211A2E 배경 / #FBF7F0 글자) |
| applying | impact_start ~ impact_end | 반영중 (#EFEAE0 / #211A2E) |
| done | impact_end+1 ~ | 반영 완료 (#EFEAE0 / #211A2E) |

`spread_ticks` 가 없으면 `applying` 구간이 0틱이 되어 3단계 배지가 성립하지 않는다.
이것이 결정-02 가 단순한 편의 요청이 아닌 이유다.

공매도 집중 배지는 펌프·덤프 전 구간 경고 톤(주황/빨강)을 유지하고,
홈 상단 티커 바로 아래 배너로도 노출하며, 섹터가 아니라 종목 하나를 지정해 표시한다.

---

## 7. 게임 루프 틱 순서

```
틱 t 시작
 1. 대기 주문 큐 스냅샷 확정      -- 틱 t-1 동안 접수된 주문이 체결 대상
 2. index_engine  -> fear[t], fx[t], fear_change, fx_change_pct
 3. event_scheduler.delta_for(t, symbol) -> 종목별 event_delta
 4. 종목별 buy_qty/sell_qty 집계(접수 수량 기준) -> price_engine.compute_tick -> price[t]
 5. price[t] 로 주문 순차 체결(접수 순 FIFO). 현금·비중 재검증, 부족 시 부분체결
 6. DB 기록: tick_log, index_log, orders, holdings, players
 7. 참가자별 총자산 갱신 + Welford 온라인 통계(위험조정점수용, 메모리)
 8. WS 공통 브로드캐스트 + 체결자에게만 개인 메시지
 9. 5~10틱 주기: 순위 스냅샷 재계산 + 지수 표시 변화분 갱신
틱 t 종료 (목표 4.8초, 실제 소요 ms 를 admin/health 에 기록)
```

4단계와 5단계의 순서는 뒤집을 수 없다. 체결가가 `price[t]` 이므로 가격이 먼저 확정되어야
한다. 그 결과 `f` 는 **접수 수량** 기준이고 실제 체결 수량과 미세하게 다를 수 있다.
그래서 `tick_log` 에 `buy_qty_submitted` 와 `buy_qty_filled` 를 둘 다 남기고,
리허설 보고서에 불일치율을 실측해 보고한다. 숨기지 않는다.

---

## 8. 스펙 검토에서 나온 결정 요청 12건

스펙 19번에 따라 임의로 메우지 않고 승인을 요청한다.
각 항목마다 내 제안과 그 근거를 적었다. 그대로 가도 되면 "전부 승인"으로 답해도 된다.

### 결정-01 (모호) 펌프/덤프 min·max 의 단위
`"pump": {"ticks": 8, "min": 0.08, "max": 0.15}` 가 틱당 값인지 구간 총합인지 명시가 없다.
틱당이면 8틱 누적 +64%~+120% 가 되어 6번 서사와 맞지 않는다.
**제안: 구간 전체 누적 총합으로 해석.**

### 결정-02 (누락) normal 이벤트의 `spread_ticks` 필드
4번은 "몇 틱에 걸쳐 분산 적용"이라 했는데 7번 JSON 예시에는 해당 필드가 없다.
없으면 `delta: -0.10` 이 한 틱에 -10% 로 떨어지고, "반영중" 배지 구간도 0틱이 된다.
**제안: `spread_ticks` 필드 추가(기본 6), 지수 감쇠형 분배.**

### 결정-03 (누락) 주문 체결 시 현금 부족 처리 정책
10번은 "다음 틱 가격으로 체결"이지만, 참가자가 본 예상 금액은 직전 틱 가격 기준이다.
가격이 오르면 체결 시점에 현금이 모자랄 수 있다.
**제안:**
- 접수 시 `quoted_price × qty × (1 + 0.00015)` 만큼 현금을 `reserved_cash` 로 예약.
  같은 틱에 중복 주문을 넣어 현금을 초과 사용하는 것을 막는다.
- 체결 시 실제 가격으로 재검증. 부족하면 **가능한 수량만 부분체결**하고 잔량 취소,
  개인 WS 메시지로 알림(`status: partial`).
- 매도는 `reserved_qty` 로 동일하게 처리.
대안(체결가 기준 여유 마진을 미리 더 잡아두기)은 참가자가 본 예상 금액보다 많은 현금이
묶여 혼란스러우므로 택하지 않았다.

### 결정-04 (누락) 한 종목 50% 비중 제한의 판정 시점
가격이 올라 매매 없이 50%를 넘는 경우를 강제 매도해야 하는지 규정이 없다.
**제안: 매수 주문 체결 시점에만 검증(체결 후 예상 비중 > 50% 면 그 초과분 거부).
가격 상승으로 인한 초과는 허용하되 홈 화면 도넛차트에 분산도 경고만 표시.**
강제 매도는 참가자가 이해하기 어렵고 축제 부스에서 항의 요인이 된다.

### 결정-05 (모호) `fx_rate_change_pct` 의 단위
이름은 `_pct` 인데 퍼센트 숫자(-0.09)인지 소수(-0.0009)인지에 따라 충격이 100배 차이난다.
환율 1315, `noise_sigma` 1.2 기준 틱당 변화는 약 ±1.2원이다.
퍼센트 숫자로 해석하면 `fx_exposure` 0.8 종목이 틱당 ±7% 흔들려 게임이 붕괴한다.
**제안: 소수(fraction) 해석. `(fx[t] - fx[t-1]) / fx[t-1]`.**

### 결정-06 (제안) `fear_sensitivity` / `fx_sensitivity` 계수 추가
스펙 공식대로면 공포지수가 한 틱에 4포인트 오를 때 beta 1.1 종목은 -4.4% 를 맞는다.
`fear_noise_sigma` 0.8 만으로도 beta 1.8 종목은 틱당 ±1.44% 의 가격 노이즈가 생기는데,
이는 `noise_sigma` 0.004(0.4%)의 3.6배로 기본 노이즈를 압도한다.
한편 3번은 지수가 생동감 있게 움직일 것을 요구하므로 `noise_sigma` 를 무작정 낮출 수도 없다.
**제안: 두 계수를 시나리오 파라미터로 추가(기본 1.0 이면 스펙 원식과 완전히 동일).
8번 튜닝에서 함께 조정.** 스펙 공식 자체는 바꾸지 않는다.

참고로 평균회귀 덕분에 `fear_change` 의 375틱 합은 (최종값 - 초기값)으로 수렴하므로
누적 편향은 생기지 않는다. 문제는 누적이 아니라 틱당 체감 변동성이다.

### 결정-07 (누락) 종목별 `noise_sigma` 오버라이드
17번 (2)는 안정형 3종목(늘봄유통·한별에너지·파란로지스틱스)의 변동성이 씨드게임즈·미르소재보다
**확실히 낮을 것**을 요구한다. 그런데 현재 구조에서 종목별 변동성 차이를 만들 수단이 없다.
- `noise_sigma` 는 전역 파라미터 하나뿐이다.
- `beta` 는 `fear_shock` 에만 작용한다.
- `drift_segments` 는 방향성 추세이지 변동성이 아니다.

전역 `noise_sigma` 0.004 의 375틱 누적 랜덤워크 표준편차는 0.4% × √375 ≈ 7.75% 다.
안정형 종목의 의도된 등락폭이 ±5% 수준이라면 노이즈에 서사가 완전히 묻힌다.
**제안: `stocks[]` 에 `noise_sigma` 선택 필드 추가(미지정 시 `params.noise_sigma`).**
이게 없으면 17번 (2) 는 구조적으로 통과할 수 없다.

### 결정-08 (관찰 · 수치 근거) 유동성 L 값의 포화 문제
6번이 "값을 그대로 사용하라"고 한 L 값으로 `f` 를 계산해 본 결과다.
한 참가자가 한 종목에 넣을 수 있는 최대 금액은 총자산의 50%, 즉 500만 원이다.

| 종목 | L | 1인 최대 매수 | 1인 f | 50인 동시 f |
|---|---|---|---|---|
| 가온반도체 | 8000 | 96주 | 0.012 | 0.60 |
| 한별에너지 | 7500 | 73주 | 0.010 | 0.49 |
| 청솔바이오 | 3000 | 208주 | 0.069 | 3.47 (포화) |
| 늘봄유통 | 2800 | 322주 | 0.115 | 5.76 (포화) |
| 파란로지스틱스 | 2600 | 263주 | 0.101 | 5.06 (포화) |
| 씨드게임즈 | 900 | 609주 | 0.677 | 33.8 (포화) |
| 미르소재 | 700 | 781주 | 1.116 (1인만으로 포화) | 포화 |

즉 소형주 2종은 참가자 한 명만으로 `f` 가 상한에 붙고, 대형주 2종은 50명이 한 틱에
전원 최대 매수해도 `damp` 가 0.18(`resistance_coef` 0.3 기준)에 그친다.
"저항"이 의미 있는 해상도를 갖는 구간이 종목별로 극단적으로 다르다.

이것이 의도된 설계일 수도 있다(대형주는 개인이 못 움직인다는 것 자체가 교육적으로 타당).
**지금은 값을 바꾸지 않고 그대로 구현한다.** 다만 8번 튜닝과 9번 리허설에서
이 포화가 `influence_index` 를 망가뜨리는 것이 확인되면 L 조정안을 수치와 함께
제안하고 승인을 받겠다. 현 시점에서 "문제다"라고 단정하지 않는다.

### 결정-09 (누락) 중도 참가자의 위험조정 순위 자격
10번은 "참가 시점 기준 수익률로 평가"라고만 한다. 그런데 11번 위험조정점수는
틱별 수익률의 mean/std 이므로, 마지막 20틱에 들어와 운 좋게 오른 참가자가
표본이 적어 std 가 작게 나와 1위가 될 수 있다.
**제안: 참여 틱 수가 `min_ticks_for_risk_rank`(기본 60틱, 약 4.8분) 미만이면
위험조정·종합 탭에서 "집계 제외"로 별도 표기. 수익률 탭에는 그대로 포함.**

### 결정-10 (구조) 13번 파일 목록에 없는 모듈 추가
`db.py`, `scenario_loader.py`, `event_scheduler.py`, `ws_hub.py`, `bots.py`,
그리고 `docs/`, `setup.sh`, `run.sh`.
특히 `bots.py` 는 8번(튜닝)과 17번(자가 검증 (7)(8) 항목)이 같은 봇 5종을 써야 해서
공유 모듈로 빼지 않으면 구현이 중복된다.
**제안: 위 모듈 추가 승인.**

### 결정-11 (해석 확인) 12번 "게임 중 별도 로깅 금지"의 적용 범위
위험조정점수에는 참가자별 틱 단위 자산 시계열이 필요하다.
이를 DB 테이블로 남기면 12번 위반으로 읽힌다.
**제안: DB에는 남기지 않고 메모리 온라인 통계로만 계산. 사후 분석은
`tick_log` + `orders` 로 재구성하고, 재구성값과 실시간값이 일치하는지 E2E 에서 대조.**
부작용: 서버가 라운드 중 크래시하면 실시간 위험조정 통계는 사라진다(재구성으로 복구 가능).
이 위험은 잔여 위험 목록에 남긴다.

### 결정-12 (운영) 실행 방식과 프론트 서빙
"Docker 없이 명령어 2줄" + "QR 접속" 을 만족하려면 포트가 하나여야 한다.
Vite dev 서버를 따로 띄우면 포트가 2개가 되어 QR 주소가 갈린다.
**제안: `./setup.sh`(의존성 설치 + 프론트 빌드 + 폰트 번들 확인) 와
`./run.sh`(FastAPI 단일 프로세스가 `frontend/dist` 를 정적 서빙) 2줄.**
폰트는 Black Han Sans / Noto Sans KR 을 한글 서브셋 woff2 로 저장소에 포함해
CDN 없이 동작시킨다(둘 다 OFL 라이선스로 재배포 가능).

---

## 9. 지금 시점의 잔여 위험

구현 전이지만 미리 기록한다.
1. **학교 와이파이의 AP isolation** — 단말 간 통신이 차단되면 휴대폰이 노트북에 접속하지 못한다.
   코드로 해결 불가. README 운영 절차에 사전 확인 단계와 노트북 핫스팟 대비책을 넣는다.
2. **서버 크래시 시 실시간 위험조정 통계 소실** (결정-11).
3. **L 값 포화** (결정-08) — 튜닝·리허설에서 실측 후 재판단.
4. **폰트 서브셋 용량** — 닉네임에 쓰인 한자·특수 한글이 서브셋에서 빠지면 깨질 수 있다.
   KS X 1001 한글 2350자 + 확장 상용 음절을 포함하는 서브셋으로 만들고, 폴백 폰트를 지정한다.
5. **4.8초 틱 예산 초과** — 50명 체결 + DB 쓰기 + 브로드캐스트가 4.8초를 넘으면 드리프트가 생긴다.
   틱마다 실제 소요 ms 를 기록하고 부하 테스트에서 확인한다.

---

## 10. 승인자가 확인할 체크리스트

- [ ] 결정-01 ~ 결정-12 각각에 대해 제안 수용 여부
- [ ] DB 스키마에서 빠진 항목이 없는지 (특히 15번 화면이 요구하는 데이터)
- [ ] 추가한 API 6개(`/api/orders`, `/api/rank`, `/api/admin/scenarios`, `/api/admin/kick`,
      `/api/admin/health`, `/ws/board`)가 필요 범위를 넘지 않는지
- [ ] WS 개인화 최소화 방식(공통 브로드캐스트 + 체결 시에만 개인 메시지)에 동의하는지
- [ ] 난수를 엔진 밖에서 주입하는 순수 함수 설계에 동의하는지
- [ ] 이벤트 스케줄 사전 전개(precompute) 방식에 동의하는지

## 11. 승인 후 바로 할 일 (18번 확인 지점 1 이후 구간)

1. `backend/price_engine.py`, `backend/index_engine.py`, `backend/event_scheduler.py`,
   `backend/scenario_loader.py`, `backend/models.py` 구현
2. `scenarios/festival_01.json` 작성 — 6번 서사·타임라인과 3번 지수 구간을 일치시킴
3. 단위테스트 작성 및 통과
   - 실행 명령: `./run_all_tests.sh --unit`
   - 눈으로 확인할 것: price_engine 불변 조건 4개, 공매도 펌프<덤프 불변 조건 2종,
     공포지수 0~100 clamp, 램프 보간에 계단 점프 없음, 시나리오 스키마 검증 거부 케이스
4. 그 다음이 [확인 지점 2]의 `simulate_tuning` 4×4 그리드다.

현재 통과한 테스트는 없다. 위는 예정 항목이며 결과를 미리 단정하지 않는다.
