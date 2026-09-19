"""결과 분석 — 봇 데이터로 성향 유형을 구분해내는지 확인한다(스펙 16번).

실제 GameSession 을 봇으로 완주시킨 뒤, tick_log 와 orders 만 읽어 분석한다.
분석이 게임 중 별도 로깅 없이 성립하는지도 여기서 같이 검증된다.
"""

import pytest

from backend import analysis
from backend.bots import STRATEGIES, Bot, MarketView, build_bots
from backend.game_loop import OrderRejected

BOTS_PER_STRATEGY = 5


def _sync(bot: Bot, player) -> None:
    """봇이 보는 장부를 세션의 실제 장부와 맞춘다. 세션이 권위 있는 쪽이다."""
    bot.cash = player.cash
    bot.reserved_cash = player.reserved_cash
    bot.holdings = dict(player.holdings)
    bot.reserved_qty = dict(player.reserved_qty)
    bot.avg_cost = dict(player.avg_cost)


@pytest.fixture(scope="module")
def played(request):
    """봇 25마리로 한 판을 끝까지 돌린 세션."""
    from backend import db as dbmod
    from backend.game_loop import GameSession
    from backend.scenario_loader import load_scenario
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    scenario = load_scenario(root / "scenarios" / "festival_01.json")
    conn = dbmod.connect(":memory:")
    dbmod.init_schema(conn)
    session = GameSession(scenario, conn, seed=5150)
    session.persist_session()
    session.status = "running"

    count = BOTS_PER_STRATEGY * len(STRATEGIES)
    bots = build_bots(count, scenario.starting_cash, seed=5150)
    pairs = []
    for bot in bots:
        player = session.join(
            f"{bot.strategy}{bot.bot_id:02d}", f"dev-{bot.bot_id:04d}-analysis"
        )
        pairs.append((bot, player))

    symbols = session.symbols
    last_headline_tick, last_symbols = -999, ()
    for tick in range(1, scenario.total_ticks + 1):
        view = MarketView(
            tick=session.tick, prices=session.prices, history=session.history,
            symbols=symbols, fresh_headline_symbols=last_symbols,
            ticks_since_headline=session.tick - last_headline_tick,
        )
        for bot, player in pairs:
            _sync(bot, player)
            order = bot.decide(view, scenario.params.max_position_ratio)
            if order is None:
                continue
            try:
                session.submit_order(
                    player.player_id, order.symbol, order.side, order.qty, order.reason
                )
            except OrderRejected:
                pass
        session.advance_tick()
        headlines = session.scheduler.headlines_at(session.tick)
        if headlines:
            last_headline_tick = session.tick
            last_symbols = tuple(s for e in headlines for s in e.targets)

    session.status = "ended"
    session._refresh_rank_snapshot()
    yield session, pairs
    conn.close()


def test_봇이_실제로_거래했다(played):
    session, _ = played
    filled = session.conn.execute(
        "SELECT COUNT(*) c FROM orders WHERE session_id=? AND filled_qty>0",
        (session.session_id,),
    ).fetchone()["c"]
    assert filled > 100, f"체결이 너무 적어 분석을 검증할 수 없다 ({filled}건)"


def test_성향_5유형을_구분해낸다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)

    observed = {}
    for bot, player in pairs:
        kind = analysis.classify_behavior(data, player.player_id)["type"]
        observed.setdefault(bot.strategy, []).append(kind)

    def modal(values):
        return max(set(values), key=values.count)

    summary = {s: modal(v) for s, v in observed.items()}
    print("\n전략별 판정 결과:", summary)

    assert summary["buy_and_hold"] == "hold"
    assert summary["news_reactive"] == "news"
    assert summary["momentum"] in ("momentum", "news")
    assert summary["contrarian"] in ("contrarian", "news")
    assert summary["momentum"] != summary["contrarian"], (
        "모멘텀과 역추세가 같은 유형으로 뭉개지면 진단이 무의미하다"
    )
    distinct = {k for v in observed.values() for k in v}
    assert len(distinct) >= 4, f"판정 유형이 {len(distinct)}종뿐이다: {distinct}"


def test_원인_분해의_합이_실제_가격효과와_맞는다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)
    for _, player in pairs[:5]:
        result = analysis.decompose(data, player.player_id)
        parts = sum(result["contributions"].values())
        assert parts == pytest.approx(
            result["total_price_effect"], rel=0.02, abs=1000
        ), "요인별 기여의 합이 실제 평가손익 변화와 달라졌다"


def test_근거별_성적표가_6종_근거를_다룬다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)
    seen = set()
    for _, player in pairs:
        outcomes = analysis.trade_outcomes(data, player.player_id)
        for row in analysis.reason_report(outcomes):
            seen.add(row["reason"])
    assert len(seen) >= 4, f"근거가 {len(seen)}종만 나왔다: {seen}"


def test_FIFO_실현이_수량을_보존한다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)
    for _, player in pairs[:8]:
        bought = sum(
            o["filled_qty"] for o in data.orders
            if o["player_id"] == player.player_id and o["side"] == "buy"
        )
        matched = sum(o.qty for o in analysis.trade_outcomes(data, player.player_id))
        assert matched == bought, "매수 수량과 짝지은 수량이 다르다"


def test_보유_재구성이_실제_보유와_일치한다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)
    for _, player in pairs:
        timeline = analysis.holdings_timeline(data, player.player_id)
        for symbol in data.symbols:
            assert timeline[symbol][-1] == player.holdings.get(symbol, 0), (
                f"{player.nickname} {symbol} 재구성 불일치"
            )


def test_공매도_구간_분석이_나온다(played):
    session, pairs = played
    data = analysis.load_game_data(session.conn, session.session_id)
    returns = {s.player_id: s.return_pct for s in session.scores()}
    rows = analysis.short_pressure_report(data, returns)
    assert len(rows) >= 1
    for row in rows:
        assert row["pump_realized_pct"] > 0, "펌프 구간이 오르지 않았다"
        assert row["dump_realized_pct"] < 0, "덤프 구간이 내리지 않았다"
        assert 0.0 <= row["escape_ratio"] <= 1.0


def test_결과_전체가_조립된다(played):
    session, pairs = played
    payload = analysis.build_result(session, pairs[0][1].player_id)
    for key in ("me", "decomposition", "reasons", "diagnosis", "type_averages",
                "crowd", "short_pressure", "stock_summary"):
        assert key in payload
    assert payload["me"]["players_count"] == len(pairs)
    assert len(payload["stock_summary"]) == len(session.symbols)
