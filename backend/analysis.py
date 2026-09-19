"""결과 분석 — tick_log, orders, events_fired 만으로 계산한다(스펙 12번).

게임 중에 분석용 로깅을 따로 추가하지 않는다. 참가자별 보유 변화는 orders 로,
가격과 변동 요인은 tick_log 로 전부 재구성할 수 있다.

다루는 것
  (1) 원인 분해     수익을 base_drift / event_delta / fear_shock / fx_shock 로 나눔
  (2) 근거별 성적표  매수 근거별 횟수와 평균 수익률 (FIFO 실현)
  (3) 성향 진단     자기 선언형 vs 행동 기반 2축, 괴리 명시
  (4) 군중심리      매수 쏠림을 따라간 사람과 아닌 사람의 성적 비교
  (5) 공매도 집중    펌프 구간 매수자와 비매수자, 덤프 전 탈출 비율
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field

# 행동 기반 성향 판정 기준
TREND_WINDOW = 5              # 매수 직전 추세를 보는 틱 수
TREND_THRESHOLD = 0.004       # 이보다 크면 '상승 중', 작으면 '하락 중'
NEWS_WINDOW = 4               # 헤드라인 이후 몇 틱까지를 '뉴스 직후'로 볼지
HOLD_MAX_TRADES = 10          # 매매가 이보다 적고 전부 초반이면 매수후보유
HOLD_EARLY_RATIO = 0.35       # 라운드 앞쪽 몇 %까지를 '초반'으로 볼지
DOMINANT_SHARE = 0.55         # 한 유형이 이 비율을 넘어야 그 유형으로 판정

# 군중심리 판정 기준
CROWD_WINDOW = 5              # 쏠림을 보는 틱 폭
CROWD_SHARE = 0.6             # 그 구간 전체 매수의 이 비율 이상이면 쏠림
CROWD_MIN_PLAYERS = 3         # 최소 몇 명이 몰려야 쏠림으로 볼지

BEHAVIOR_LABELS = {
    "momentum": "모멘텀추종",
    "contrarian": "역추세",
    "news": "뉴스반응",
    "hold": "매수후보유",
    "mixed": "혼합",
}


@dataclass
class Lot:
    qty: int
    price: int
    reason: str | None
    tick: int


@dataclass
class TradeOutcome:
    """매수 한 건의 성과. FIFO 로 매도와 짝지어 실현한다."""

    reason: str | None
    qty: int
    buy_price: int
    exit_price: int
    buy_tick: int
    realized: bool

    @property
    def return_pct(self) -> float:
        return self.exit_price / self.buy_price - 1.0


@dataclass
class GameData:
    """DB 에서 한 번만 읽어 오는 원자료."""

    session_id: int
    total_ticks: int
    starting_cash: int
    symbols: tuple[str, ...]
    prices: dict[str, list[int]]                  # 틱 0..N
    script: dict[str, list[dict]]                 # 틱별 변동 요인
    submitted_buy: dict[str, list[int]]
    orders: list[dict]
    events: list[dict]
    players: dict[int, dict]

    def final_price(self, symbol: str) -> int:
        return self.prices[symbol][-1]


def load_game_data(conn, session_id: int) -> GameData:
    session = conn.execute(
        "SELECT * FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    total_ticks = session["total_ticks"]

    rows = conn.execute(
        """SELECT tick, symbol, base_drift, event_delta, fear_shock, fx_shock,
                  total_script_delta, damp, actual_delta, price, buy_qty_submitted
           FROM tick_log WHERE session_id=? ORDER BY tick""",
        (session_id,),
    ).fetchall()

    symbols = tuple(sorted({r["symbol"] for r in rows}))
    prices = {s: [0] * (total_ticks + 1) for s in symbols}
    script = {s: [{} for _ in range(total_ticks + 1)] for s in symbols}
    submitted_buy = {s: [0] * (total_ticks + 1) for s in symbols}

    for r in rows:
        s, t = r["symbol"], r["tick"]
        prices[s][t] = r["price"]
        script[s][t] = {
            "base_drift": r["base_drift"], "event_delta": r["event_delta"],
            "fear_shock": r["fear_shock"], "fx_shock": r["fx_shock"],
            "damp": r["damp"], "actual_delta": r["actual_delta"],
        }
        submitted_buy[s][t] = r["buy_qty_submitted"]

    # 틱 0 의 가격은 tick_log 에 없다. 틱 1 의 가격과 변동률로 역산한다.
    for s in symbols:
        first = script[s][1]
        delta = first.get("actual_delta", 0.0)
        prices[s][0] = int(round(prices[s][1] / (1.0 + delta))) if delta != -1 else prices[s][1]
        for t in range(1, total_ticks + 1):
            if prices[s][t] == 0:
                prices[s][t] = prices[s][t - 1]

    orders = [dict(r) for r in conn.execute(
        """SELECT * FROM orders WHERE session_id=? AND filled_qty > 0
           ORDER BY fill_tick, id""",
        (session_id,),
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events_fired WHERE session_id=? ORDER BY headline_tick",
        (session_id,),
    ).fetchall()]
    players = {
        r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM players WHERE session_id=?", (session_id,)
        ).fetchall()
    }
    return GameData(
        session_id=session_id, total_ticks=total_ticks,
        starting_cash=session["starting_cash"], symbols=symbols, prices=prices,
        script=script, submitted_buy=submitted_buy, orders=orders, events=events,
        players=players,
    )


# ---------------------------------------------------------------- 보유 재구성

def holdings_timeline(data: GameData, player_id: int) -> dict[str, list[int]]:
    """orders 만으로 참가자의 틱별 보유 수량을 복원한다."""
    timeline = {s: [0] * (data.total_ticks + 1) for s in data.symbols}
    changes: dict[str, dict[int, int]] = {s: defaultdict(int) for s in data.symbols}
    for o in data.orders:
        if o["player_id"] != player_id:
            continue
        sign = 1 if o["side"] == "buy" else -1
        changes[o["symbol"]][o["fill_tick"]] += sign * o["filled_qty"]
    for symbol in data.symbols:
        held = 0
        for tick in range(0, data.total_ticks + 1):
            held += changes[symbol].get(tick, 0)
            timeline[symbol][tick] = held
    return timeline


# ---------------------------------------------------------------- (1) 원인 분해

def decompose(data: GameData, player_id: int) -> dict:
    """수익을 변동 요인별 기여분으로 나눈다.

    틱 t 에서 종목 i 를 q 주 들고 있었다면, 요인 X 의 기여 금액은
        q x 직전가 x X x (1 - damp)
    이다. actual_delta = total_script_delta x (1 - damp) 이므로 요인별로 나눠도
    합이 실제 평가손익 변화와 맞는다.
    """
    timeline = holdings_timeline(data, player_id)
    parts = {"base_drift": 0.0, "event_delta": 0.0, "fear_shock": 0.0, "fx_shock": 0.0}
    total_moved = 0.0

    for symbol in data.symbols:
        prices = data.prices[symbol]
        for tick in range(1, data.total_ticks + 1):
            qty = timeline[symbol][tick - 1]
            if qty == 0:
                continue
            row = data.script[symbol][tick]
            if not row:
                continue
            base_value = qty * prices[tick - 1]
            keep = 1.0 - row["damp"]
            for key in parts:
                parts[key] += base_value * row[key] * keep
            total_moved += base_value * row["actual_delta"]

    start = data.starting_cash
    return {
        "contributions": {k: round(v) for k, v in parts.items()},
        "contribution_pct": {k: v / start for k, v in parts.items()},
        "total_price_effect": round(total_moved),
        "note": "매매 타이밍과 수수료는 이 분해에 포함되지 않는다",
    }


# ---------------------------------------------------------------- (2) 근거별 성적표

def trade_outcomes(data: GameData, player_id: int) -> list[TradeOutcome]:
    """매수를 FIFO 로 매도와 짝지어 성과를 낸다. 남은 것은 최종가로 평가한다."""
    lots: dict[str, deque[Lot]] = {s: deque() for s in data.symbols}
    outcomes: list[TradeOutcome] = []

    for o in data.orders:
        if o["player_id"] != player_id:
            continue
        symbol = o["symbol"]
        if o["side"] == "buy":
            lots[symbol].append(
                Lot(o["filled_qty"], o["fill_price"], o["reason"], o["fill_tick"])
            )
            continue
        remaining = o["filled_qty"]
        while remaining > 0 and lots[symbol]:
            lot = lots[symbol][0]
            take = min(remaining, lot.qty)
            outcomes.append(TradeOutcome(
                reason=lot.reason, qty=take, buy_price=lot.price,
                exit_price=o["fill_price"], buy_tick=lot.tick, realized=True,
            ))
            lot.qty -= take
            remaining -= take
            if lot.qty == 0:
                lots[symbol].popleft()

    for symbol, queue in lots.items():
        for lot in queue:
            if lot.qty <= 0:
                continue
            outcomes.append(TradeOutcome(
                reason=lot.reason, qty=lot.qty, buy_price=lot.price,
                exit_price=data.final_price(symbol), buy_tick=lot.tick, realized=False,
            ))
    return outcomes


def reason_report(outcomes: list[TradeOutcome]) -> list[dict]:
    grouped: dict[str, list[TradeOutcome]] = defaultdict(list)
    for o in outcomes:
        grouped[o.reason or "미기재"].append(o)
    rows = []
    for reason, items in grouped.items():
        invested = sum(o.qty * o.buy_price for o in items)
        gained = sum(o.qty * (o.exit_price - o.buy_price) for o in items)
        rows.append({
            "reason": reason,
            "count": len(items),
            "invested": invested,
            "profit": gained,
            "avg_return": gained / invested if invested else 0.0,
            "realized_count": sum(1 for o in items if o.realized),
        })
    rows.sort(key=lambda r: -r["count"])
    return rows


# ---------------------------------------------------------------- (3) 성향 진단

def _trend_before(data: GameData, symbol: str, tick: int) -> float:
    prices = data.prices[symbol]
    past = max(0, tick - TREND_WINDOW)
    if prices[past] == 0:
        return 0.0
    return prices[tick] / prices[past] - 1.0


def _headline_ticks(data: GameData) -> list[int]:
    return [e["headline_tick"] for e in data.events if (e["headline"] or "").strip()]


def classify_behavior(data: GameData, player_id: int) -> dict:
    buys = [
        o for o in data.orders
        if o["player_id"] == player_id and o["side"] == "buy"
    ]
    if not buys:
        return {
            "type": "mixed", "label": BEHAVIOR_LABELS["mixed"],
            "votes": {}, "trade_count": 0,
            "note": "매수 기록이 없어 행동으로는 판정할 수 없다",
        }

    headlines = _headline_ticks(data)
    votes = {"momentum": 0, "contrarian": 0, "news": 0}
    for o in buys:
        tick = o["submit_tick"]
        if any(0 <= tick - h <= NEWS_WINDOW for h in headlines):
            votes["news"] += 1
            continue
        trend = _trend_before(data, o["symbol"], tick)
        if trend > TREND_THRESHOLD:
            votes["momentum"] += 1
        elif trend < -TREND_THRESHOLD:
            votes["contrarian"] += 1

    # 매수후보유의 결정적 신호는 '초반 이후 아무것도 하지 않았다' 는 것이다.
    # 매매 횟수만으로는 조용한 다른 유형과 구분되지 않는다.
    early_cut = data.total_ticks * HOLD_EARLY_RATIO
    all_trades = [o for o in data.orders if o["player_id"] == player_id]
    if len(all_trades) <= HOLD_MAX_TRADES and all(
        o["submit_tick"] <= early_cut for o in all_trades
    ):
        return {
            "type": "hold", "label": BEHAVIOR_LABELS["hold"], "votes": votes,
            "trade_count": len(all_trades),
            "note": "매매가 적고 초반에 담은 뒤 그대로 둔 형태",
        }

    total = sum(votes.values())
    if total == 0:
        kind = "mixed"
    else:
        kind = max(votes, key=votes.__getitem__)
        if votes[kind] / total < DOMINANT_SHARE:
            kind = "mixed"
    return {
        "type": kind, "label": BEHAVIOR_LABELS[kind], "votes": votes,
        "trade_count": len(all_trades), "note": "",
    }


def diagnose(data: GameData, player_id: int, outcomes: list[TradeOutcome]) -> dict:
    """자기 선언형(가장 많이 고른 근거)과 행동 기반을 함께 내고 괴리를 명시한다."""
    counts: dict[str, int] = defaultdict(int)
    for o in data.orders:
        if o["player_id"] == player_id and o["side"] == "buy" and o["reason"]:
            counts[o["reason"]] += 1
    declared = max(counts, key=counts.__getitem__) if counts else None
    behavior = classify_behavior(data, player_id)

    # 근거와 행동 유형의 자연스러운 대응 관계
    pairing = {
        "차트추세": "momentum", "저평가": "contrarian", "뉴스": "news",
        "분산": "hold", "실적성장": "mixed", "직감": "mixed",
    }
    expected = pairing.get(declared or "", None)
    gap = (
        declared is not None
        and behavior["type"] != "mixed"
        and expected is not None
        and expected != behavior["type"]
    )
    message = ""
    if gap:
        message = (
            f"스스로 {declared} 을(를) 가장 많이 골랐으나 "
            f"실제 매매는 {behavior['label']}에 가까움"
        )
    return {
        "declared": declared,
        "declared_counts": dict(counts),
        "behavior": behavior,
        "has_gap": gap,
        "gap_message": message,
    }


def type_averages(data: GameData, returns: dict[int, float]) -> list[dict]:
    """유형별 오늘 대회 평균 수익률."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for pid in data.players:
        kind = classify_behavior(data, pid)["type"]
        if pid in returns:
            grouped[kind].append(returns[pid])
    return [
        {
            "type": kind, "label": BEHAVIOR_LABELS[kind], "count": len(values),
            "avg_return": sum(values) / len(values) if values else 0.0,
        }
        for kind, values in sorted(grouped.items())
    ]


# ---------------------------------------------------------------- (4) 군중심리

def crowd_moments(data: GameData) -> list[dict]:
    """한 종목에 매수가 몰린 구간을 찾는다."""
    moments = []
    for start in range(1, data.total_ticks - CROWD_WINDOW + 1, CROWD_WINDOW):
        window = range(start, start + CROWD_WINDOW)
        totals = {
            s: sum(data.submitted_buy[s][t] * data.prices[s][t] for t in window)
            for s in data.symbols
        }
        grand = sum(totals.values())
        if grand <= 0:
            continue
        symbol = max(totals, key=totals.__getitem__)
        share = totals[symbol] / grand
        if share < CROWD_SHARE:
            continue
        followers = {
            o["player_id"] for o in data.orders
            if o["side"] == "buy" and o["symbol"] == symbol
            and start <= o["submit_tick"] < start + CROWD_WINDOW
        }
        if len(followers) < CROWD_MIN_PLAYERS:
            continue
        moments.append({
            "start_tick": start, "end_tick": start + CROWD_WINDOW - 1,
            "symbol": symbol, "share": share, "followers": sorted(followers),
        })
    return moments


def crowd_report(data: GameData, returns: dict[int, float]) -> dict:
    moments = crowd_moments(data)
    rows = []
    for m in moments:
        followers = [returns[p] for p in m["followers"] if p in returns]
        others = [r for p, r in returns.items() if p not in set(m["followers"])]
        rows.append({
            "start_tick": m["start_tick"], "end_tick": m["end_tick"],
            "symbol": m["symbol"], "share": m["share"],
            "follower_count": len(followers),
            "follower_avg_return": sum(followers) / len(followers) if followers else 0.0,
            "other_avg_return": sum(others) / len(others) if others else 0.0,
        })
    return {"moments": rows, "count": len(rows)}


# ---------------------------------------------------------------- (5) 공매도 집중

def short_pressure_report(data: GameData, returns: dict[int, float]) -> list[dict]:
    rows = []
    for event in data.events:
        if event["type"] != "short_pressure":
            continue
        schedule = json.loads(event["schedule_json"])
        pump_ticks = schedule["pump_ticks"]
        pump_start = event["impact_start_tick"]
        pump_end = pump_start + pump_ticks - 1
        dump_end = event["impact_end_tick"]
        symbol = event["symbol"]

        buyers, escaped = set(), set()
        for o in data.orders:
            if o["symbol"] != symbol:
                continue
            if o["side"] == "buy" and pump_start <= o["submit_tick"] <= pump_end:
                buyers.add(o["player_id"])
        for o in data.orders:
            if (o["symbol"] == symbol and o["side"] == "sell"
                    and o["player_id"] in buyers
                    and pump_end < o["submit_tick"] <= dump_end):
                escaped.add(o["player_id"])

        buyer_returns = [returns[p] for p in buyers if p in returns]
        other_returns = [r for p, r in returns.items() if p not in buyers]
        prices = data.prices[symbol]
        rows.append({
            "event_key": event["event_key"],
            "headline": event["headline"],
            "symbol": symbol,
            "pump_range": [pump_start, pump_end],
            "dump_range": [pump_end + 1, dump_end],
            "pump_realized_pct": (
                prices[pump_end] / prices[pump_start - 1] - 1.0
                if pump_start >= 1 and prices[pump_start - 1] else 0.0
            ),
            "dump_realized_pct": (
                prices[dump_end] / prices[pump_end] - 1.0 if prices[pump_end] else 0.0
            ),
            "buyer_count": len(buyers),
            "escaped_count": len(escaped),
            "escape_ratio": len(escaped) / len(buyers) if buyers else 0.0,
            "buyer_avg_return": (
                sum(buyer_returns) / len(buyer_returns) if buyer_returns else 0.0
            ),
            "other_avg_return": (
                sum(other_returns) / len(other_returns) if other_returns else 0.0
            ),
        })
    return rows


# ---------------------------------------------------------------- 조립

def build_result(session, player_id: int) -> dict:
    data = load_game_data(session.conn, session.session_id)
    scores = {s.player_id: s for s in session.scores()}
    returns = {pid: s.return_pct for pid, s in scores.items()}
    me = scores.get(player_id)
    outcomes = trade_outcomes(data, player_id)

    return {
        "me": None if me is None else {
            "player_id": me.player_id, "nickname": me.nickname,
            "final_asset": me.final_asset, "return_pct": me.return_pct,
            "risk_adjusted": me.risk_adjusted, "mdd": me.mdd,
            "rank_return": me.rank_return, "rank_risk": me.rank_risk,
            "rank_total": me.rank_total, "ticks_played": me.ticks_played,
            "eligible_for_risk": me.eligible_for_risk,
            "players_count": len(scores),
        },
        "decomposition": decompose(data, player_id),
        "reasons": reason_report(outcomes),
        "diagnosis": diagnose(data, player_id, outcomes),
        "type_averages": type_averages(data, returns),
        "crowd": crowd_report(data, returns),
        "short_pressure": short_pressure_report(data, returns),
        "stock_summary": [
            {
                "symbol": s,
                "final_price": data.final_price(s),
                "initial_price": data.prices[s][0],
                "chg_pct": data.final_price(s) / data.prices[s][0] - 1.0,
            }
            for s in data.symbols
        ],
    }
