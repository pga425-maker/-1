"""파라미터 자동 튜닝 — 서버도 UI도 DB도 없이 엔진만 부르는 headless 시뮬레이션.

9번 리허설 하네스와 목적이 다르다. 리허설은 실제로 떠 있는 서버에 HTTP/WebSocket
으로 붙어 체결까지 통과시키지만, 여기서는 price_engine / index_engine /
event_scheduler 만 불러 계산한다. 그래서 수백 회를 빠르게 돌릴 수 있다.

실행:
    python3 -m backend.simulate_tuning                 4x4 1차 그리드
    python3 -m backend.simulate_tuning --full          0.1~0.7 x 0.5~0.8 전수
    python3 -m backend.simulate_tuning --runs 10       조합당 회차 수 변경
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from dataclasses import dataclass, field

from backend.bots import STRATEGIES, Bot, MarketView, Order, build_bots
from backend.event_scheduler import EventScheduler
from backend.index_engine import build_target_series, change_pct, step, step_fear
from backend.models import Scenario
from backend.price_engine import TickInput, compute_tick
from backend.scenario_loader import load_scenario

DEFAULT_SCENARIO = "scenarios/festival_01.json"

# 1차 그리드.
#
# 스펙 8번 원안은 resistance_coef {0.1,0.3,0.5,0.7} x dampening_cap {0.5,0.6,0.7,0.8}
# 였다. 그런데 damp = min(r*|f|, cap) 이고 |f| <= 1 이므로 r*|f| 의 최대가 0.7 이다.
# cap 이 0.5~0.8 이면 cap 축이 거의 작동하지 않는다(cap=0.8 은 수식상 절대 작동 안 함).
# 실측에서도 같은 r 행의 네 칸 값이 소수점까지 같았다.
# 원인을 더 파 보니 r 범위가 아니라 유동성 L 이 문제였다. damp = min(r*|f|, cap) 에서
# r 과 깊이는 비율로만 작용하는데, 스펙의 대형주 깊이(4~5억)가 참가자 주문 규모보다
# 100배 커서 |f| 가 0.01 대에 묶여 있었다. 깊이를 800만~2000만 원 대로 맞추니
# 스펙 원래 범위에서 cap 축까지 정상 작동한다. 그래서 원안 그대로 되돌렸다.
COARSE_RESISTANCE = (0.1, 0.3, 0.5, 0.7)
COARSE_CAP = (0.5, 0.6, 0.7, 0.8)

# 탈락 기준.
# 스펙 원안 0.3 은 도달할 수 없었다. r=5.0(스펙 최대의 7배)에서도 0.896 이었다.
# 제안-B 로 0.85 로 조정했다. 이벤트가 15% 넘게 깎이면 뉴스를 읽고 판단하는 학습이
# 흐려진다는 기준이다.
MIN_EVENT_PRESERVATION = 0.85
DIVERGENCE_UP_RATIO = 20.0

# 군집 행동이 표적을 찾을 때 참고하는 최근 틱 수 (약 14초)
CROWD_WINDOW = 3

# 군집 확률 축. 0.0 은 스펙 8번 원안(독립 봇)이다.
HERDING_LEVELS = (0.0, 0.4, 0.8)


@dataclass
class Prepared:
    """시나리오마다 한 번만 만들면 되는 것들. 회차마다 다시 만들면 느리다."""

    scenario: Scenario
    drift: dict[str, list[float]]
    fear_targets: list[float]
    fx_targets: list[float]
    sector_symbols: dict[str, tuple[str, ...]]


def prepare(scenario: Scenario) -> Prepared:
    drift: dict[str, list[float]] = {}
    for stock in scenario.stocks:
        arr = [0.0] * (scenario.total_ticks + 1)
        for seg in stock.drift_segments:
            for tick in range(seg.from_tick + 1, seg.to_tick + 1):
                arr[tick] = seg.drift
        drift[stock.symbol] = arr
    return Prepared(
        scenario=scenario,
        drift=drift,
        fear_targets=build_target_series(
            list(scenario.fear_index.segments), scenario.total_ticks,
            scenario.fear_index.initial,
        ),
        fx_targets=build_target_series(
            list(scenario.fx_rate.segments), scenario.total_ticks,
            scenario.fx_rate.initial,
        ),
        sector_symbols={
            s.sector: scenario.symbols_in_sector(s.sector) for s in scenario.stocks
        },
    )


@dataclass
class RoundResult:
    seed: int
    resistance_coef: float
    dampening_cap: float
    bot_returns: dict[int, float]
    strategy_returns: dict[str, float]
    event_preservation: float
    divergence_flag: bool
    final_prices: dict[str, int]
    price_returns: dict[str, float]
    tick_volatility: dict[str, float]
    peak_returns: dict[str, float]
    fear_path: list[float] = field(default_factory=list)
    fx_path: list[float] = field(default_factory=list)
    leader_changes_second_half: int = 0
    ruined_bot_ratio: float = 0.0
    flat_bot_ratio: float = 0.0
    damp_samples: list[float] = field(default_factory=list)
    fill_mismatch_ratio: float = 0.0
    price_paths: dict[str, list[int]] = field(default_factory=dict)

    @property
    def strategy_spread(self) -> float:
        """5개 봇 전략 최종 수익률의 표준편차.

        스펙 8번의 influence_index 원안이다. 다만 이 값은 참가자가 시장을 움직이는
        정도가 아니라 전략끼리 성적이 갈리는 정도를 잰다. 저항 계수에 거의 반응하지
        않는다는 것이 1차 튜닝에서 확인됐다. 그래서 이름을 바꿔 남겨 두고,
        참가자 영향력은 market_impact 로 따로 잰다(제안-C).
        """
        return statistics.pstdev(list(self.strategy_returns.values()))

    @property
    def influence_index(self) -> float:
        return self.strategy_spread


def run_round(
    prepared: Prepared,
    seed: int,
    resistance_coef: float,
    dampening_cap: float,
    bot_count: int = 40,
    herding_prob: float = 0.0,
) -> RoundResult:
    sc = prepared.scenario
    params = sc.params
    rng = random.Random(seed)
    scheduler = EventScheduler(sc, random.Random(seed))
    bots = build_bots(bot_count, sc.starting_cash, seed, herding_prob)
    by_id = {b.bot_id: b for b in bots}

    symbols = tuple(s.symbol for s in sc.stocks)
    stock_by_symbol = {s.symbol: s for s in sc.stocks}
    prices = {s.symbol: s.initial_price for s in sc.stocks}
    history = {s.symbol: [s.initial_price] for s in sc.stocks}
    peak = {s.symbol: float(s.initial_price) for s in sc.stocks}
    tick_returns = {s.symbol: [] for s in sc.stocks}

    fear = [sc.fear_index.initial]
    fx = [sc.fx_rate.initial]

    pending: list[Order] = []
    queued: list[Order] = []
    preservation_samples: list[float] = []
    damp_samples: list[float] = []
    divergence = False
    submitted_total = 0
    filled_total = 0

    # 헤드라인이 가리킨 종목을 봇에게 알려주기 위한 기록
    last_headline_tick = -999
    last_headline_symbols: tuple[str, ...] = ()

    leader_history: list[int] = []

    # 최근 CROWD_WINDOW 틱 동안 매수 금액이 가장 몰린 종목. 군집 행동의 표적이 된다.
    crowd_window: list[tuple[int, str, int]] = []
    crowd_favorite: str | None = None

    for tick in range(1, sc.total_ticks + 1):
        pending, queued = queued, []

        fear.append(step_fear(
            fear[-1], prepared.fear_targets[tick], sc.fear_index.theta,
            rng.gauss(0, sc.fear_index.noise_sigma),
        ))
        fx.append(step(
            fx[-1], prepared.fx_targets[tick], sc.fx_rate.theta,
            rng.gauss(0, sc.fx_rate.noise_sigma),
        ))
        fear_change = fear[-1] - fear[-2]
        fx_change = change_pct(fx[-2], fx[-1])

        submitted_buy = {sym: 0 for sym in symbols}
        submitted_sell = {sym: 0 for sym in symbols}
        for order in pending:
            if order.side == "buy":
                submitted_buy[order.symbol] += order.qty
            else:
                submitted_sell[order.symbol] += order.qty

        for symbol in symbols:
            stock = stock_by_symbol[symbol]
            event_delta = scheduler.delta_for(tick, symbol)
            result = compute_tick(TickInput(
                symbol=symbol, prev_price=prices[symbol], liquidity=stock.liquidity,
                beta=stock.beta, fx_exposure=stock.fx_exposure,
                scenario_drift=prepared.drift[symbol][tick],
                noise=rng.gauss(0, sc.noise_sigma_for(symbol)),
                event_delta=event_delta, fear_change=fear_change,
                fx_change_pct=fx_change,
                buy_qty=submitted_buy[symbol], sell_qty=submitted_sell[symbol],
                resistance_coef=resistance_coef, dampening_cap=dampening_cap,
                fear_sensitivity=params.fear_sensitivity,
                fx_sensitivity=params.fx_sensitivity, min_price=params.min_price,
            ))
            prices[symbol] = result.price
            history[symbol].append(result.price)
            tick_returns[symbol].append(result.actual_delta)
            peak[symbol] = max(peak[symbol], result.price)
            if result.f != 0.0:
                damp_samples.append(result.damp)

            # 이벤트 보존율: 이벤트가 살아 있는 틱에서만 잰다.
            if event_delta != 0.0 and abs(result.total_script_delta) > 1e-9:
                preservation_samples.append(
                    abs(result.actual_delta) / abs(result.total_script_delta)
                )

            if result.price >= stock.initial_price * DIVERGENCE_UP_RATIO:
                divergence = True
            if result.price <= params.min_price:
                divergence = True

        # 체결. 가격이 확정된 뒤에야 체결할 수 있다.
        for order in pending:
            filled = _fill(by_id[order.bot_id], order, prices, params)
            submitted_total += order.qty
            filled_total += filled

        # 봇의 판단. 지금 본 가격으로 낸 주문은 다음 틱 가격에 체결된다.
        headlines = scheduler.headlines_at(tick)
        if headlines:
            last_headline_tick = tick
            last_headline_symbols = tuple(
                sym for e in headlines for sym in e.targets
            )
        view = MarketView(
            tick=tick, prices=prices, history=history, symbols=symbols,
            fresh_headline_symbols=last_headline_symbols,
            ticks_since_headline=tick - last_headline_tick,
            crowd_favorite=crowd_favorite,
        )
        for bot in bots:
            order = bot.decide(view, params.max_position_ratio)
            if order is None:
                continue
            if _reserve(bot, order, prices, params):
                queued.append(order)
                if order.side == "buy":
                    crowd_window.append(
                        (tick, order.symbol, order.qty * order.quoted_price)
                    )

        crowd_window = [c for c in crowd_window if c[0] > tick - CROWD_WINDOW]
        if crowd_window:
            totals: dict[str, int] = {}
            for _, symbol, value in crowd_window:
                totals[symbol] = totals.get(symbol, 0) + value
            crowd_favorite = max(totals, key=totals.__getitem__)
        else:
            crowd_favorite = None

        if bots:
            leader_history.append(
                max((b.total_asset(prices), b.bot_id) for b in bots)[1]
            )

    final_returns = {
        b.bot_id: b.total_asset(prices) / b.starting_cash - 1.0 for b in bots
    }
    strategy_returns = {
        strategy: statistics.mean(
            [final_returns[b.bot_id] for b in bots if b.strategy == strategy] or [0.0]
        )
        for strategy in STRATEGIES
    }

    half = len(leader_history) // 2
    changes = sum(
        1 for a, b in zip(leader_history[half:], leader_history[half + 1:]) if a != b
    )
    returns_list = list(final_returns.values()) or [0.0]

    return RoundResult(
        seed=seed, resistance_coef=resistance_coef, dampening_cap=dampening_cap,
        bot_returns=final_returns, strategy_returns=strategy_returns,
        event_preservation=(
            statistics.mean(preservation_samples) if preservation_samples else 0.0
        ),
        divergence_flag=divergence,
        final_prices=dict(prices),
        price_returns={
            s: prices[s] / stock_by_symbol[s].initial_price - 1.0 for s in symbols
        },
        tick_volatility={s: statistics.pstdev(tick_returns[s]) for s in symbols},
        peak_returns={
            s: peak[s] / stock_by_symbol[s].initial_price - 1.0 for s in symbols
        },
        fear_path=fear, fx_path=fx, price_paths={s: list(history[s]) for s in symbols},
        leader_changes_second_half=changes,
        ruined_bot_ratio=sum(1 for r in returns_list if r < -0.80) / len(returns_list),
        flat_bot_ratio=sum(1 for r in returns_list if abs(r) <= 0.05) / len(returns_list),
        damp_samples=damp_samples,
        fill_mismatch_ratio=(
            (submitted_total - filled_total) / submitted_total
            if submitted_total else 0.0
        ),
    )


def _reserve(bot: Bot, order: Order, prices: dict[str, int], params) -> bool:
    """접수 시 현금/수량을 잡아 둔다.

    같은 틱에 주문을 여러 번 넣어 현금을 초과 사용하는 것을 막는다(결정-03).
    예약 기준가는 봇이 방금 본 가격이다. 참가자 화면의 '예상 금액'과 같은 값이다.
    """
    if order.qty <= 0:
        return False
    if order.side == "buy":
        order.quoted_price = prices[order.symbol]
        need = int(math.ceil(order.quoted_price * order.qty * (1.0 + params.fee_rate)))
        if need > bot.cash:
            return False
        bot.cash -= need
        bot.reserved_cash += need
        order.reserved = need
        return True

    if order.qty > bot.free_qty(order.symbol):
        return False
    order.quoted_price = prices[order.symbol]
    bot.reserved_qty[order.symbol] = bot.reserved_qty.get(order.symbol, 0) + order.qty
    return True


def _fill(bot: Bot, order: Order, prices: dict[str, int], params) -> int:
    """확정된 틱 가격으로 체결한다.

    참가자가 본 예상 금액은 직전 틱 가격 기준이라 가격이 오르면 현금이 모자랄 수
    있다. 그때는 거부하지 않고 가능한 수량만 채운다(부분체결, 결정-03).
    실제 체결 수량을 돌려준다. f 계산에 쓴 접수 수량과의 차이를 재는 데 쓴다.
    """
    price = prices[order.symbol]

    if order.side == "buy":
        bot.reserved_cash -= order.reserved
        bot.cash += order.reserved
        order.reserved = 0

        unit = price * (1.0 + params.fee_rate)
        by_cash = int(bot.cash // unit) if unit > 0 else 0
        # 비중 상한은 체결 시점에 다시 본다(결정-04).
        total = bot.total_asset(prices)
        room_value = int(total * params.max_position_ratio) - \
            bot.holdings.get(order.symbol, 0) * price
        by_room = max(0, int(room_value // price))
        qty = min(order.qty, by_cash, by_room)
        if qty <= 0:
            return 0
        gross = price * qty
        fee = int(round(gross * params.fee_rate))
        bot.cash -= gross + fee
        prev_qty = bot.holdings.get(order.symbol, 0)
        prev_cost = bot.avg_cost.get(order.symbol, 0.0) * prev_qty
        bot.holdings[order.symbol] = prev_qty + qty
        bot.avg_cost[order.symbol] = (prev_cost + gross + fee) / (prev_qty + qty)
        return qty

    reserved = bot.reserved_qty.get(order.symbol, 0)
    bot.reserved_qty[order.symbol] = max(0, reserved - order.qty)
    qty = min(order.qty, bot.holdings.get(order.symbol, 0))
    if qty <= 0:
        return 0
    gross = price * qty
    fee = int(round(gross * params.fee_rate))
    bot.cash += gross - fee
    bot.holdings[order.symbol] -= qty
    if bot.holdings[order.symbol] == 0:
        bot.holdings.pop(order.symbol)
        bot.avg_cost.pop(order.symbol, None)
    return qty


# ---------------------------------------------------------------- 그리드 서치

_COUNTERFACTUAL: dict[tuple[str, int], dict[str, list[int]]] = {}


def counterfactual_paths(prepared: Prepared, seed: int) -> dict[str, list[int]]:
    """봇이 하나도 없을 때의 가격 경로.

    봇이 없으면 f 가 항상 0 이라 damp 도 0 이다. 따라서 이 경로는 resistance_coef 와
    dampening_cap 값과 무관하다. 시드마다 한 번만 계산해 캐시한다.
    """
    key = (prepared.scenario.sha256, seed)
    if key not in _COUNTERFACTUAL:
        _COUNTERFACTUAL[key] = run_round(prepared, seed, 0.0, 0.5, bot_count=0).price_paths
    return _COUNTERFACTUAL[key]


def market_impact(
    result: RoundResult, baseline: dict[str, list[int]]
) -> tuple[float, float]:
    """참가자가 시장을 실제로 얼마나 움직였는지(제안-C).

    같은 시드에서 봇이 있는 경로와 없는 경로의 가격 차이를 틱마다 재어 평균한다.
    스펙 원안의 influence_index 와 달리 저항 파라미터에 직접 반응한다.

    평균과 피크를 함께 돌려준다. 라운드의 대부분은 조용한 틱이라 평균은 필연적으로
    작다. 참가자가 "내가 시장을 움직였다"고 느끼는 것은 몰릴 때의 피크다.
    """
    diffs = []
    peak = 0.0
    for symbol, path in result.price_paths.items():
        base = baseline[symbol]
        for actual, expected in zip(path, base):
            if expected > 0:
                d = abs(actual - expected) / expected
                diffs.append(d)
                peak = max(peak, d)
    return (statistics.mean(diffs) if diffs else 0.0), peak


@dataclass
class GridCell:
    resistance_coef: float
    dampening_cap: float
    herding_prob: float
    runs: int
    event_preservation: float
    event_preservation_sd: float
    strategy_spread: float
    strategy_spread_sd: float
    market_impact: float
    market_impact_sd: float
    peak_impact: float
    damp_p90: float
    divergence_runs: int
    mean_damp: float
    cap_bind_ratio: float
    fill_mismatch: float
    strategy_returns: dict[str, float]
    leader_changes: float
    ruined_ratio: float
    flat_ratio: float

    @property
    def influence_index(self) -> float:
        """파레토 판정에 쓰는 영향력 지표. 제안-C 승인으로 시장 영향도를 쓴다."""
        return self.market_impact

    @property
    def rejected(self) -> bool:
        return (
            self.event_preservation < MIN_EVENT_PRESERVATION
            or self.divergence_runs > 0
        )

    @property
    def reject_reason(self) -> str:
        reasons = []
        if self.event_preservation < MIN_EVENT_PRESERVATION:
            reasons.append(f"이벤트 보존 {self.event_preservation:.3f} < {MIN_EVENT_PRESERVATION}")
        if self.divergence_runs > 0:
            reasons.append(f"발산 {self.divergence_runs}회")
        return ", ".join(reasons)


def run_cell(
    prepared: Prepared, resistance_coef: float, dampening_cap: float,
    runs: int, bot_count: int, base_seed: int, herding_prob: float = 0.0,
) -> GridCell:
    results = [
        run_round(prepared, base_seed + i, resistance_coef, dampening_cap,
                  bot_count, herding_prob)
        for i in range(runs)
    ]
    preservation = [r.event_preservation for r in results]
    spreads = [r.strategy_spread for r in results]
    impact_pairs = [
        market_impact(r, counterfactual_paths(prepared, r.seed)) for r in results
    ]
    impacts = [a for a, _ in impact_pairs]
    peaks = [b for _, b in impact_pairs]
    damps = [d for r in results for d in r.damp_samples]
    strategies = {
        s: statistics.mean(r.strategy_returns[s] for r in results) for s in STRATEGIES
    }
    return GridCell(
        resistance_coef=resistance_coef, dampening_cap=dampening_cap,
        herding_prob=herding_prob, runs=runs,
        event_preservation=statistics.mean(preservation),
        event_preservation_sd=statistics.pstdev(preservation),
        strategy_spread=statistics.mean(spreads),
        strategy_spread_sd=statistics.pstdev(spreads),
        market_impact=statistics.mean(impacts),
        market_impact_sd=statistics.pstdev(impacts),
        peak_impact=statistics.mean(peaks),
        damp_p90=(
            sorted(damps)[int(len(damps) * 0.9)] if len(damps) > 10 else 0.0
        ),
        divergence_runs=sum(1 for r in results if r.divergence_flag),
        mean_damp=statistics.mean(damps) if damps else 0.0,
        cap_bind_ratio=(
            sum(1 for d in damps if d >= dampening_cap - 1e-9) / len(damps)
            if damps else 0.0
        ),
        fill_mismatch=statistics.mean(r.fill_mismatch_ratio for r in results),
        strategy_returns=strategies,
        leader_changes=statistics.mean(r.leader_changes_second_half for r in results),
        ruined_ratio=statistics.mean(r.ruined_bot_ratio for r in results),
        flat_ratio=statistics.mean(r.flat_bot_ratio for r in results),
    )


def pareto_front(cells: list[GridCell]) -> list[GridCell]:
    """이벤트 보존과 참가자 영향력은 트레이드오프다. 지배되지 않는 조합만 남긴다."""
    alive = [c for c in cells if not c.rejected]
    front = []
    for cell in alive:
        dominated = any(
            other is not cell
            and other.event_preservation >= cell.event_preservation
            and other.influence_index >= cell.influence_index
            and (
                other.event_preservation > cell.event_preservation
                or other.influence_index > cell.influence_index
            )
            for other in alive
        )
        if not dominated:
            front.append(cell)
    return sorted(front, key=lambda c: -c.event_preservation)


def describe(cell: GridCell) -> str:
    """이 조합이 어떤 성격의 게임이 되는지 한 줄로."""
    if cell.event_preservation >= 0.85:
        flavor = "시나리오가 거의 그대로 실현된다. 뉴스를 읽는 사람이 유리하다"
    elif cell.event_preservation >= 0.7:
        flavor = "시나리오가 주도하되 참가자가 흐름을 늦출 수 있다"
    elif cell.event_preservation >= 0.5:
        flavor = "시나리오와 참가자가 반반 섞인다"
    else:
        flavor = "참가자 쏠림이 시나리오를 크게 눌러 뉴스 효과가 흐려진다"
    if cell.peak_impact >= 0.05:
        impact = "참가자 쏠림이 가격에 뚜렷이 보인다"
    elif cell.peak_impact >= 0.015:
        impact = "참가자 쏠림이 가격에 감지될 정도로 나타난다"
    else:
        impact = "참가자가 가격을 움직였다는 느낌을 받기 어렵다"
    return f"{flavor}. {impact}"


def write_csv(cells: list[GridCell], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "resistance_coef", "dampening_cap", "herding_prob", "runs",
            "event_preservation", "event_preservation_sd",
            "market_impact", "market_impact_sd", "peak_impact", "damp_p90",
            "strategy_spread", "strategy_spread_sd",
            "divergence_runs", "rejected", "reject_reason",
            "mean_damp", "cap_bind_ratio", "fill_mismatch",
            "leader_changes_2nd_half", "ruined_ratio", "flat_ratio",
            *[f"ret_{s}" for s in STRATEGIES],
        ])
        for c in cells:
            writer.writerow([
                c.resistance_coef, c.dampening_cap, c.herding_prob, c.runs,
                f"{c.event_preservation:.4f}", f"{c.event_preservation_sd:.4f}",
                f"{c.market_impact:.5f}", f"{c.market_impact_sd:.5f}",
                f"{c.peak_impact:.5f}", f"{c.damp_p90:.4f}",
                f"{c.strategy_spread:.4f}", f"{c.strategy_spread_sd:.4f}",
                c.divergence_runs, int(c.rejected), c.reject_reason,
                f"{c.mean_damp:.4f}", f"{c.cap_bind_ratio:.4f}",
                f"{c.fill_mismatch:.4f}",
                f"{c.leader_changes:.2f}", f"{c.ruined_ratio:.4f}",
                f"{c.flat_ratio:.4f}",
                *[f"{c.strategy_returns[s]:.4f}" for s in STRATEGIES],
            ])


def main() -> None:
    parser = argparse.ArgumentParser(description="파라미터 그리드 서치")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--bots", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--out", default="grid_results.csv")
    parser.add_argument(
        "--herding", type=float, nargs="*", default=None,
        help="군집 확률 축. 기본 0.0 0.4 0.8. 0.0 만 주면 스펙 8번 원안",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="저항 계수를 더 촘촘하게 훑는다",
    )
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    prepared = prepare(scenario)
    herding_levels = tuple(args.herding) if args.herding is not None else HERDING_LEVELS

    if args.full:
        resistances = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
        caps = COARSE_CAP
    else:
        resistances, caps = COARSE_RESISTANCE, COARSE_CAP

    print(f"시나리오: {scenario.name} ({scenario.source_file})")
    print(f"그리드 {len(resistances)}x{len(caps)}, 군집 축 {herding_levels}")
    print(f"조합당 봇 {args.bots}개 x {args.runs}회, 시드 {args.seed} 고정")

    cells: list[GridCell] = []
    for herd in herding_levels:
        label = "독립(스펙 원안)" if herd == 0.0 else f"군집 {herd:.1f}"
        print(f"\n--- {label} ---")
        for r in resistances:
            for cap in caps:
                cell = run_cell(
                    prepared, r, cap, args.runs, args.bots, args.seed, herd
                )
                cells.append(cell)
                status = "탈락" if cell.rejected else "생존"
                print(f"  r={r:<5.2f} cap={cap:.1f}  보존 {cell.event_preservation:.3f}  "
                      f"영향 평균 {cell.market_impact*100:5.2f}% 피크 {cell.peak_impact*100:6.2f}%  "
                      f"damp 평균 {cell.mean_damp:.3f} p90 {cell.damp_p90:.3f}  "
                      f"cap작동 {cell.cap_bind_ratio*100:4.1f}%  {status}")

    write_csv(cells, args.out)
    print(f"\n전체 결과: {args.out}")

    for herd in herding_levels:
        subset = [c for c in cells if c.herding_prob == herd]
        front = pareto_front(subset)
        label = "독립(스펙 원안)" if herd == 0.0 else f"군집 {herd:.1f}"
        print(f"\n=== 파레토 최적 후보 — {label} ===")
        if not front:
            reasons = {c.reject_reason for c in subset if c.rejected}
            print(f"  생존한 조합이 없다. 탈락 사유: {reasons}")
            continue
        for c in front[:5]:
            print(f"\n  resistance_coef={c.resistance_coef}, "
                  f"dampening_cap={c.dampening_cap}")
            print(f"    이벤트 보존 {c.event_preservation:.3f} "
                  f"(표준편차 {c.event_preservation_sd:.3f})")
            print(f"    시장 영향도 {c.market_impact*100:.2f}% "
                  f"(표준편차 {c.market_impact_sd*100:.2f}%p)")
            print(f"    전략 변별력 {c.strategy_spread:.4f}, "
                  f"후반 1위 교체 {c.leader_changes:.1f}회, "
                  f"cap 작동 {c.cap_bind_ratio*100:.1f}%")
            print(f"    {describe(c)}")

    print("\n최적값 하나로 단정하지 않는다. 이벤트 보존과 참가자 영향력은 맞바꾸는 관계다.")
    print("최종 확정은 리허설에서 사람이 체감으로 판단한다.")


if __name__ == "__main__":
    main()
