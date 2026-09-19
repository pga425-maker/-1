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

# 1차 그리드. 스펙 8번이 지정한 조합이다.
COARSE_RESISTANCE = (0.1, 0.3, 0.5, 0.7)
COARSE_CAP = (0.5, 0.6, 0.7, 0.8)

# 탈락 기준
MIN_EVENT_PRESERVATION = 0.3
DIVERGENCE_UP_RATIO = 20.0


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

    @property
    def influence_index(self) -> float:
        """5개 봇 전략 최종 수익률의 표준편차. 참가자 영향력의 대리 지표."""
        return statistics.pstdev(list(self.strategy_returns.values()))


def run_round(
    prepared: Prepared,
    seed: int,
    resistance_coef: float,
    dampening_cap: float,
    bot_count: int = 40,
) -> RoundResult:
    sc = prepared.scenario
    params = sc.params
    rng = random.Random(seed)
    scheduler = EventScheduler(sc, random.Random(seed))
    bots = build_bots(bot_count, sc.starting_cash, seed)
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
        )
        for bot in bots:
            order = bot.decide(view, params.max_position_ratio)
            if order is None:
                continue
            if _reserve(bot, order, prices, params):
                queued.append(order)

        assets = [(b.total_asset(prices), b.bot_id) for b in bots]
        leader_history.append(max(assets)[1])

    final_returns = {
        b.bot_id: b.total_asset(prices) / b.starting_cash - 1.0 for b in bots
    }
    strategy_returns = {
        strategy: statistics.mean(
            final_returns[b.bot_id] for b in bots if b.strategy == strategy
        )
        for strategy in STRATEGIES
    }

    half = len(leader_history) // 2
    changes = sum(
        1 for a, b in zip(leader_history[half:], leader_history[half + 1:]) if a != b
    )
    returns_list = list(final_returns.values())

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
        fear_path=fear, fx_path=fx,
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

@dataclass
class GridCell:
    resistance_coef: float
    dampening_cap: float
    runs: int
    event_preservation: float
    event_preservation_sd: float
    influence_index: float
    influence_index_sd: float
    divergence_runs: int
    mean_damp: float
    fill_mismatch: float
    strategy_returns: dict[str, float]
    leader_changes: float
    ruined_ratio: float
    flat_ratio: float

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
    runs: int, bot_count: int, base_seed: int,
) -> GridCell:
    results = [
        run_round(prepared, base_seed + i, resistance_coef, dampening_cap, bot_count)
        for i in range(runs)
    ]
    preservation = [r.event_preservation for r in results]
    influence = [r.influence_index for r in results]
    damps = [d for r in results for d in r.damp_samples]
    strategies = {
        s: statistics.mean(r.strategy_returns[s] for r in results) for s in STRATEGIES
    }
    return GridCell(
        resistance_coef=resistance_coef, dampening_cap=dampening_cap, runs=runs,
        event_preservation=statistics.mean(preservation),
        event_preservation_sd=statistics.pstdev(preservation),
        influence_index=statistics.mean(influence),
        influence_index_sd=statistics.pstdev(influence),
        divergence_runs=sum(1 for r in results if r.divergence_flag),
        mean_damp=statistics.mean(damps) if damps else 0.0,
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
    if cell.influence_index >= 0.15:
        spread = "전략에 따라 성적이 크게 갈린다"
    elif cell.influence_index >= 0.08:
        spread = "전략 차이가 분명히 드러난다"
    else:
        spread = "어떤 전략을 써도 결과가 비슷해진다"
    return f"{flavor}. {spread}"


def write_csv(cells: list[GridCell], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "resistance_coef", "dampening_cap", "runs",
            "event_preservation", "event_preservation_sd",
            "influence_index", "influence_index_sd",
            "divergence_runs", "rejected", "reject_reason",
            "mean_damp", "fill_mismatch",
            "leader_changes_2nd_half", "ruined_ratio", "flat_ratio",
            *[f"ret_{s}" for s in STRATEGIES],
        ])
        for c in cells:
            writer.writerow([
                c.resistance_coef, c.dampening_cap, c.runs,
                f"{c.event_preservation:.4f}", f"{c.event_preservation_sd:.4f}",
                f"{c.influence_index:.4f}", f"{c.influence_index_sd:.4f}",
                c.divergence_runs, int(c.rejected), c.reject_reason,
                f"{c.mean_damp:.4f}", f"{c.fill_mismatch:.4f}",
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
        "--full", action="store_true",
        help="0.1~0.7 x 0.5~0.8 전수 (1차 4x4 가 아니라)",
    )
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    prepared = prepare(scenario)

    if args.full:
        resistances = tuple(round(0.1 * i, 1) for i in range(1, 8))
        caps = tuple(round(0.5 + 0.1 * i, 1) for i in range(4))
    else:
        resistances, caps = COARSE_RESISTANCE, COARSE_CAP

    print(f"시나리오: {scenario.name} ({scenario.source_file})")
    print(f"그리드 {len(resistances)}x{len(caps)}, 조합당 봇 {args.bots}개 x {args.runs}회, "
          f"시드 {args.seed} 고정")
    print()

    cells: list[GridCell] = []
    for r in resistances:
        for cap in caps:
            cell = run_cell(prepared, r, cap, args.runs, args.bots, args.seed)
            cells.append(cell)
            status = "탈락" if cell.rejected else "생존"
            print(f"  r={r:.1f} cap={cap:.1f}  보존 {cell.event_preservation:.3f}  "
                  f"영향력 {cell.influence_index:.4f}  damp평균 {cell.mean_damp:.3f}  "
                  f"{status}")

    write_csv(cells, args.out)
    print(f"\n전체 결과: {args.out}")

    front = pareto_front(cells)
    print("\n=== 파레토 최적 후보 ===")
    if not front:
        print("생존한 조합이 없다. 탈락 사유를 확인할 것.")
        return
    for c in front[:5]:
        print(f"\n  resistance_coef={c.resistance_coef:.1f}, "
              f"dampening_cap={c.dampening_cap:.1f}")
        print(f"    이벤트 보존 {c.event_preservation:.3f} "
              f"(표준편차 {c.event_preservation_sd:.3f})")
        print(f"    영향력 지수 {c.influence_index:.4f} "
              f"(표준편차 {c.influence_index_sd:.4f})")
        print(f"    {describe(c)}")

    print("\n최적값 하나로 단정하지 않는다. 이벤트 보존과 참가자 영향력은 맞바꾸는 관계다.")
    print("최종 확정은 리허설에서 사람이 체감으로 판단한다.")


if __name__ == "__main__":
    main()
