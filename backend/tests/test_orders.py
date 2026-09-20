"""주문 검증 — 스펙 16번이 요구한 거부 케이스 전부."""

import pytest

from backend.game_loop import STATUS_ENDED, OrderRejected


def _join(session, name="가온이", token="dev-0001-aaaa"):
    return session.join(name, token)


def test_정상_매수는_접수되고_현금이_예약된다(session):
    player = _join(session)
    before = player.cash
    order = session.submit_order(player.player_id, "A006", "buy", 10, "차트추세")
    assert order.qty == 10
    assert player.reserved_cash == order.reserved
    assert player.cash == before - order.reserved
    assert order.reserved >= order.quoted_price * 10


def test_잔고가_부족하면_거부한다(session):
    player = _join(session)
    huge = player.cash // session.prices["A001"] + 100
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A001", "buy", huge, "실적성장")
    assert exc.value.code == "NO_CASH"


def test_한_종목_비중_상한을_넘으면_거부한다(session):
    player = _join(session)
    price = session.prices["A004"]
    over = int(player.cash * 0.6) // price
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A004", "buy", over, "분산")
    assert exc.value.code == "OVER_CONCENTRATION"


def test_상한_직전까지는_통과한다(session):
    player = _join(session)
    price = session.prices["A004"]
    just_under = int(player.cash * 0.49) // price
    order = session.submit_order(player.player_id, "A004", "buy", just_under, "분산")
    assert order.qty == just_under


@pytest.mark.parametrize("qty", [0, -1, -1000])
def test_수량이_0이거나_음수면_거부한다(session, qty):
    player = _join(session)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", qty, "직감")
    assert exc.value.code == "INVALID_QTY"


def test_없는_종목은_거부한다(session):
    player = _join(session)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "Z999", "buy", 1, "직감")
    assert exc.value.code == "UNKNOWN_SYMBOL"


def test_매수_근거를_안_고르면_거부한다(session):
    player = _join(session)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", 1, None)
    assert exc.value.code == "NO_REASON"


def test_모르는_근거도_거부한다(session):
    player = _join(session)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", 1, "느낌적인느낌")
    assert exc.value.code == "NO_REASON"


def test_매도에는_근거를_요구하지_않는다(session):
    player = _join(session)
    session.submit_order(player.player_id, "A006", "buy", 10, "직감")
    session.advance_tick()
    order = session.submit_order(player.player_id, "A006", "sell", 5, None)
    assert order.side == "sell"


def test_보유하지_않은_종목은_팔_수_없다(session):
    player = _join(session)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A003", "sell", 1, None)
    assert exc.value.code == "NO_QTY"


def test_이미_매도_예약한_수량은_다시_팔_수_없다(session):
    player = _join(session)
    session.submit_order(player.player_id, "A006", "buy", 10, "직감")
    session.advance_tick()
    session.submit_order(player.player_id, "A006", "sell", 10, None)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "sell", 1, None)
    assert exc.value.code == "NO_QTY"


def test_종료된_판에서는_주문을_받지_않는다(session):
    player = _join(session)
    session.status = STATUS_ENDED
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", 1, "직감")
    assert exc.value.code == "SESSION_NOT_RUNNING"


def test_마지막_틱에는_주문을_받지_않는다(session):
    """다음 틱 가격으로 체결되므로 마지막 틱 주문은 체결될 수 없다."""
    player = _join(session)
    session.tick = session.scenario.total_ticks
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", 1, "직감")
    assert exc.value.code == "LAST_TICK_CLOSED"


def test_강제_퇴장된_참가자는_주문할_수_없다(session):
    player = _join(session)
    session.kick(player.player_id)
    with pytest.raises(OrderRejected) as exc:
        session.submit_order(player.player_id, "A006", "buy", 1, "직감")
    assert exc.value.code == "PLAYER_KICKED"


def test_같은_기기로_다시_들어오면_기존_참가자로_복구된다(session):
    first = _join(session, "가온이", "dev-abc-12345678")
    session.submit_order(first.player_id, "A006", "buy", 10, "직감")
    session.advance_tick()
    again = session.join("아무개", "dev-abc-12345678")
    assert again.player_id == first.player_id
    assert again.holdings == first.holdings


def test_닉네임_중복은_거부한다(session):
    _join(session, "가온이", "dev-aaaa-11111111")
    with pytest.raises(OrderRejected) as exc:
        session.join("가온이", "dev-bbbb-22222222")
    assert exc.value.code == "NICKNAME_TAKEN"


def test_주문은_다음_틱_가격으로_체결된다(session):
    """현재가를 보고 즉시 체결되면 안 된다."""
    player = _join(session)
    quoted = session.prices["A001"]
    session.submit_order(player.player_id, "A001", "buy", 5, "실적성장")
    assert player.holdings.get("A001", 0) == 0, "접수만으로 체결되면 안 된다"
    session.advance_tick()
    assert player.holdings.get("A001", 0) == 5
    row = session.conn.execute(
        "SELECT fill_price, quoted_price, fill_tick FROM orders WHERE id=1"
    ).fetchone()
    assert row["quoted_price"] == quoted
    assert row["fill_tick"] == 1
    assert row["fill_price"] == session.prices["A001"]


def test_수수료가_양방향으로_붙는다(session):
    player = _join(session)
    session.submit_order(player.player_id, "A002", "buy", 10, "저평가")
    session.advance_tick()
    session.submit_order(player.player_id, "A002", "sell", 10, None)
    session.advance_tick()
    fees = session.conn.execute(
        "SELECT SUM(fee) s FROM orders WHERE session_id=?", (session.session_id,)
    ).fetchone()["s"]
    assert fees > 0
    assert player.cash < player.starting_cash or player.holdings == {}
