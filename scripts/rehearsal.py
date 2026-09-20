#!/usr/bin/env python3
"""리허설 하네스 — 실제로 떠 있는 서버에 진짜 HTTP/WebSocket 으로 붙는다.

8번 튜닝과 목적이 다르다. 튜닝은 price_engine 만 떼어 계산하지만, 리허설은
POST /api/join 으로 참가하고 POST /api/order 로 주문해서 서버, DB, WebSocket,
체결 로직 전체를 통과시킨다.

리허설 봇은 튜닝 봇과 달리 독립적으로 행동하지 않는다. 실제 현장의 50명은
몰려다니므로 두 가지를 얹는다.

    herding_prob          최근 매수가 몰린 종목을 따라 살 확률
    reaction_delay_ticks  배지와 뉴스를 보고 주문까지 걸리는 지연

실행:
    ./scripts/run_rehearsal.sh 50
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "rehearsal")
os.environ.setdefault("FESTIVAL_DB", str(ROOT / "rehearsal.db"))

import httpx            # noqa: E402
import uvicorn          # noqa: E402
import websockets       # noqa: E402

from backend.bots import REASON_BY_STRATEGY, STRATEGIES      # noqa: E402
from backend.main import app, state                          # noqa: E402

HOST = "127.0.0.1"
REPORT_PATH = ROOT / "rehearsal_report.md"

# 군집 표적을 고를 때 참고하는 최근 틱 수 (약 14초)
CROWD_WINDOW = 3


@dataclass
class Metrics:
    latencies: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timeouts: int = 0
    orders_sent: int = 0
    orders_rejected: int = 0
    reject_codes: collections.Counter = field(default_factory=collections.Counter)
    leader_history: list[int] = field(default_factory=list)
    tick_seen: collections.Counter = field(default_factory=collections.Counter)


class Swarm:
    """봇 무리가 공유하는 '지금 뭐 사는지' 정보.

    실제 부스에서는 옆 사람이 뭘 샀는지 말로 퍼진다. 그 효과를 그대로 흉내낸다.
    """

    def __init__(self) -> None:
        self.recent: collections.deque = collections.deque()
        self.favorite: str | None = None

    def record(self, tick: int, symbol: str, amount: int) -> None:
        self.recent.append((tick, symbol, amount))
        while self.recent and self.recent[0][0] < tick - CROWD_WINDOW:
            self.recent.popleft()
        totals: dict[str, int] = {}
        for _, sym, value in self.recent:
            totals[sym] = totals.get(sym, 0) + value
        self.favorite = max(totals, key=totals.__getitem__) if totals else None


class RehearsalBot:
    def __init__(self, index, token, strategy, herding, delay, rng, swarm, metrics):
        self.index = index
        self.token = token
        self.strategy = strategy
        self.herding = herding
        self.delay = delay
        self.rng = rng
        self.swarm = swarm
        self.metrics = metrics
        self.prices: dict[str, int] = {}
        self.history: dict[str, list[int]] = {}
        self.cash = 0
        self.holdings: dict[str, int] = {}
        self.tick = 0
        self.queued: list[tuple[int, dict]] = []   # (실행 틱, 주문)
        self.fresh_news_symbols: tuple[str, ...] = ()
        self.news_tick = -999
        self.badge_symbols: tuple[str, ...] = ()

    # ---------- 시장 관찰 ----------

    def on_tick(self, msg: dict) -> None:
        self.tick = msg["t"]
        for symbol, price in msg["p"].items():
            self.prices[symbol] = price
            self.history.setdefault(symbol, []).append(price)
        if msg.get("news"):
            self.news_tick = self.tick
            self.fresh_news_symbols = tuple(
                n["symbol"] for n in msg["news"] if n.get("symbol")
            ) or tuple(self.prices)
        self.badge_symbols = tuple(msg.get("badges", {}).keys())

    def on_fills(self, msg: dict) -> None:
        me = msg.get("me")
        if not me:
            return
        self.cash = me["cash"]
        self.holdings = {h["symbol"]: h["quantity"] for h in me["holdings"]}

    def window_return(self, symbol: str, window: int = 5) -> float:
        hist = self.history.get(symbol, [])
        if len(hist) <= window or hist[-window - 1] == 0:
            return 0.0
        return hist[-1] / hist[-window - 1] - 1.0

    # ---------- 판단 ----------

    def intend(self) -> dict | None:
        if not self.prices:
            return None
        symbols = tuple(self.prices)

        # 군집: 남들이 몰리는 것을 따라 산다
        if (
            self.herding > 0
            and self.swarm.favorite
            and self.rng.random() < self.herding
        ):
            return self._buy(self.swarm.favorite, 0.3)

        # 공매도 집중 배지가 뜨면 일부는 미끼를 문다
        if self.badge_symbols and self.rng.random() < 0.12:
            return self._buy(self.rng.choice(self.badge_symbols), 0.25)

        if self.strategy == "momentum":
            if self.rng.random() < 0.07:
                best = max(symbols, key=self.window_return)
                if self.window_return(best) > 0:
                    return self._buy(best, 0.3)
            if self.rng.random() < 0.05 and self.holdings:
                worst = min(self.holdings, key=self.window_return)
                if self.window_return(worst) < -0.01:
                    return self._sell(worst, 0.5)
        elif self.strategy == "contrarian":
            if self.rng.random() < 0.07:
                worst = min(symbols, key=self.window_return)
                if self.window_return(worst) < 0:
                    return self._buy(worst, 0.3)
            if self.rng.random() < 0.05 and self.holdings:
                best = max(self.holdings, key=self.window_return)
                if self.window_return(best) > 0.02:
                    return self._sell(best, 0.5)
        elif self.strategy == "random":
            if self.rng.random() < 0.09:
                symbol = self.rng.choice(symbols)
                if self.rng.random() < 0.6:
                    return self._buy(symbol, 0.22)
                if self.holdings.get(symbol):
                    return self._sell(symbol, 0.5)
        elif self.strategy == "buy_and_hold":
            if self.tick <= 12 and len(self.holdings) < 3 and self.rng.random() < 0.5:
                pool = [s for s in symbols if s not in self.holdings]
                if pool:
                    return self._buy(self.rng.choice(pool), 0.32)
        elif self.strategy == "news_reactive":
            if (
                self.fresh_news_symbols
                and self.tick - self.news_tick <= 4
                and self.rng.random() < 0.4
            ):
                return self._buy(self.rng.choice(self.fresh_news_symbols), 0.35)
        return None

    def _buy(self, symbol: str, ratio: float) -> dict | None:
        price = self.prices.get(symbol, 0)
        if price <= 0:
            return None
        qty = int(self.cash * ratio) // price
        if qty <= 0:
            return None
        return {
            "symbol": symbol, "side": "buy", "qty": qty,
            "reason": REASON_BY_STRATEGY[self.strategy],
        }

    def _sell(self, symbol: str, ratio: float) -> dict | None:
        held = self.holdings.get(symbol, 0)
        qty = int(held * ratio)
        if qty <= 0:
            return None
        return {"symbol": symbol, "side": "sell", "qty": qty, "reason": None}

    def step(self) -> list[dict]:
        """이번 틱에 실제로 낼 주문. 반응 지연을 거친 것만 나간다."""
        intent = self.intend()
        if intent is not None:
            self.queued.append((self.tick + self.delay, intent))
        ready = [order for at, order in self.queued if at <= self.tick]
        self.queued = [(at, o) for at, o in self.queued if at > self.tick]
        return ready


async def send_order(bot: RehearsalBot, http: httpx.AsyncClient, order: dict) -> None:
    metrics = bot.metrics
    try:
        res = await http.post(
            "/api/order", json=order, headers={"X-Player-Token": bot.token},
        )
        metrics.orders_sent += 1
        if res.status_code != 200:
            metrics.orders_rejected += 1
            metrics.reject_codes[(res.json() or {}).get("reject_code", "UNKNOWN")] += 1
        elif order["side"] == "buy":
            bot.swarm.record(
                bot.tick, order["symbol"], order["qty"] * bot.prices[order["symbol"]],
            )
    except Exception as exc:                                  # noqa: BLE001
        metrics.errors.append(f"주문 실패: {type(exc).__name__}: {exc}")


async def bot_task(bot: RehearsalBot, http: httpx.AsyncClient, base: str, stop):
    url = f"ws://{HOST}:{base}/ws/ticks?token={bot.token}"
    metrics = bot.metrics
    pending: list[asyncio.Task] = []
    try:
        async with websockets.connect(url, ping_interval=None, max_queue=64) as ws:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                except asyncio.TimeoutError:
                    if stop.is_set():
                        return
                    metrics.timeouts += 1
                    return
                msg = json.loads(raw)
                if msg.get("type") == "snapshot":
                    me = msg.get("me")
                    if me:
                        bot.cash = me["cash"]
                        bot.holdings = {
                            h["symbol"]: h["quantity"] for h in me["holdings"]
                        }
                    for s in msg.get("stocks", []):
                        bot.prices[s["symbol"]] = s["price"]
                        bot.history[s["symbol"]] = list(s.get("spark", []))
                    continue
                if msg.get("type") == "fills":
                    bot.on_fills(msg)
                    continue
                if msg.get("type") == "ended":
                    return
                if msg.get("type") != "tick":
                    continue

                if msg.get("ts"):
                    metrics.latencies.append(time.time() * 1000.0 - msg["ts"])
                metrics.tick_seen[msg["t"]] += 1
                bot.on_tick(msg)

                # 주문은 따로 띄워 보낸다. 수신 루프 안에서 기다리면 다음 틱 메시지를
                # 늦게 읽게 되어 브로드캐스트 지연 측정이 오염된다. 실제 휴대폰도
                # 주문 응답을 기다리며 화면 갱신을 멈추지 않는다.
                for order in bot.step():
                    pending.append(asyncio.create_task(send_order(bot, http, order)))
                pending = [t for t in pending if not t.done()]
    except Exception as exc:                                  # noqa: BLE001
        metrics.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def run_once(port, players, herding, delay, speed, seed, scenario_file):
    metrics = Metrics()
    swarm = Swarm()
    rng = random.Random(seed)
    base = f"http://{HOST}:{port}"

    async with httpx.AsyncClient(base_url=base, timeout=20.0) as http:
        login = await http.post("/api/admin/login", json={"password": "rehearsal"})
        headers = {"X-Admin-Token": login.json()["admin_token"]}
        started = await http.post(
            "/api/admin/start",
            json={"scenario_file": scenario_file, "seed": seed, "speed": speed},
            headers=headers,
        )
        if started.status_code != 200:
            raise RuntimeError(f"판을 시작하지 못했다: {started.text}")

        bots = []
        for i in range(players):
            res = await http.post("/api/join", json={
                "nickname": f"리허설{i:02d}",
                "device_token": f"rehearsal-{i:04d}-{seed}",
            })
            token = res.json()["player_token"]
            bots.append(RehearsalBot(
                index=i, token=token, strategy=STRATEGIES[i % len(STRATEGIES)],
                herding=herding, delay=delay,
                rng=random.Random(rng.randrange(1 << 30)),
                swarm=swarm, metrics=metrics,
            ))

        stop = asyncio.Event()
        tasks = [asyncio.create_task(bot_task(b, http, port, stop)) for b in bots]

        session = state.session
        total = session.scenario.total_ticks
        deadline = time.time() + 900
        last_leader = None
        leader_changes = 0
        while session.status == "running" and time.time() < deadline:
            await asyncio.sleep(0.3)
            if session.rank_snapshot:
                leader = session.rank_snapshot[0]["player_id"]
                if last_leader is not None and leader != last_leader:
                    leader_changes += 1
                last_leader = leader
                metrics.leader_history.append(leader)

        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        health = (await http.get("/api/admin/health", headers=headers)).json()

    return {
        "herding": herding, "delay": delay, "players": players, "seed": seed,
        "ticks": session.tick, "total_ticks": total,
        "metrics": metrics, "health": health, "session": session,
        "leader_changes": leader_changes,
    }


def analyze(session, metrics) -> dict:
    """DB 에 남은 것만으로 보고서 항목을 계산한다."""
    conn = session.conn
    sid = session.session_id
    scenario = session.scenario

    prices = {s.symbol: [s.initial_price] * (scenario.total_ticks + 1)
              for s in scenario.stocks}
    volume = {s.symbol: 0 for s in scenario.stocks}
    for row in conn.execute(
        """SELECT tick, symbol, price, buy_qty_filled, sell_qty_filled
           FROM tick_log WHERE session_id=? ORDER BY tick""", (sid,)
    ):
        prices[row["symbol"]][row["tick"]] = row["price"]
        volume[row["symbol"]] += row["buy_qty_filled"] + row["sell_qty_filled"]
    for symbol, series in prices.items():
        for t in range(1, len(series)):
            if series[t] == 0:
                series[t] = series[t - 1]

    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events_fired WHERE session_id=? ORDER BY headline_tick", (sid,)
    )]
    orders = [dict(r) for r in conn.execute(
        "SELECT * FROM orders WHERE session_id=? AND filled_qty>0", (sid,)
    )]

    shorts = []
    for e in events:
        if e["type"] != "short_pressure":
            continue
        sched = json.loads(e["schedule_json"])
        pump_start = e["impact_start_tick"]
        pump_end = pump_start + sched["pump_ticks"] - 1
        dump_end = e["impact_end_tick"]
        symbol = e["symbol"]
        series = prices[symbol]
        entered = {
            o["player_id"] for o in orders
            if o["symbol"] == symbol and o["side"] == "buy"
            and pump_start <= o["submit_tick"] <= pump_end
        }
        escaped = {
            o["player_id"] for o in orders
            if o["symbol"] == symbol and o["side"] == "sell"
            and o["player_id"] in entered
            and pump_end < o["submit_tick"] <= dump_end
        }
        shorts.append({
            "key": e["event_key"], "symbol": symbol, "headline": e["headline"],
            "headline_tick": e["headline_tick"],
            "pump_range": (pump_start, pump_end), "dump_range": (pump_end + 1, dump_end),
            "pump_realized": series[pump_end] / series[pump_start - 1] - 1,
            "dump_realized": series[dump_end] / series[pump_end] - 1,
            "pump_scheduled": sched["pump_total"], "dump_scheduled": sched["dump_total"],
            "entered": len(entered), "escaped": len(escaped),
        })

    impact_gaps = []
    for e in events:
        if not (e["headline"] or "").strip():
            continue
        gap_ticks = e["impact_start_tick"] - e["headline_tick"]
        impact_gaps.append({
            "key": e["event_key"], "headline": e["headline"],
            "headline_tick": e["headline_tick"],
            "impact_tick": e["impact_start_tick"],
            "gap_ticks": gap_ticks,
            "gap_seconds": gap_ticks * scenario.tick_seconds,
        })

    index_rows = list(conn.execute(
        "SELECT tick, fear_index, fx_rate FROM index_log WHERE session_id=? ORDER BY tick",
        (sid,)
    ))
    fear = [r["fear_index"] for r in index_rows]
    fx = [r["fx_rate"] for r in index_rows]

    def longest_flat(series, eps):
        longest = current = 0
        for a, b in zip(series, series[1:]):
            current = current + 1 if abs(b - a) < eps else 0
            longest = max(longest, current)
        return longest

    return {
        "stocks": [
            {
                "symbol": s.symbol, "name": s.name,
                "final": prices[s.symbol][-1], "initial": s.initial_price,
                "chg": prices[s.symbol][-1] / s.initial_price - 1,
                "volume": volume[s.symbol],
            }
            for s in scenario.stocks
        ],
        "shorts": shorts,
        "impact_gaps": impact_gaps,
        "fear": {
            "min": min(fear) if fear else 0, "max": max(fear) if fear else 0,
            "flat": longest_flat(fear, 0.15),
            "mean_abs_step": statistics.mean(
                abs(b - a) for a, b in zip(fear, fear[1:])
            ) if len(fear) > 1 else 0,
        },
        "fx": {
            "min": min(fx) if fx else 0, "max": max(fx) if fx else 0,
            "flat": longest_flat(fx, 0.4),
            "mean_abs_step": statistics.mean(
                abs(b - a) for a, b in zip(fx, fx[1:])
            ) if len(fx) > 1 else 0,
        },
        "fill_mismatch": conn.execute(
            """SELECT COALESCE(SUM(requested_qty - filled_qty), 0) miss,
                      COALESCE(SUM(requested_qty), 0) total
               FROM orders WHERE session_id=?""", (sid,)
        ).fetchone(),
    }


def pctile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def render(main_run, sweep_rows, args) -> str:
    m = main_run["metrics"]
    a = main_run["analysis"]
    h = main_run["health"]
    lines = [
        "# 리허설 결과",
        "",
        f"- 참가 봇 {main_run['players']}명, 군집 확률 {main_run['herding']}, "
        f"반응 지연 {main_run['delay']}틱",
        f"- 배속 {args.speed}x, 시드 {main_run['seed']}, "
        f"{main_run['ticks']}/{main_run['total_ticks']}틱 진행",
        "- 실제 서버에 HTTP 와 WebSocket 으로 붙어 서버·DB·체결 로직을 전부 통과시켰다",
        "",
        "## 공매도 집중 발동",
        "",
    ]
    if not a["shorts"]:
        lines.append("발동하지 않았다. 시나리오를 확인해야 한다.")
    else:
        lines += [
            "| 이벤트 | 종목 | 펌프 예정 | 펌프 실현 | 덤프 예정 | 덤프 실현 | 순효과 |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in a["shorts"]:
            net = (1 + s["pump_realized"]) * (1 + s["dump_realized"]) - 1
            lines.append(
                f"| {s['key']} | {s['symbol']} | +{s['pump_scheduled'] * 100:.1f}% | "
                f"{s['pump_realized'] * 100:+.1f}% | -{s['dump_scheduled'] * 100:.1f}% | "
                f"{s['dump_realized'] * 100:+.1f}% | {net * 100:+.1f}% |"
            )
        lines += ["", "### 함정이 실제로 작동했는가", "",
                  "| 이벤트 | 펌프 구간 진입 봇 | 덤프 전 탈출 | 탈출률 |", "|---|---|---|---|"]
        for s in a["shorts"]:
            ratio = s["escaped"] / s["entered"] if s["entered"] else 0
            lines.append(
                f"| {s['key']} | {s['entered']}명 | {s['escaped']}명 | {ratio * 100:.0f}% |"
            )
        lines.append("")
        lines.append(
            "탈출률이 높으면 함정이 무르고, 0%에 가까우면 너무 가혹하다는 뜻이다."
        )

    lines += [
        "",
        "## WebSocket 브로드캐스트 지연",
        "",
        "| 구분 | p50 | p95 | p99 | 최대 | 표본 |",
        "|---|---|---|---|---|---|",
        f"| 클라이언트 수신까지 | {pctile(m.latencies, 0.5):.1f}ms | "
        f"{pctile(m.latencies, 0.95):.1f}ms | {pctile(m.latencies, 0.99):.1f}ms | "
        f"{max(m.latencies) if m.latencies else 0:.1f}ms | {len(m.latencies)}개 |",
        f"| 서버 전송 소요 | {h['broadcast_latency_ms']['p50']:.1f}ms | "
        f"{h['broadcast_latency_ms']['p95']:.1f}ms | "
        f"{h['broadcast_latency_ms']['p99']:.1f}ms | - | "
        f"{h['broadcast_latency_ms']['count']}개 |",
        "",
        f"틱 처리 시간 평균 {h['tick_duration_ms']['mean']:.1f}ms, "
        f"최대 {h['tick_duration_ms']['max']:.1f}ms "
        f"(틱 예산 {main_run['session'].scenario.tick_seconds}초)",
        "",
        "## 헤드라인 노출과 실제 반영 간격",
        "",
        "| 이벤트 | 헤드라인 | 노출 틱 | 반영 시작 틱 | 간격 |",
        "|---|---|---|---|---|",
    ]
    for g in a["impact_gaps"]:
        lines.append(
            f"| {g['key']} | {g['headline'][:28]} | {g['headline_tick']} | "
            f"{g['impact_tick']} | {g['gap_ticks']}틱 ({g['gap_seconds']:.0f}초) |"
        )

    lines += [
        "",
        "## 최종 순위 변동",
        "",
        f"1위가 바뀐 횟수 {main_run['leader_changes']}회 "
        f"(관측 {len(m.leader_history)}회)",
        "",
        "1위가 계속 고정이면 중반부터 결과가 정해져 재미가 없다는 신호다.",
        "",
        "## 종목별 최종 등락률과 거래량",
        "",
        "| 종목 | 시작가 | 최종가 | 등락률 | 체결 수량 |",
        "|---|---|---|---|---|",
    ]
    for s in a["stocks"]:
        lines.append(
            f"| {s['name']} | {s['initial']:,} | {s['final']:,} | "
            f"{s['chg'] * 100:+.1f}% | {s['volume']:,}주 |"
        )
    lines += [
        "",
        "안정형 3종목(한별에너지, 늘봄유통, 파란로지스틱스)의 거래량이 유독 적으면",
        "참가자에게 지루했다는 뜻이다.",
        "",
        "## 공포지수와 환율 궤적",
        "",
        f"- 공포지수 {a['fear']['min']:.1f} ~ {a['fear']['max']:.1f}, "
        f"틱당 평균 변화 {a['fear']['mean_abs_step']:.3f}, "
        f"최장 정체 {a['fear']['flat']}틱",
        f"- 환율 {a['fx']['min']:.1f} ~ {a['fx']['max']:.1f}, "
        f"틱당 평균 변화 {a['fx']['mean_abs_step']:.3f}원, "
        f"최장 정체 {a['fx']['flat']}틱",
        "",
        "평평하면 3번 평균회귀 파라미터가 잘못된 것이다.",
        "",
        "## 주문과 체결",
        "",
        f"- 보낸 주문 {m.orders_sent}건, 거부 {m.orders_rejected}건",
    ]
    for code, count in m.reject_codes.most_common():
        lines.append(f"  - {code}: {count}건")
    miss = a["fill_mismatch"]
    if miss["total"]:
        lines.append(
            f"- 접수 수량 대비 미체결 {miss['miss']:,}주 / {miss['total']:,}주 "
            f"({miss['miss'] / miss['total'] * 100:.2f}%). "
            "f 계산에 쓴 접수 수량과 실제 체결 수량의 차이다"
        )

    lines += ["", "## 예외와 타임아웃", ""]
    server_exc = main_run["session"].exceptions
    if not server_exc and not m.errors and not m.timeouts:
        lines.append("발생하지 않았다.")
    else:
        if server_exc:
            lines.append(f"- 서버 예외 {len(server_exc)}건")
            lines += [f"  - {e}" for e in server_exc[:10]]
        if m.errors:
            lines.append(f"- 클라이언트 오류 {len(m.errors)}건")
            for e in list(dict.fromkeys(m.errors))[:10]:
                lines.append(f"  - {e}")
        if m.timeouts:
            lines.append(f"- 수신 타임아웃 {m.timeouts}건")

    if sweep_rows:
        lines += [
            "",
            "## 군집 확률과 반응 지연 스윕",
            "",
            "반응 지연이 길면 공매도 펌프 구간에 진입할 시간이 모자란다. 그 경계를 본다.",
            "",
            "| 군집 | 지연 | 펌프 진입 봇 | 덤프 전 탈출 | 주문 | 거부 | 지연 p95 |",
            "|---|---|---|---|---|---|---|",
        ]
        rows_data = []
        for row in sweep_rows:
            entered = sum(s["entered"] for s in row["analysis"]["shorts"])
            escaped = sum(s["escaped"] for s in row["analysis"]["shorts"])
            rows_data.append((row, entered, escaped))
            lines.append(
                f"| {row['herding']} | {row['delay']}틱 | {entered}명 | {escaped}명 | "
                f"{row['metrics'].orders_sent}건 | {row['metrics'].orders_rejected}건 | "
                f"{pctile(row['metrics'].latencies, 0.95):.1f}ms |"
            )

        lines += ["", "### 읽는 법", ""]

        # 반응 지연이 펌프 구간을 잡아먹는지
        pump_lengths = [
            s["pump_range"][1] - s["pump_range"][0] + 1
            for s in main_run["analysis"]["shorts"]
        ]
        if pump_lengths:
            shortest = min(pump_lengths)
            budget = shortest - 1          # 체결이 다음 틱이라 1틱을 더 쓴다
            lines.append(
                f"- 가장 짧은 펌프 구간이 {shortest}틱이고 주문은 다음 틱에 체결되므로, "
                f"반응 지연이 {budget}틱을 넘으면 펌프 안에 진입할 수 없다. "
                f"스윕한 지연 1틱과 4틱은 모두 이 한도 안이다."
            )
        by_delay = {}
        for row, entered, _ in rows_data:
            by_delay.setdefault(row["delay"], []).append(entered)
        if len(by_delay) > 1:
            summary = ", ".join(
                f"지연 {d}틱 평균 {statistics.mean(v):.1f}명"
                for d, v in sorted(by_delay.items())
            )
            lines.append(f"- 지연별 펌프 진입 봇 수: {summary}")

        by_herd = {}
        for row, entered, _ in rows_data:
            by_herd.setdefault(row["herding"], []).append(
                (entered, row["metrics"].orders_rejected)
            )
        if len(by_herd) > 1:
            parts = []
            for herd, vals in sorted(by_herd.items()):
                parts.append(
                    f"군집 {herd}: 진입 {statistics.mean(e for e, _ in vals):.1f}명, "
                    f"거부 {statistics.mean(r for _, r in vals):.0f}건"
                )
            lines.append(f"- {' / '.join(parts)}")
            lines.append(
                "- 군집 확률이 높을수록 펌프 진입이 오히려 줄고 거부가 급증한다. "
                "봇이 군중 표적에 먼저 비중 상한까지 채워서 정작 공매도 구간에 살 "
                "여력이 남지 않기 때문이다. 현장에서 한 종목 쏠림이 심하면 같은 일이 "
                "벌어질 수 있다."
            )

        total_entered = sum(e for _, e, _ in rows_data)
        total_escaped = sum(x for _, _, x in rows_data)
        if total_entered:
            lines.append(
                f"- 전체 스윕에서 펌프 진입 {total_entered}명 중 덤프 전 탈출 "
                f"{total_escaped}명({total_escaped / total_entered * 100:.0f}%). "
                "탈출률이 낮다는 것은 경고 배지를 보고도 미끼를 문 참가자가 거의 "
                "빠져나오지 못한다는 뜻이다. 교육 효과로는 의도한 바이지만, "
                "현장에서 너무 가혹하게 느껴지면 덤프 구간을 늘려 완만하게 만드는 것을 "
                "사람이 판단해야 한다."
            )
        lines.append(
            "- 봇은 화면의 '최대 N주' 안내를 보지 않고 주문하므로 거부 건수가 실제보다 "
            "많이 잡힌다. 참가자 화면에는 한도가 표시된다."
        )

    lines += [
        "",
        "---",
        "",
        "이 수치는 봇 리허설 결과다. 실제 참가자는 봇과 다르게 움직이므로",
        "확정된 값으로 읽으면 안 된다. 최종 판단은 사람이 직접 해 보고 내려야 한다.",
        "",
    ]
    return "\n".join(lines)


async def main_async(args) -> int:
    config = uvicorn.Config(app, host=HOST, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    try:
        print(f"본 리허설: 봇 {args.players}명, 군집 {args.herding}, "
              f"반응 지연 {args.delay}틱, 배속 {args.speed}x")
        main_run = await run_once(
            args.port, args.players, args.herding, args.delay, args.speed,
            args.seed, args.scenario,
        )
        main_run["analysis"] = analyze(main_run["session"], main_run["metrics"])
        print(f"  완주 {main_run['ticks']}/{main_run['total_ticks']}틱, "
              f"주문 {main_run['metrics'].orders_sent}건")

        sweep_rows = []
        if not args.no_sweep:
            combos = [(h, d) for h in (0.0, 0.4, 0.8) for d in (1, 4)]
            for i, (herd, delay) in enumerate(combos, start=1):
                print(f"스윕 {i}/{len(combos)}: 군집 {herd}, 지연 {delay}틱")
                row = await run_once(
                    args.port, args.sweep_players, herd, delay, args.sweep_speed,
                    args.seed + 1000 + i, args.scenario,
                )
                row["analysis"] = analyze(row["session"], row["metrics"])
                sweep_rows.append(row)

        REPORT_PATH.write_text(render(main_run, sweep_rows, args), encoding="utf-8")
        print(f"\n보고서: {REPORT_PATH}")

        failed = bool(main_run["session"].exceptions) or bool(main_run["metrics"].errors)
        if main_run["ticks"] != main_run["total_ticks"]:
            failed = True
            print("완주하지 못했다")
        if failed:
            print("예외나 오류가 있었다. 보고서를 확인해라")
        return 1 if failed else 0
    finally:
        await server.shutdown()
        task.cancel()


def main() -> int:
    parser = argparse.ArgumentParser(description="리허설 하네스")
    parser.add_argument("players", nargs="?", type=int, default=50)
    parser.add_argument("--scenario", default="festival_01.json")
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--speed", type=float, default=30.0)
    parser.add_argument("--herding", type=float, default=0.5)
    parser.add_argument("--delay", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--sweep-players", type=int, default=20)
    parser.add_argument("--sweep-speed", type=float, default=60.0)
    parser.add_argument("--no-sweep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
