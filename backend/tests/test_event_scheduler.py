"""뉴스 지연 타이밍과 공매도 2단계 펌프-덤프 불변 조건."""

import random

import pytest

from backend.event_scheduler import (
    PHASE_DUMP, PHASE_PENDING, PHASE_PUMP, STATE_APPLYING, STATE_DONE,
    STATE_PENDING, EventScheduler,
)
from backend.scenario_loader import load_scenario

SCENARIO = "scenarios/festival_01.json"


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(SCENARIO)


def _schedulers(scenario, count=200):
    for seed in range(count):
        yield seed, EventScheduler(scenario, random.Random(seed))


# ---------- 공매도 집중 불변 조건 ----------

def test_펌프_총합은_항상_덤프_총합보다_작다(scenario):
    """경고가 거짓말이 되면 안 된다. 스펙 5번 필수 제약."""
    for seed, sch in _schedulers(scenario):
        for e in sch.events:
            if not e.is_short_pressure:
                continue
            assert e.pump_total < e.dump_total, f"seed={seed} {e.key}"


def test_복리_순효과는_항상_손실이다(scenario):
    """산술 합만으로는 실제 손실이 보장되지 않는다. 배율로 직접 확인한다."""
    for seed, sch in _schedulers(scenario):
        for e in sch.events:
            if e.is_short_pressure:
                assert e.net_multiplier() < 1.0, f"seed={seed} {e.key}"


def test_펌프_구간_상승이_눈에_띄는_수준이다(scenario):
    """미끼가 보이지 않으면 함정이 작동하지 않는다(자가 검증 (6) 과 같은 기준)."""
    floor = scenario.params.min_visible_pump
    for seed, sch in _schedulers(scenario):
        for e in sch.events:
            if not e.is_short_pressure:
                continue
            mult = 1.0
            for d in e.deltas[: e.pump_ticks]:
                mult *= 1.0 + d
            assert mult >= floor, f"seed={seed} {e.key} 펌프 배율 {mult:.4f}"


def test_펌프는_전부_상승_덤프는_전부_하락이다(scenario):
    for _, sch in _schedulers(scenario, 50):
        for e in sch.events:
            if not e.is_short_pressure:
                continue
            assert all(d > 0 for d in e.deltas[: e.pump_ticks])
            assert all(d < 0 for d in e.deltas[e.pump_ticks:])


def test_펌프는_가속하고_덤프는_초반이_가파르다(scenario):
    """미끼는 서서히 매력적으로, 함정은 빠져나갈 틈 없이."""
    sch = EventScheduler(scenario, random.Random(0))
    for e in sch.events:
        if not e.is_short_pressure:
            continue
        pump = list(e.deltas[: e.pump_ticks])
        dump = [abs(d) for d in e.deltas[e.pump_ticks:]]
        assert pump == sorted(pump)
        assert dump == sorted(dump, reverse=True)


def test_단계_판정이_구간과_일치한다(scenario):
    sch = EventScheduler(scenario, random.Random(3))
    e = next(x for x in sch.events if x.is_short_pressure)
    assert e.phase_at(e.headline_tick) == PHASE_PENDING
    assert e.phase_at(e.impact_start_tick) == PHASE_PUMP
    assert e.phase_at(e.impact_start_tick + e.pump_ticks - 1) == PHASE_PUMP
    assert e.phase_at(e.impact_start_tick + e.pump_ticks) == PHASE_DUMP
    assert e.phase_at(e.impact_end_tick) == PHASE_DUMP


def test_공매도_배지는_펌프_구간에도_계속_노출된다(scenario):
    """펌프 중에 배지가 사라지면 '지금 사도 되나' 라는 잘못된 신호가 된다."""
    sch = EventScheduler(scenario, random.Random(5))
    e = next(x for x in sch.events if x.is_short_pressure)
    for tick in range(e.headline_tick, e.impact_end_tick + 1):
        badges = sch.active_badges(tick)
        assert e.symbol in badges
        assert badges[e.symbol]["label"] == "공매도 집중 과열 · 급등 후 급락 위험"
    assert e.symbol not in sch.active_badges(e.impact_end_tick + 1)


def test_공매도는_섹터가_아니라_종목_하나를_지정한다(scenario):
    sch = EventScheduler(scenario, random.Random(6))
    for e in sch.events:
        if e.is_short_pressure:
            assert e.sector is None
            assert len(e.targets) == 1


# ---------- 뉴스 지연 타이밍 ----------

def test_반영_지연_전에는_가격에_아무_영향이_없다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    for e in sch.events:
        for tick in range(e.headline_tick, e.impact_start_tick):
            for symbol in e.targets:
                contribution = sum(
                    o.deltas[tick - o.impact_start_tick]
                    for o in sch.events
                    if o.key == e.key and o.impact_start_tick <= tick <= o.impact_end_tick
                )
                assert contribution == 0.0


def test_반영_시작_틱부터_영향이_들어온다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    for e in sch.events:
        assert e.deltas[0] != 0.0
        assert len(e.deltas) == e.impact_end_tick - e.impact_start_tick + 1


def test_분산_적용된_델타의_합이_원래_델타와_같다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    by_key = {e.key: e for e in sch.events}
    for spec in scenario.normal_events:
        assert sum(by_key[spec.key].deltas) == pytest.approx(spec.delta)


def test_반영_상태_배지가_3단계를_모두_거친다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    for e in sch.events:
        assert e.state_at(e.headline_tick) == STATE_PENDING
        assert e.state_at(e.impact_start_tick) == STATE_APPLYING
        assert e.state_at(e.impact_end_tick) == STATE_APPLYING
        assert e.state_at(e.impact_end_tick + 1) == STATE_DONE
        # '반영중' 구간이 0틱이면 3단계 배지가 성립하지 않는다(결정-02 가 필요한 이유).
        assert e.impact_end_tick >= e.impact_start_tick


def test_헤드라인은_최초_노출_틱에만_뜬다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    shown = [e.key for tick in range(scenario.total_ticks + 1)
             for e in sch.headlines_at(tick)]
    assert len(shown) == len(set(shown))


def test_연쇄_이벤트는_헤드라인으로_뜨지_않는다(scenario):
    """간접형의 연쇄분은 가격에는 반영되지만 화면에는 뜨지 않는다."""
    sch = EventScheduler(scenario, random.Random(0))
    shown = {e.key for tick in range(scenario.total_ticks + 1)
             for e in sch.headlines_at(tick)}
    assert "EV03B" not in shown
    assert "EV03" in shown
    assert sch.delta_for(176 + 12, "A004") < 0, "연쇄분은 가격에 반영되어야 한다"


def test_섹터_이벤트는_그_섹터_종목에만_걸린다(scenario):
    sch = EventScheduler(scenario, random.Random(0))
    tick = 96 + 8
    assert sch.delta_for(tick, "A001") < 0
    for other in ("A002", "A003", "A004", "A005", "A006", "A007"):
        assert sch.delta_for(tick, other) == 0.0


def test_헤드라인_간격이_기준_안에_있다(scenario):
    gaps = EventScheduler(scenario, random.Random(0)).headline_gaps_seconds()
    assert gaps, "헤드라인이 2개 이상 있어야 간격을 잴 수 있다"
    assert min(gaps) >= 90.0
    assert max(gaps) <= 420.0


def test_초반_90틱에는_헤드라인이_없다(scenario):
    """참가자가 화면과 종목을 파악하는 구간이다."""
    sch = EventScheduler(scenario, random.Random(0))
    for tick in range(0, 90):
        assert sch.headlines_at(tick) == []
