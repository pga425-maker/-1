"""재접속 복구 — 휴대폰을 껐다 켜거나 브라우저가 죽어도 그대로 이어져야 한다.

축제 부스에서 가장 흔한 사고다. 참가자가 실수로 창을 닫거나 화면이 멈춰서
새로고침했을 때 보유와 잔고가 날아가면 그 자리에서 항의가 나온다.

세션 수준이 아니라 실제 API 를 통해 확인한다.
"""

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
os.environ.setdefault("ADMIN_PASSWORD", "reconnect-test")
os.environ["FESTIVAL_DB"] = ":memory:"

from fastapi.testclient import TestClient      # noqa: E402

from backend.main import app, state            # noqa: E402

DEVICE = "reconnect-device-00000001"
ADMIN = {"password": "reconnect-test"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        token = c.post("/api/admin/login", json=ADMIN).json()["admin_token"]
        headers = {"X-Admin-Token": token}
        res = c.post(
            "/api/admin/start",
            json={"scenario_file": "festival_01.json", "seed": 424242,
                  "speed": 200, "reset_db": True},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        yield c, headers
        c.post("/api/admin/end", headers=headers)


def _join(client, nickname, device):
    res = client.post(
        "/api/join", json={"nickname": nickname, "device_token": device}
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_같은_기기로_재접속하면_보유와_잔고가_그대로다(client):
    c, _ = client
    first = _join(c, "가온이", DEVICE)
    token = first["player_token"]
    headers = {"X-Player-Token": token}

    snap = c.get("/api/state", headers=headers).json()
    symbol = snap["stocks"][0]["symbol"]
    price = snap["stocks"][0]["price"]
    qty = max(1, int(snap["me"]["cash"] * 0.2) // price)

    order = c.post(
        "/api/order",
        json={"symbol": symbol, "side": "buy", "qty": qty, "reason": "실적성장"},
        headers=headers,
    )
    assert order.status_code == 200, order.text

    # 체결될 때까지 틱을 진행시킨다
    session = state.session
    session.advance_tick()

    before = c.get("/api/state", headers=headers).json()["me"]
    assert before["holdings"], "체결이 안 되면 테스트가 무의미하다"

    # 휴대폰을 껐다 켠 상황. 참가자 토큰은 잃었지만 기기 토큰은 남아 있다.
    again = _join(c, "아무이름이나", DEVICE)
    assert again["rejoined"] is True
    assert again["player_id"] == first["player_id"]
    assert again["nickname"] == "가온이", "닉네임이 바뀌면 안 된다"

    after = c.get(
        "/api/state", headers={"X-Player-Token": again["player_token"]}
    ).json()["me"]

    assert after["cash"] == before["cash"]
    assert after["reserved_cash"] == before["reserved_cash"]
    assert after["total_asset"] == before["total_asset"]
    assert after["holdings"] == before["holdings"]


def test_재접속해도_참가자_수가_늘지_않는다(client):
    c, _ = client
    _join(c, "가온이", DEVICE)
    first_count = c.get("/api/meta").json()
    for _ in range(4):
        _join(c, "가온이", DEVICE)
    session = state.session
    assert len(session.players) == 1
    assert first_count is not None


def test_재접속_후에도_주문이_정상_동작한다(client):
    c, _ = client
    first = _join(c, "가온이", DEVICE)
    session = state.session
    session.advance_tick()

    again = _join(c, "가온이", DEVICE)
    headers = {"X-Player-Token": again["player_token"]}
    snap = c.get("/api/state", headers=headers).json()
    symbol = snap["stocks"][1]["symbol"]
    qty = max(1, int(snap["me"]["cash"] * 0.1) // snap["stocks"][1]["price"])

    res = c.post(
        "/api/order",
        json={"symbol": symbol, "side": "buy", "qty": qty, "reason": "분산"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    session.advance_tick()
    after = c.get("/api/state", headers=headers).json()["me"]
    assert any(h["symbol"] == symbol for h in after["holdings"])
    assert after["player_id"] == first["player_id"]


def test_다른_기기는_다른_참가자다(client):
    c, _ = client
    _join(c, "가온이", DEVICE)
    other = _join(c, "다른사람", "reconnect-device-00000002")
    assert other["rejoined"] is False
    assert len(state.session.players) == 2


def test_중복_닉네임은_다른_기기에서_막힌다(client):
    c, _ = client
    _join(c, "가온이", DEVICE)
    res = c.post(
        "/api/join",
        json={"nickname": "가온이", "device_token": "reconnect-device-00000003"},
    )
    assert res.status_code == 409


def test_욕설_닉네임은_거부된다(client):
    c, _ = client
    res = c.post(
        "/api/join",
        json={"nickname": "시발놈", "device_token": "reconnect-device-00000004"},
    )
    assert res.status_code == 400
    assert "쓸 수 없는" in res.json()["detail"]
