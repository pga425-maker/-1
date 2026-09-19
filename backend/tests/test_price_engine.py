"""가격 엔진 불변 조건. 스펙 2번이 테스트로 강제하라고 못박은 4개가 핵심이다."""

import math
import random

import pytest

from backend.price_engine import TickInput, compute_tick


def _random_input(rng: random.Random, **overrides) -> TickInput:
    base = dict(
        symbol="TEST",
        prev_price=rng.randint(100, 200_000),
        liquidity=rng.randint(1, 10_000),
        beta=rng.uniform(0.0, 2.5),
        fx_exposure=rng.uniform(-1.0, 1.0),
        scenario_drift=rng.uniform(-0.02, 0.02),
        noise=rng.gauss(0, 0.01),
        event_delta=rng.uniform(-0.3, 0.3),
        fear_change=rng.uniform(-15.0, 15.0),
        fx_change_pct=rng.uniform(-0.05, 0.05),
        buy_qty=rng.randint(0, 50_000),
        sell_qty=rng.randint(0, 50_000),
        resistance_coef=rng.uniform(0.0, 1.0),
        dampening_cap=rng.uniform(0.0, 0.99),
        fear_sensitivity=rng.uniform(0.0, 2.0),
        fx_sensitivity=rng.uniform(0.0, 3.0),
    )
    base.update(overrides)
    return TickInput(**base)


def test_불변조건_방향은_절대_뒤집히지_않는다():
    """참가자 주문은 변동폭을 깎을 뿐 방향을 바꾸지 못한다."""
    rng = random.Random(20260919)
    for _ in range(20_000):
        r = compute_tick(_random_input(rng))
        if r.actual_delta == 0:
            continue
        assert math.copysign(1, r.actual_delta) == math.copysign(1, r.total_script_delta), (
            f"방향이 뒤집혔다: total={r.total_script_delta} actual={r.actual_delta}"
        )


def test_불변조건_변동폭은_커지지_않는다():
    rng = random.Random(1234)
    for _ in range(20_000):
        r = compute_tick(_random_input(rng))
        assert abs(r.actual_delta) <= abs(r.total_script_delta) + 1e-12


def test_불변조건_가격은_항상_양수이고_NaN이_아니다():
    rng = random.Random(555)
    for _ in range(20_000):
        r = compute_tick(_random_input(rng))
        assert r.price > 0
        for value in (r.base_drift, r.event_delta, r.fear_shock, r.fx_shock,
                      r.total_script_delta, r.f, r.damp, r.actual_delta):
            assert math.isfinite(value)


def test_불변조건_유동성이_0이면_ValueError():
    rng = random.Random(7)
    with pytest.raises(ValueError, match="유동성"):
        compute_tick(_random_input(rng, liquidity=0))


def test_유동성_음수도_거부한다():
    rng = random.Random(8)
    with pytest.raises(ValueError, match="유동성"):
        compute_tick(_random_input(rng, liquidity=-100))


def test_dampening_cap이_1이상이면_거부한다():
    """(1 - damp) 가 0 이하가 되면 방향 보존 불변 조건이 깨진다."""
    rng = random.Random(9)
    for bad in (1.0, 1.5, -0.1):
        with pytest.raises(ValueError, match="dampening_cap"):
            compute_tick(_random_input(rng, dampening_cap=bad))


def test_f는_항상_clip된다():
    rng = random.Random(11)
    for _ in range(5_000):
        r = compute_tick(_random_input(rng))
        assert -1.0 <= r.f <= 1.0


def test_damp는_상한을_넘지_않는다():
    rng = random.Random(12)
    for _ in range(5_000):
        inp = _random_input(rng)
        r = compute_tick(inp)
        assert 0.0 <= r.damp <= inp.dampening_cap + 1e-12


def test_주문이_없으면_감쇠도_없다():
    rng = random.Random(13)
    inp = _random_input(rng, buy_qty=0, sell_qty=0)
    r = compute_tick(inp)
    assert r.f == 0.0
    assert r.damp == 0.0
    assert r.actual_delta == pytest.approx(r.total_script_delta)


def test_매수가_몰리면_상승폭이_깎인다():
    """같은 시나리오 델타에서 매수 저항이 커질수록 실현 변동폭이 작아져야 한다."""
    common = dict(
        symbol="T", prev_price=10_000, liquidity=1_000, beta=0.0, fx_exposure=0.0,
        scenario_drift=0.05, noise=0.0, event_delta=0.0, fear_change=0.0,
        fx_change_pct=0.0, sell_qty=0, resistance_coef=0.3, dampening_cap=0.7,
    )
    deltas = [compute_tick(TickInput(buy_qty=q, **common)).actual_delta
              for q in (0, 200, 600, 1_000, 5_000)]
    assert deltas == sorted(deltas, reverse=True)
    assert all(d > 0 for d in deltas), "감쇠가 방향을 뒤집으면 안 된다"


def test_최저가_하한이_동작한다():
    r = compute_tick(TickInput(
        symbol="T", prev_price=120, liquidity=100, beta=0.0, fx_exposure=0.0,
        scenario_drift=-0.9, noise=0.0, event_delta=0.0, fear_change=0.0,
        fx_change_pct=0.0, buy_qty=0, sell_qty=0, resistance_coef=0.0,
        dampening_cap=0.7, min_price=100,
    ))
    assert r.price == 100
    assert r.clamped is True


def test_공포지수_민감도_기본값은_스펙_원식과_같다():
    """fear_sensitivity=1.0 이면 -beta * (change/100) 그대로여야 한다."""
    r = compute_tick(TickInput(
        symbol="T", prev_price=10_000, liquidity=100, beta=1.4, fx_exposure=0.0,
        scenario_drift=0.0, noise=0.0, event_delta=0.0, fear_change=5.0,
        fx_change_pct=0.0, buy_qty=0, sell_qty=0, resistance_coef=0.0,
        dampening_cap=0.7,
    ))
    assert r.fear_shock == pytest.approx(-1.4 * 0.05)


def test_환율_충격은_소수_해석이다():
    """fx_change_pct 는 퍼센트 숫자가 아니라 소수다(결정-05)."""
    r = compute_tick(TickInput(
        symbol="T", prev_price=10_000, liquidity=100, beta=0.0, fx_exposure=0.8,
        scenario_drift=0.0, noise=0.0, event_delta=0.0, fear_change=0.0,
        fx_change_pct=-0.01, buy_qty=0, sell_qty=0, resistance_coef=0.0,
        dampening_cap=0.7,
    ))
    assert r.fx_shock == pytest.approx(-0.008)
