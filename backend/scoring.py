"""점수 계산 — 수익률, 위험조정점수, MDD, 3종 순위.

위험조정점수는 틱별 수익률의 평균/표준편차다. 게임 중에는 DB 에 틱별 자산을 남기지
않고(결정-11) Welford 온라인 누적으로만 들고 있다가, 사후에 tick_log 와 orders 로
재구성한 값과 대조한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 참여 틱이 이보다 적으면 위험조정·종합 순위에서 집계 제외한다(결정-09).
# 표본이 적으면 표준편차가 우연히 작게 나와 순위가 뒤집힌다.
DEFAULT_MIN_TICKS_FOR_RISK = 60


@dataclass
class ReturnStats:
    """틱별 수익률의 평균과 표준편차를 온라인으로 누적한다(Welford)."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def push(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

    @property
    def stdev(self) -> float:
        if self.count < 2:
            return 0.0
        return math.sqrt(self.m2 / self.count)

    @property
    def risk_adjusted(self) -> float:
        """평균/표준편차. 표준편차가 0 이면 0점(스펙 11번)."""
        sd = self.stdev
        if sd == 0.0:
            return 0.0
        return self.mean / sd


@dataclass
class PlayerScore:
    player_id: int
    nickname: str
    join_tick: int
    ticks_played: int
    starting_cash: int
    final_asset: int
    return_pct: float
    risk_adjusted: float
    mdd: float
    eligible_for_risk: bool
    rank_return: int = 0
    rank_risk: int = 0
    rank_total: int = 0


@dataclass
class PlayerTracker:
    """한 참가자의 틱별 자산 흐름을 게임 중 메모리에서만 들고 있는다."""

    player_id: int
    nickname: str
    join_tick: int
    starting_cash: int
    prev_asset: int
    peak_asset: int
    stats: ReturnStats = field(default_factory=ReturnStats)
    mdd: float = 0.0

    def push_asset(self, asset: int) -> None:
        if self.prev_asset > 0:
            self.stats.push(asset / self.prev_asset - 1.0)
        self.prev_asset = asset
        if asset > self.peak_asset:
            self.peak_asset = asset
        elif self.peak_asset > 0:
            drawdown = 1.0 - asset / self.peak_asset
            self.mdd = max(self.mdd, drawdown)

    def score(self, min_ticks: int = DEFAULT_MIN_TICKS_FOR_RISK) -> PlayerScore:
        return PlayerScore(
            player_id=self.player_id,
            nickname=self.nickname,
            join_tick=self.join_tick,
            ticks_played=self.stats.count,
            starting_cash=self.starting_cash,
            final_asset=self.prev_asset,
            return_pct=self.prev_asset / self.starting_cash - 1.0,
            risk_adjusted=self.stats.risk_adjusted,
            mdd=self.mdd,
            eligible_for_risk=self.stats.count >= min_ticks,
        )


def assign_ranks(scores: list[PlayerScore]) -> list[PlayerScore]:
    """수익률 / 위험조정 / 종합 3종 순위를 매긴다.

    종합은 두 순위의 평균이고, 동점이면 MDD 가 낮은 쪽이 앞선다(스펙 11번).
    참여 틱이 모자란 참가자는 위험조정·종합에서 빠지고 순위 0 으로 남는다.
    """
    if not scores:
        return []

    by_return = sorted(scores, key=lambda s: (-s.return_pct, s.mdd, s.player_id))
    for i, s in enumerate(by_return, start=1):
        s.rank_return = i

    eligible = [s for s in scores if s.eligible_for_risk]
    by_risk = sorted(eligible, key=lambda s: (-s.risk_adjusted, s.mdd, s.player_id))
    for i, s in enumerate(by_risk, start=1):
        s.rank_risk = i
    for s in scores:
        if not s.eligible_for_risk:
            s.rank_risk = 0

    by_total = sorted(
        eligible,
        key=lambda s: ((s.rank_return + s.rank_risk) / 2, s.mdd, s.player_id),
    )
    for i, s in enumerate(by_total, start=1):
        s.rank_total = i
    for s in scores:
        if not s.eligible_for_risk:
            s.rank_total = 0

    return scores


def leaderboard(scores: list[PlayerScore], tab: str, limit: int = 0) -> list[PlayerScore]:
    """순위 탭 하나를 뽑는다. tab 은 return | risk | total."""
    if tab == "return":
        rows = sorted(scores, key=lambda s: s.rank_return)
    elif tab == "risk":
        rows = [s for s in scores if s.eligible_for_risk]
        rows.sort(key=lambda s: s.rank_risk)
    elif tab == "total":
        rows = [s for s in scores if s.eligible_for_risk]
        rows.sort(key=lambda s: s.rank_total)
    else:
        raise ValueError(f"모르는 순위 탭이다: {tab}")
    return rows[:limit] if limit else rows


def portfolio_value(cash: int, reserved_cash: int, holdings, prices) -> int:
    """현금(예약분 포함) + 보유 종목 평가액."""
    value = 0
    for symbol, qty in holdings.items():
        if qty:
            value += qty * prices[symbol]
    return cash + reserved_cash + value
