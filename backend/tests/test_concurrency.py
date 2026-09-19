"""동시성 — 같은 종목에 주문 100개를 동시에 넣어도 장부가 깨지지 않아야 한다.

합격 기준(스펙 16번)
  - 잔고 음수 0건
  - 전체 (현금 + 평가액) 총합이 수수료 오차 안에서 보존
"""

import asyncio

import pytest

from backend.game_loop import OrderRejected

pytestmark = pytest.mark.asyncio


def _fill_players(session, count=20):
    players = []
    for i in range(count):
        players.append(session.join(f"참가자{i:02d}", f"dev-{i:04d}-concurrency"))
    return players


def _book_total(session) -> int:
    total = 0
    for state in session.players.values():
        total += state.asset(session.prices)
    return total


def _total_fees(session) -> int:
    row = session.conn.execute(
        "SELECT COALESCE(SUM(fee), 0) s FROM orders WHERE session_id=?",
        (session.session_id,),
    ).fetchone()
    return row["s"]


async def test_같은_종목_동시_주문_100건(session):
    players = _fill_players(session, 20)
    symbol = "A006"
    price = session.prices[symbol]
    qty = max(1, int(session.scenario.starting_cash * 0.04) // price)

    async def submit(player):
        async with session.lock:
            try:
                session.submit_order(player.player_id, symbol, "buy", qty, "직감")
                return True
            except OrderRejected:
                return False

    tasks = [submit(players[i % len(players)]) for i in range(100)]
    results = await asyncio.gather(*tasks)
    accepted = sum(results)
    assert accepted > 0, "전부 거부되면 테스트가 무의미하다"

    async with session.lock:
        session.advance_tick()

    for state in session.players.values():
        assert state.cash >= 0, f"{state.nickname} 현금이 음수다"
        assert state.reserved_cash >= 0
        for sym, q in state.holdings.items():
            assert q >= 0, f"{state.nickname} {sym} 보유가 음수다"

    # 체결가와 평가가가 같은 틱이므로, 이 시점의 총합은 시작 자금에서 수수료만
    # 빠진 값이어야 한다. 차이가 나면 어딘가에서 돈이 생기거나 사라진 것이다.
    expected = session.scenario.starting_cash * len(players) - _total_fees(session)
    actual = _book_total(session)
    assert abs(actual - expected) <= len(players), (
        f"장부 총합이 맞지 않는다: 기대 {expected}, 실제 {actual}, "
        f"수수료 {_total_fees(session)}, 차이 {actual - expected}"
    )


async def test_현금을_초과해_주문할_수_없다(session):
    player = session.join("올인러", "dev-allin-00000001")
    price = session.prices["A007"]
    qty = int(player.cash * 0.45) // price

    async def submit():
        async with session.lock:
            try:
                session.submit_order(player.player_id, "A007", "buy", qty, "직감")
                return True
            except OrderRejected:
                return False

    results = await asyncio.gather(*[submit() for _ in range(20)])
    assert player.cash >= 0
    assert sum(results) <= 3, "예약 없이 통과하면 현금을 초과 사용하게 된다"

    async with session.lock:
        session.advance_tick()
    assert player.cash >= 0


async def test_동시_매도로_보유를_초과할_수_없다(session):
    player = session.join("털이", "dev-sell-00000001")
    async with session.lock:
        session.submit_order(player.player_id, "A004", "buy", 100, "분산")
        session.advance_tick()
    held = player.holdings["A004"]

    async def sell():
        async with session.lock:
            try:
                session.submit_order(player.player_id, "A004", "sell", held, None)
                return True
            except OrderRejected:
                return False

    results = await asyncio.gather(*[sell() for _ in range(10)])
    assert sum(results) == 1, "예약 수량을 무시하면 없는 주식을 팔게 된다"
    async with session.lock:
        session.advance_tick()
    assert player.holdings.get("A004", 0) == 0


async def test_틱_진행_중_장부가_음수로_가지_않는다(session):
    players = _fill_players(session, 10)
    rng_symbols = ["A001", "A003", "A006", "A007"]

    for tick in range(40):
        async with session.lock:
            for i, player in enumerate(players):
                symbol = rng_symbols[(tick + i) % len(rng_symbols)]
                price = session.prices[symbol]
                qty = max(1, int(player.cash * 0.2) // price)
                try:
                    if tick % 3 == 2 and player.holdings.get(symbol):
                        session.submit_order(
                            player.player_id, symbol, "sell",
                            max(1, player.free_qty(symbol) // 2), None,
                        )
                    else:
                        session.submit_order(
                            player.player_id, symbol, "buy", qty, "차트추세"
                        )
                except OrderRejected:
                    pass
            session.advance_tick()

    for state in session.players.values():
        assert state.cash >= 0
        assert state.reserved_cash >= 0
        assert all(q >= 0 for q in state.holdings.values())
        assert all(q >= 0 for q in state.reserved_qty.values())
        assert state.asset(session.prices) > 0
