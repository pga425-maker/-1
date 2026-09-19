"""도메인 모델 — 시나리오 파일이 파싱된 뒤의 불변 자료구조.

API 요청/응답용 Pydantic 모델은 main.py 구현 단계에서 별도로 추가한다.
여기 있는 것은 엔진·스케줄러·튜닝·자가 검증이 공유하는 순수 자료형이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.index_engine import Segment

# 매수 근거 6종. 스펙 10번. 매도에는 근거를 요구하지 않는다.
BUY_REASONS = ("실적성장", "저평가", "차트추세", "분산", "뉴스", "직감")

# 뉴스 난이도 3종. 스펙 4번.
DIFFICULTIES = ("direct", "indirect", "counterexample")

# 종목 서사 유형. 스펙 6번.
NARRATIVES = ("supercycle", "binary", "stable", "meme", "vision")


@dataclass(frozen=True)
class DriftSegment:
    """종목별 시나리오 드리프트 구간. drift 는 '틱당' 목표 변동률이다."""

    from_tick: int
    to_tick: int
    drift: float


@dataclass(frozen=True)
class StockSpec:
    symbol: str
    name: str
    sector: str
    initial_price: int
    liquidity: int
    beta: float
    fx_exposure: float
    narrative: str
    avatar_color: str
    short_pressure_weight: float = 0.0
    noise_sigma: float | None = None      # 결정-07. None 이면 params.noise_sigma
    drift_segments: tuple[DriftSegment, ...] = ()


@dataclass(frozen=True)
class IndexSpec:
    initial: float
    theta: float
    noise_sigma: float
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class PhaseSpec:
    """공매도 집중 이벤트의 한 단계(펌프 또는 덤프) 정의.

    min/max 는 '구간 전체 누적 총합'이다(결정-01). 틱당 값으로 읽으면
    8틱 × 0.08 = 누적 +64% 가 되어 서사와 맞지 않는다.
    """

    ticks: int
    min_total: float
    max_total: float


@dataclass(frozen=True)
class NormalEventSpec:
    key: str
    tick: int
    delta: float
    impact_delay_ticks: int
    spread_ticks: int                     # 결정-02. 없으면 한 틱 폭락이 된다
    difficulty: str
    headline: str
    body: str = ""
    sector: str | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class ShortPressureSpec:
    key: str
    tick: int
    symbol: str
    impact_delay_ticks: int
    pump: PhaseSpec
    dump: PhaseSpec
    headline: str
    body: str = "공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다"
    badge: str = "공매도 집중 과열 · 급등 후 급락 위험"


@dataclass(frozen=True)
class Params:
    # 가격 엔진
    resistance_coef: float = 0.3
    dampening_cap: float = 0.7
    noise_sigma: float = 0.004
    fear_sensitivity: float = 1.0         # 결정-06
    fx_sensitivity: float = 1.0           # 결정-06
    min_price: int = 100
    # 공매도 집중 불변 조건
    net_loss_margin: float = 0.15         # 덤프 총합 >= 펌프 총합 * (1 + margin)
    min_visible_pump: float = 1.05        # 펌프 구간 최고/시작 배율 하한
    # 게임 규칙
    fee_rate: float = 0.00015             # 0.015% 양방향
    max_position_ratio: float = 0.5       # 한 종목 총자산 50%
    # 표시·집계
    snapshot_interval_ticks: int = 8      # 지수 표시 변화분·순위 갱신 주기
    min_ticks_for_risk_rank: int = 60     # 결정-09


@dataclass(frozen=True)
class Scenario:
    name: str
    source_file: str
    sha256: str
    tick_seconds: float
    total_ticks: int
    starting_cash: int
    params: Params
    fear_index: IndexSpec
    fx_rate: IndexSpec
    stocks: tuple[StockSpec, ...]
    normal_events: tuple[NormalEventSpec, ...] = ()
    short_events: tuple[ShortPressureSpec, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    def stock(self, symbol: str) -> StockSpec:
        for s in self.stocks:
            if s.symbol == symbol:
                return s
        raise KeyError(f"없는 종목이다: {symbol}")

    def symbols_in_sector(self, sector: str) -> tuple[str, ...]:
        return tuple(s.symbol for s in self.stocks if s.sector == sector)

    def noise_sigma_for(self, symbol: str) -> float:
        """종목별 노이즈. 미지정이면 전역값을 쓴다(결정-07)."""
        override = self.stock(symbol).noise_sigma
        return self.params.noise_sigma if override is None else override

    @property
    def all_event_ticks(self) -> tuple[int, ...]:
        ticks = [e.tick for e in self.normal_events] + [e.tick for e in self.short_events]
        return tuple(sorted(ticks))
