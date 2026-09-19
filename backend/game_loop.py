"""게임 루프 — 틱 진행, 주문 큐, 체결, 브로드캐스트.

틱 t 의 처리 순서는 뒤집을 수 없다.

    1. 대기 주문 큐 확정      틱 t-1 동안 접수된 주문이 체결 대상이다
    2. 지수 갱신              공포지수, 환율
    3. 이벤트 델타            impact_delay 를 넘긴 이벤트만
    4. 가격 확정              접수 수량으로 f 를 계산한다
    5. 체결                   가격이 정해진 뒤에야 체결할 수 있다
    6. 기록                   tick_log, index_log, orders, holdings
    7. 자산 갱신              Welford 온라인 통계(메모리)
    8. 브로드캐스트           공통 + 체결자 개인
    9. 주기 갱신              순위 스냅샷, 지수 표시 변화분

4단계가 5단계보다 먼저이므로 f 는 '접수 수량' 기준이고 실제 체결 수량과 미세하게
다를 수 있다. 그래서 tick_log 에 접수/체결 수량을 둘 다 남긴다.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend import db as dbmod
from backend.event_scheduler import EventScheduler
from backend.index_engine import build_target_series, change_pct, step, step_fear
from backend.models import BUY_REASONS, Scenario
from backend.price_engine import TickInput, compute_tick
from backend.scoring import PlayerTracker, assign_ranks, leaderboard

STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_ENDED = "ended"

SPARK_TICKS = 60          # 상세 차트에 내려보내는 최근 틱 수
BOARD_TOP = 10            # 프로젝터 화면 상위 인원


class OrderRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PendingOrder:
    order_id: int
    player_id: int
    symbol: str
    side: str
    qty: int
    reason: str | None
    submit_tick: int
    quoted_price: int
    reserved: int


@dataclass
class PlayerState:
    player_id: int
    nickname: str
    device_token: str
    cash: int
    starting_cash: int
    join_tick: int
    reserved_cash: int = 0
    holdings: dict[str, int] = field(default_factory=dict)
    reserved_qty: dict[str, int] = field(default_factory=dict)
    avg_cost: dict[str, float] = field(default_factory=dict)
    realized_pnl: dict[str, int] = field(default_factory=dict)
    status: str = "active"
    tracker: PlayerTracker | None = None

    def free_qty(self, symbol: str) -> int:
        return self.holdings.get(symbol, 0) - self.reserved_qty.get(symbol, 0)

    def asset(self, prices: dict[str, int]) -> int:
        value = sum(q * prices[s] for s, q in self.holdings.items() if q)
        return self.cash + self.reserved_cash + value


class GameSession:
    """한 판의 모든 상태. FastAPI 앱이 하나만 들고 있는다."""

    def __init__(
        self, scenario: Scenario, conn, seed: int | None = None, speed: float = 1.0
    ) -> None:
        self.scenario = scenario
        # 배속. 리허설과 자동 테스트에서만 쓴다. 본 라운드는 반드시 1.0 이다.
        # 틱 길이 4.8초는 처음 해 보는 참가자가 판단할 시간을 위한 값이라 줄이지 않는다.
        if speed <= 0:
            raise ValueError("배속은 양수여야 한다")
        self.speed = speed
        self.tick_seconds = scenario.tick_seconds / speed
        self.conn = conn
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.rng = random.Random(self.seed)
        self.scheduler = EventScheduler(scenario, random.Random(self.seed))

        self.status = STATUS_WAITING
        self.tick = 0
        self.session_id: int | None = None

        self.symbols = tuple(s.symbol for s in scenario.stocks)
        self.stock_by_symbol = {s.symbol: s for s in scenario.stocks}
        self.prices = {s.symbol: s.initial_price for s in scenario.stocks}
        self.history = {s.symbol: [s.initial_price] for s in scenario.stocks}

        self.fear = [scenario.fear_index.initial]
        self.fx = [scenario.fx_rate.initial]
        self.fear_targets = build_target_series(
            list(scenario.fear_index.segments), scenario.total_ticks,
            scenario.fear_index.initial,
        )
        self.fx_targets = build_target_series(
            list(scenario.fx_rate.segments), scenario.total_ticks,
            scenario.fx_rate.initial,
        )
        self.drift = self._build_drift()

        self.players: dict[int, PlayerState] = {}
        self.by_token: dict[str, int] = {}
        self.nicknames: set[str] = set()
        self._next_player_id = 1
        self._next_order_id = 1

        self.queued: list[PendingOrder] = []   # 이번 틱에 접수된 주문
        self.pending: list[PendingOrder] = []  # 다음 틱에 체결될 주문
        # 최근 몇 틱간 종목별 순매수 참가자. 시세 화면의 "지금 순매수 N명" 에 쓴다.
        self.recent_flow: list[tuple[int, int, str, int]] = []   # (틱, 참가자, 종목, 부호x수량)

        self.lock = asyncio.Lock()
        self.hub = None                        # main.py 가 주입한다
        self.rank_snapshot: list[dict] = []
        self.display_delta = {"fear": 0.0, "fx": 0.0}
        self.news_feed: list[dict] = []
        self.tick_durations: list[float] = []
        self.exceptions: list[str] = []
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ 준비

    def _build_drift(self) -> dict[str, list[float]]:
        drift: dict[str, list[float]] = {}
        for stock in self.scenario.stocks:
            arr = [0.0] * (self.scenario.total_ticks + 1)
            for seg in stock.drift_segments:
                for tick in range(seg.from_tick + 1, seg.to_tick + 1):
                    arr[tick] = seg.drift
            drift[stock.symbol] = arr
        return drift

    def persist_session(self) -> int:
        cur = self.conn.execute(
            """INSERT INTO sessions
               (scenario_file, scenario_name, scenario_sha256, seed, status,
                current_tick, tick_seconds, total_ticks, starting_cash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (self.scenario.source_file, self.scenario.name, self.scenario.sha256,
             self.seed, self.status, 0, self.scenario.tick_seconds,
             self.scenario.total_ticks, self.scenario.starting_cash, _now()),
        )
        self.session_id = cur.lastrowid
        rows = [
            (self.session_id, e.key, e.type, e.difficulty, e.sector, e.symbol,
             e.headline, e.body, e.headline_tick, e.impact_start_tick,
             e.impact_end_tick, json.dumps({
                 "deltas": list(e.deltas), "targets": list(e.targets),
                 "pump_ticks": e.pump_ticks, "dump_ticks": e.dump_ticks,
                 "pump_total": e.pump_total, "dump_total": e.dump_total,
             }, ensure_ascii=False))
            for e in self.scheduler.events
        ]
        self.conn.executemany(
            """INSERT INTO events_fired
               (session_id, event_key, type, difficulty, sector, symbol, headline,
                body, headline_tick, impact_start_tick, impact_end_tick, schedule_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        return self.session_id

    # ------------------------------------------------------------ 참가

    def join(self, nickname: str, device_token: str) -> PlayerState:
        existing = self.by_token.get(device_token)
        if existing is not None:
            return self.players[existing]
        if nickname in self.nicknames:
            raise OrderRejected("NICKNAME_TAKEN", "이미 쓰고 있는 닉네임입니다")

        player_id = self._next_player_id
        self._next_player_id += 1
        state = PlayerState(
            player_id=player_id, nickname=nickname, device_token=device_token,
            cash=self.scenario.starting_cash,
            starting_cash=self.scenario.starting_cash, join_tick=self.tick,
        )
        state.tracker = PlayerTracker(
            player_id=player_id, nickname=nickname, join_tick=self.tick,
            starting_cash=self.scenario.starting_cash,
            prev_asset=self.scenario.starting_cash,
            peak_asset=self.scenario.starting_cash,
        )
        self.players[player_id] = state
        self.by_token[device_token] = player_id
        self.nicknames.add(nickname)
        self.conn.execute(
            """INSERT INTO players
               (id, session_id, nickname, device_token, cash, reserved_cash,
                starting_cash, join_tick, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (player_id, self.session_id, nickname, device_token, state.cash, 0,
             state.starting_cash, state.join_tick, "active", _now()),
        )
        return state

    def kick(self, player_id: int) -> None:
        state = self.players.get(player_id)
        if state is None:
            raise OrderRejected("UNKNOWN_PLAYER", "없는 참가자입니다")
        state.status = "kicked"
        self.conn.execute(
            "UPDATE players SET status='kicked' WHERE session_id=? AND id=?",
            (self.session_id, player_id),
        )

    # ------------------------------------------------------------ 주문

    def submit_order(
        self, player_id: int, symbol: str, side: str, qty: int, reason: str | None
    ) -> PendingOrder:
        """주문을 접수한다. 체결은 다음 틱 가격으로 한다.

        접수 시점에 현금(또는 수량)을 예약해 둔다. 같은 틱에 주문을 여러 번 넣어
        현금을 초과 사용하는 것을 막기 위해서다(결정-03).
        """
        state = self.players.get(player_id)
        if state is None:
            raise OrderRejected("UNKNOWN_PLAYER", "없는 참가자입니다")
        if state.status != "active":
            raise OrderRejected("PLAYER_KICKED", "참가가 종료된 상태입니다")
        if self.status != STATUS_RUNNING:
            raise OrderRejected("SESSION_NOT_RUNNING", "지금은 주문을 받지 않습니다")
        if self.tick >= self.scenario.total_ticks:
            raise OrderRejected(
                "LAST_TICK_CLOSED", "마지막 틱이라 체결될 수 없어 주문을 받지 않습니다"
            )
        if symbol not in self.stock_by_symbol:
            raise OrderRejected("UNKNOWN_SYMBOL", "없는 종목입니다")
        if not isinstance(qty, int) or qty <= 0:
            raise OrderRejected("INVALID_QTY", "수량은 1 이상의 정수여야 합니다")
        if side not in ("buy", "sell"):
            raise OrderRejected("INVALID_SIDE", "매수 또는 매도만 가능합니다")
        if side == "buy" and reason not in BUY_REASONS:
            raise OrderRejected("NO_REASON", "매수 근거를 하나 선택해야 합니다")

        params = self.scenario.params
        quoted = self.prices[symbol]
        reserved = 0

        if side == "buy":
            need = int(math.ceil(quoted * qty * (1.0 + params.fee_rate)))
            if need > state.cash:
                raise OrderRejected("NO_CASH", "현금이 부족합니다")
            # 비중 상한은 접수 시점에도 본다. 체결 시 가격으로 다시 확인한다(결정-04).
            total = state.asset(self.prices)
            held_value = state.holdings.get(symbol, 0) * quoted
            room = int(total * params.max_position_ratio) - held_value
            if quoted * qty > max(0, room):
                raise OrderRejected(
                    "OVER_CONCENTRATION",
                    f"한 종목에 총자산의 {int(params.max_position_ratio * 100)}% 를 "
                    "넘게 담을 수 없습니다",
                )
            state.cash -= need
            state.reserved_cash += need
            reserved = need
        else:
            if qty > state.free_qty(symbol):
                raise OrderRejected("NO_QTY", "보유 수량이 부족합니다")
            state.reserved_qty[symbol] = state.reserved_qty.get(symbol, 0) + qty

        order_id = self._next_order_id
        self._next_order_id += 1
        order = PendingOrder(
            order_id=order_id, player_id=player_id, symbol=symbol, side=side,
            qty=qty, reason=reason, submit_tick=self.tick, quoted_price=quoted,
            reserved=reserved,
        )
        self.queued.append(order)
        self.recent_flow.append(
            (self.tick, player_id, symbol, qty if side == "buy" else -qty)
        )
        self.conn.execute(
            """INSERT INTO orders
               (id, session_id, player_id, symbol, side, requested_qty, reason,
                submit_tick, quoted_price, reserved, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (order_id, self.session_id, player_id, symbol, side, qty, reason,
             self.tick, quoted, reserved, "pending", _now()),
        )
        return order

    def _fill(self, order: PendingOrder) -> tuple[int, str]:
        """확정된 틱 가격으로 체결한다. 모자라면 가능한 만큼만 채운다(부분체결)."""
        state = self.players[order.player_id]
        params = self.scenario.params
        price = self.prices[order.symbol]

        if order.side == "buy":
            state.reserved_cash -= order.reserved
            state.cash += order.reserved
            unit = price * (1.0 + params.fee_rate)
            by_cash = int(state.cash // unit) if unit > 0 else 0
            total = state.asset(self.prices)
            room_value = int(total * params.max_position_ratio) - \
                state.holdings.get(order.symbol, 0) * price
            by_room = max(0, int(room_value // price))
            qty = min(order.qty, by_cash, by_room)
            if qty <= 0:
                code = "NO_CASH" if by_cash <= 0 else "OVER_CONCENTRATION"
                return 0, code
            gross = price * qty
            fee = int(round(gross * params.fee_rate))
            state.cash -= gross + fee
            prev_qty = state.holdings.get(order.symbol, 0)
            prev_cost = state.avg_cost.get(order.symbol, 0.0) * prev_qty
            state.holdings[order.symbol] = prev_qty + qty
            state.avg_cost[order.symbol] = (prev_cost + gross + fee) / (prev_qty + qty)
            return qty, ""

        reserved = state.reserved_qty.get(order.symbol, 0)
        state.reserved_qty[order.symbol] = max(0, reserved - order.qty)
        qty = min(order.qty, state.holdings.get(order.symbol, 0))
        if qty <= 0:
            return 0, "NO_QTY"
        gross = price * qty
        fee = int(round(gross * params.fee_rate))
        state.cash += gross - fee
        cost = state.avg_cost.get(order.symbol, 0.0) * qty
        state.realized_pnl[order.symbol] = (
            state.realized_pnl.get(order.symbol, 0) + int(gross - fee - cost)
        )
        state.holdings[order.symbol] -= qty
        if state.holdings[order.symbol] == 0:
            state.holdings.pop(order.symbol)
            state.avg_cost.pop(order.symbol, None)
        return qty, ""

    # ------------------------------------------------------------ 틱

    def advance_tick(self) -> dict:
        """한 틱을 진행하고 브로드캐스트할 공통 페이로드를 돌려준다."""
        started = time.perf_counter()
        sc = self.scenario
        params = sc.params
        self.tick += 1
        tick = self.tick

        self.pending, self.queued = self.queued, []

        self.fear.append(step_fear(
            self.fear[-1], self.fear_targets[tick], sc.fear_index.theta,
            self.rng.gauss(0, sc.fear_index.noise_sigma),
        ))
        self.fx.append(step(
            self.fx[-1], self.fx_targets[tick], sc.fx_rate.theta,
            self.rng.gauss(0, sc.fx_rate.noise_sigma),
        ))
        fear_change = self.fear[-1] - self.fear[-2]
        fx_change = change_pct(self.fx[-2], self.fx[-1])

        submitted_buy = {s: 0 for s in self.symbols}
        submitted_sell = {s: 0 for s in self.symbols}
        for order in self.pending:
            bucket = submitted_buy if order.side == "buy" else submitted_sell
            bucket[order.symbol] += order.qty

        tick_rows = []
        for symbol in self.symbols:
            stock = self.stock_by_symbol[symbol]
            result = compute_tick(TickInput(
                symbol=symbol, prev_price=self.prices[symbol],
                liquidity=stock.liquidity, beta=stock.beta,
                fx_exposure=stock.fx_exposure, scenario_drift=self.drift[symbol][tick],
                noise=self.rng.gauss(0, sc.noise_sigma_for(symbol)),
                event_delta=self.scheduler.delta_for(tick, symbol),
                fear_change=fear_change, fx_change_pct=fx_change,
                buy_qty=submitted_buy[symbol], sell_qty=submitted_sell[symbol],
                resistance_coef=params.resistance_coef,
                dampening_cap=params.dampening_cap,
                fear_sensitivity=params.fear_sensitivity,
                fx_sensitivity=params.fx_sensitivity, min_price=params.min_price,
            ))
            self.prices[symbol] = result.price
            self.history[symbol].append(result.price)
            tick_rows.append(result)

        filled_buy = {s: 0 for s in self.symbols}
        filled_sell = {s: 0 for s in self.symbols}
        fills_by_player: dict[int, list[dict]] = {}
        for order in self.pending:
            qty, reject = self._fill(order)
            if order.side == "buy":
                filled_buy[order.symbol] += qty
            else:
                filled_sell[order.symbol] += qty
            price = self.prices[order.symbol]
            fee = int(round(price * qty * params.fee_rate)) if qty else 0
            if qty == 0:
                status = "rejected"
            elif qty < order.qty:
                status = "partial"
            else:
                status = "filled"
            self.conn.execute(
                """UPDATE orders SET filled_qty=?, fill_tick=?, fill_price=?, fee=?,
                   status=?, reject_code=? WHERE id=? AND session_id=?""",
                (qty, tick, price if qty else None, fee, status,
                 reject or None, order.order_id, self.session_id),
            )
            fills_by_player.setdefault(order.player_id, {"orders": []})["orders"].append({
                "order_id": order.order_id, "symbol": order.symbol,
                "side": order.side, "status": status, "filled_qty": qty,
                "requested_qty": order.qty, "fill_price": price if qty else None,
                "fee": fee, "reject_code": reject or None,
            })

        self.conn.executemany(
            """INSERT OR REPLACE INTO tick_log
               (session_id, tick, symbol, base_drift, event_delta, fear_shock,
                fx_shock, total_script_delta, f, damp, actual_delta, price,
                buy_qty_submitted, sell_qty_submitted, buy_qty_filled, sell_qty_filled)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(self.session_id, tick, r.symbol, r.base_drift, r.event_delta,
              r.fear_shock, r.fx_shock, r.total_script_delta, r.f, r.damp,
              r.actual_delta, r.price, submitted_buy[r.symbol],
              submitted_sell[r.symbol], filled_buy[r.symbol], filled_sell[r.symbol])
             for r in tick_rows],
        )
        self.conn.execute(
            """INSERT OR REPLACE INTO index_log
               (session_id, tick, fear_index, fear_target, fx_rate, fx_target)
               VALUES (?,?,?,?,?,?)""",
            (self.session_id, tick, self.fear[-1], self.fear_targets[tick],
             self.fx[-1], self.fx_targets[tick]),
        )
        self._persist_touched(fills_by_player.keys())
        self.conn.execute(
            "UPDATE sessions SET current_tick=? WHERE id=?", (tick, self.session_id)
        )

        for state in self.players.values():
            if state.tracker is not None:
                state.tracker.push_asset(state.asset(self.prices))

        # 체결이 있었던 참가자에게는 갱신된 장부를 함께 보낸다. 다시 조회하지 않아도
        # 현금과 보유 수량이 맞는다.
        for pid, bundle in fills_by_player.items():
            bundle["me"] = self.player_view(pid)

        headlines = [self._news_item(e, tick) for e in self.scheduler.headlines_at(tick)]
        self.news_feed = headlines + self.news_feed

        interval = params.snapshot_interval_ticks
        if tick % interval == 0 or tick == 1:
            self._refresh_rank_snapshot()
            anchor = max(0, tick - interval)
            self.display_delta = {
                "fear": self.fear[tick] - self.fear[anchor],
                "fx": self.fx[tick] - self.fx[anchor],
            }

        self.tick_durations.append((time.perf_counter() - started) * 1000.0)
        return {
            "type": "tick",
            "t": tick,
            "ts": int(time.time() * 1000),
            "remaining_seconds": max(
                0, int((sc.total_ticks - tick) * sc.tick_seconds)
            ),
            "p": dict(self.prices),
            "c": {
                s: self.prices[s] / self.stock_by_symbol[s].initial_price - 1.0
                for s in self.symbols
            },
            "chg_tick": {r.symbol: r.actual_delta for r in tick_rows},
            "f": {r.symbol: round(r.f, 4) for r in tick_rows},
            "flow": self.flow_summary(),
            "fear": round(self.fear[-1], 1),
            "fx": round(self.fx[-1], 1),
            "disp": {
                "fear_delta": round(self.display_delta["fear"]),
                "fx_delta": round(self.display_delta["fx"]),
            },
            "news": headlines,
            "badges": self.scheduler.active_badges(tick),
            "rank": self.rank_snapshot[:BOARD_TOP],
            "players_count": sum(
                1 for p in self.players.values() if p.status == "active"
            ),
            "fills": fills_by_player,
        }

    def _persist_touched(self, player_ids) -> None:
        for pid in player_ids:
            state = self.players[pid]
            self.conn.execute(
                "UPDATE players SET cash=?, reserved_cash=? WHERE session_id=? AND id=?",
                (state.cash, state.reserved_cash, self.session_id, pid),
            )
            for symbol in set(state.holdings) | set(state.reserved_qty):
                self.conn.execute(
                    """INSERT INTO holdings
                       (session_id, player_id, symbol, quantity, reserved_qty,
                        avg_cost, realized_pnl)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(session_id, player_id, symbol) DO UPDATE SET
                         quantity=excluded.quantity,
                         reserved_qty=excluded.reserved_qty,
                         avg_cost=excluded.avg_cost,
                         realized_pnl=excluded.realized_pnl""",
                    (self.session_id, pid, symbol, state.holdings.get(symbol, 0),
                     state.reserved_qty.get(symbol, 0),
                     state.avg_cost.get(symbol, 0.0),
                     state.realized_pnl.get(symbol, 0)),
                )

    def flow_summary(self) -> dict[str, dict]:
        """종목별 주문 쏠림 요약.

        f 는 가격 계산에 쓴 값 그대로이고, 참가자 수는 최근 구간에서 순매수인
        사람만 센다. f 만으로는 몇 명이 몰렸는지 알 수 없어서 둘을 같이 내려 준다.
        """
        window = self.scenario.params.snapshot_interval_ticks
        cutoff = self.tick - window
        self.recent_flow = [r for r in self.recent_flow if r[0] > cutoff]

        net: dict[str, dict[int, int]] = {s: {} for s in self.symbols}
        for _, player_id, symbol, signed in self.recent_flow:
            bucket = net.setdefault(symbol, {})
            bucket[player_id] = bucket.get(player_id, 0) + signed

        summary = {}
        for symbol in self.symbols:
            people = net.get(symbol, {})
            summary[symbol] = {
                "buyers": sum(1 for v in people.values() if v > 0),
                "sellers": sum(1 for v in people.values() if v < 0),
            }
        return summary

    def _news_item(self, event, tick: int) -> dict:
        return {
            "event_key": event.key,
            "headline": event.headline,
            "body": event.body,
            "sector": event.sector,
            "symbol": event.symbol,
            "difficulty": event.difficulty,
            "tick": event.headline_tick,
            "is_short_pressure": event.is_short_pressure,
            "badge": event.badge,
            "impact_state": event.state_at(tick),
        }

    def _refresh_rank_snapshot(self) -> None:
        scores = [
            p.tracker.score(self.scenario.params.min_ticks_for_risk_rank)
            for p in self.players.values()
            if p.tracker is not None and p.status == "active"
        ]
        assign_ranks(scores)
        self.rank_snapshot = [
            {
                "player_id": s.player_id, "nickname": s.nickname,
                "return_pct": s.return_pct, "risk_adjusted": s.risk_adjusted,
                "mdd": s.mdd, "rank_return": s.rank_return,
                "rank_risk": s.rank_risk, "rank_total": s.rank_total,
                "eligible_for_risk": s.eligible_for_risk,
                "final_asset": s.final_asset,
            }
            for s in sorted(scores, key=lambda x: x.rank_return)
        ]

    # ------------------------------------------------------------ 조회

    def scores(self):
        result = [
            p.tracker.score(self.scenario.params.min_ticks_for_risk_rank)
            for p in self.players.values() if p.tracker is not None
        ]
        return assign_ranks(result)

    def board(self, tab: str = "return", limit: int = BOARD_TOP):
        return leaderboard(self.scores(), tab, limit)

    def snapshot(self, player_id: int | None = None) -> dict:
        sc = self.scenario
        data = {
            "session": {
                "status": self.status, "current_tick": self.tick,
                "total_ticks": sc.total_ticks, "tick_seconds": sc.tick_seconds,
                "remaining_seconds": max(
                    0, int((sc.total_ticks - self.tick) * sc.tick_seconds)
                ),
                "scenario_name": sc.name,
            },
            "stocks": [
                {
                    "symbol": s.symbol, "name": s.name, "sector": s.sector,
                    "price": self.prices[s.symbol],
                    "initial_price": s.initial_price,
                    "chg_pct": self.prices[s.symbol] / s.initial_price - 1.0,
                    "avatar_color": s.avatar_color,
                    "spark": self.history[s.symbol][-SPARK_TICKS:],
                }
                for s in sc.stocks
            ],
            "indices": {
                "fear": {
                    "value": round(self.fear[-1], 1),
                    "display_delta": round(self.display_delta["fear"]),
                },
                "fx": {
                    "value": round(self.fx[-1], 1),
                    "display_delta": round(self.display_delta["fx"]),
                },
            },
            "news": [
                {**n, "impact_state": self._state_of(n["event_key"])}
                for n in self.news_feed[:20]
            ],
            "badges": self.scheduler.active_badges(self.tick),
            "flow": self.flow_summary(),
            "rank": self.rank_snapshot[:BOARD_TOP],
            "players_count": sum(
                1 for p in self.players.values() if p.status == "active"
            ),
            "reasons": list(BUY_REASONS),
            "me": None,
        }
        if player_id is not None and player_id in self.players:
            data["me"] = self.player_view(player_id)
        return data

    def _state_of(self, event_key: str) -> str:
        for e in self.scheduler.events:
            if e.key == event_key:
                return e.state_at(self.tick)
        return "done"

    def player_view(self, player_id: int) -> dict:
        state = self.players[player_id]
        asset = state.asset(self.prices)
        score = state.tracker.score(self.scenario.params.min_ticks_for_risk_rank)
        rank = next(
            (r["rank_return"] for r in self.rank_snapshot
             if r["player_id"] == player_id), 0,
        )
        return {
            "player_id": player_id, "nickname": state.nickname,
            "cash": state.cash, "reserved_cash": state.reserved_cash,
            "total_asset": asset, "starting_cash": state.starting_cash,
            "return_pct": asset / state.starting_cash - 1.0,
            "profit": asset - state.starting_cash,
            "rank": rank, "status": state.status,
            "join_tick": state.join_tick,
            "risk_adjusted": score.risk_adjusted, "mdd": score.mdd,
            "eligible_for_risk": score.eligible_for_risk,
            "holdings": [
                {
                    "symbol": symbol, "quantity": qty,
                    "avg_cost": state.avg_cost.get(symbol, 0.0),
                    "price": self.prices[symbol],
                    "value": qty * self.prices[symbol],
                    "pnl_pct": (
                        self.prices[symbol] / state.avg_cost[symbol] - 1.0
                        if state.avg_cost.get(symbol) else 0.0
                    ),
                    "weight": (
                        qty * self.prices[symbol] / asset if asset else 0.0
                    ),
                }
                for symbol, qty in sorted(state.holdings.items()) if qty
            ],
            "pending_orders": [
                {
                    "order_id": o.order_id, "symbol": o.symbol, "side": o.side,
                    "qty": o.qty, "quoted_price": o.quoted_price,
                }
                for o in self.queued + self.pending if o.player_id == player_id
            ],
        }

    # ------------------------------------------------------------ 진행 제어

    async def run(self) -> None:
        """틱 루프. 지연이 누적되지 않도록 절대 시각 기준으로 잔다."""
        sc = self.scenario
        started = time.perf_counter()
        while self.tick < sc.total_ticks:
            if self.status == STATUS_PAUSED:
                await asyncio.sleep(0.05)
                started = time.perf_counter() - self.tick * self.tick_seconds
                continue
            if self.status != STATUS_RUNNING:
                return
            try:
                async with self.lock:
                    payload = self.advance_tick()
                if self.hub is not None:
                    await self.hub.broadcast(payload)
            except Exception as exc:                       # noqa: BLE001
                self.exceptions.append(f"tick {self.tick}: {exc!r}")
                raise
            target = started + (self.tick + 1) * self.tick_seconds
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
        await self.end()

    async def start(self) -> None:
        if self.status == STATUS_RUNNING:
            return
        if self.session_id is None:
            self.persist_session()
        self.status = STATUS_RUNNING
        self.conn.execute(
            "UPDATE sessions SET status=?, started_at=? WHERE id=?",
            (self.status, _now(), self.session_id),
        )
        self._task = asyncio.create_task(self.run())

    async def pause(self) -> None:
        if self.status == STATUS_RUNNING:
            self.status = STATUS_PAUSED
            self.conn.execute(
                "UPDATE sessions SET status=? WHERE id=?", (self.status, self.session_id)
            )

    async def resume(self) -> None:
        if self.status == STATUS_PAUSED:
            self.status = STATUS_RUNNING
            self.conn.execute(
                "UPDATE sessions SET status=? WHERE id=?", (self.status, self.session_id)
            )

    async def end(self) -> None:
        if self.status == STATUS_ENDED:
            return
        self.status = STATUS_ENDED
        self._refresh_rank_snapshot()
        self.conn.execute(
            "UPDATE sessions SET status=?, ended_at=?, current_tick=? WHERE id=?",
            (self.status, _now(), self.tick, self.session_id),
        )
        if self.hub is not None:
            await self.hub.broadcast({"type": "ended", "t": self.tick})
