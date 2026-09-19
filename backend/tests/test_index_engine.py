"""공포지수 / 환율 평균회귀 엔진 테스트."""

import random

import pytest

from backend.index_engine import (
    Segment, build_target_series, change_pct, display_delta, step, step_fear,
)


def test_공포지수는_0과_100을_벗어나지_않는다():
    """노이즈를 비상식적으로 키워도 clamp 가 버텨야 한다."""
    rng = random.Random(42)
    value = 50.0
    for _ in range(100_000):
        target = rng.uniform(-500, 500)
        value = step_fear(value, target, rng.uniform(0, 1), rng.gauss(0, 50))
        assert 0.0 <= value <= 100.0


def test_theta가_범위를_벗어나면_거부한다():
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError, match="theta"):
            step(100.0, 100.0, bad, 0.0)


def test_램프_보간에_계단_점프가_없다():
    """구간이 바뀌는 지점에서 목표값이 튀면 안 된다. 스펙 3번의 핵심 요구."""
    segments = [
        Segment(0, 90, 20), Segment(90, 135, 36), Segment(135, 165, 31),
        Segment(165, 210, 28), Segment(210, 240, 33),
    ]
    series = build_target_series(segments, 240, initial=20)
    diffs = [abs(b - a) for a, b in zip(series, series[1:])]
    # 가장 가파른 구간은 90~135 의 (36-20)/45 = 0.356 이다.
    assert max(diffs) == pytest.approx(16 / 45, abs=1e-9)
    # 어느 한 틱에서도 인접 틱 평균 변화의 3배를 넘는 점프가 없어야 한다.
    assert max(diffs) < 3 * (sum(diffs) / len(diffs))


def test_구간_목표값에_정확히_도달한다():
    segments = [Segment(0, 50, 20), Segment(50, 100, 45)]
    series = build_target_series(segments, 100, initial=20)
    assert series[0] == 20
    assert series[50] == pytest.approx(20)
    assert series[100] == pytest.approx(45)
    assert series[75] == pytest.approx(32.5)


def test_평탄_구간은_정말_평탄하다():
    series = build_target_series([Segment(0, 90, 20)], 90, initial=20)
    assert all(v == pytest.approx(20) for v in series)


def test_마지막_구간이_짧으면_마지막_목표값을_유지한다():
    series = build_target_series([Segment(0, 50, 30)], 80, initial=10)
    assert series[50] == pytest.approx(30)
    assert series[80] == pytest.approx(30)


def test_평균회귀는_목표로_수렴한다():
    """노이즈가 없으면 목표값으로 단조 수렴해야 한다."""
    value = 20.0
    for _ in range(200):
        value = step(value, 50.0, 0.15, 0.0)
    assert value == pytest.approx(50.0, abs=1e-6)


def test_충격_후_서서히_복귀한다():
    """이벤트로 튄 뒤 목표로 돌아오는 움직임이 나와야 한다."""
    value = step_fear(20.0, 20.0, 0.15, 25.0)   # 큰 충격
    assert value > 40
    path = [value]
    for _ in range(40):
        value = step_fear(value, 20.0, 0.15, 0.0)
        path.append(value)
    assert path == sorted(path, reverse=True), "단조 복귀해야 한다"
    assert path[-1] == pytest.approx(20.0, abs=0.5)


def test_환율_변화율은_소수를_돌려준다():
    assert change_pct(1315, 1302) == pytest.approx((1302 - 1315) / 1315)
    assert abs(change_pct(1315, 1302)) < 0.02
    with pytest.raises(ValueError):
        change_pct(0, 1300)


def test_표시_변화분은_스냅샷_주기로만_갱신된다():
    """매 틱 갱신하면 화면이 산만하다. 같은 주기 안에서는 기준점이 같아야 한다."""
    history = [float(i) for i in range(41)]
    anchors = {display_delta(history, t, 8) for t in range(24, 32)}
    assert len(anchors) == 8          # 값은 매 틱 달라지되
    d24, d31 = display_delta(history, 24, 8), display_delta(history, 31, 8)
    assert d31 - d24 == pytest.approx(7)   # 기준점이 고정이라 차이가 틱 수와 같다
