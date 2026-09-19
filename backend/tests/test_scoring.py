"""점수 계산 — 위험조정점수, MDD, 3종 순위."""

import pytest

from backend.scoring import (
    PlayerScore, PlayerTracker, ReturnStats, assign_ranks, leaderboard,
)


def _tracker(pid=1, name="가온이", start=10_000_000, join=0):
    return PlayerTracker(
        player_id=pid, nickname=name, join_tick=join, starting_cash=start,
        prev_asset=start, peak_asset=start,
    )


def test_표준편차가_0이면_위험조정점수는_0점이다():
    """스펙 11번이 명시한 규칙. 나눗셈이 터지는 대신 0점이다."""
    stats = ReturnStats()
    for _ in range(50):
        stats.push(0.01)          # 매 틱 똑같이 올랐다면 표준편차가 0
    assert stats.stdev == pytest.approx(0.0)
    assert stats.risk_adjusted == 0.0


def test_표본이_하나면_위험조정점수는_0점이다():
    stats = ReturnStats()
    stats.push(0.05)
    assert stats.risk_adjusted == 0.0


def test_온라인_누적이_일반_계산과_같다():
    import statistics
    values = [0.01, -0.02, 0.03, 0.005, -0.001, 0.02]
    stats = ReturnStats()
    for v in values:
        stats.push(v)
    assert stats.mean == pytest.approx(statistics.mean(values))
    assert stats.stdev == pytest.approx(statistics.pstdev(values))


def test_같은_평균이면_흔들림이_적은_쪽이_점수가_높다():
    steady, wild = ReturnStats(), ReturnStats()
    for _ in range(20):
        steady.push(0.01)
        steady.push(0.011)
    for _ in range(20):
        wild.push(0.06)
        wild.push(-0.039)
    assert steady.mean == pytest.approx(wild.mean, abs=1e-3)
    assert steady.risk_adjusted > wild.risk_adjusted


def test_MDD는_고점_대비_최대_낙폭이다():
    t = _tracker()
    for asset in (10_000_000, 12_000_000, 9_000_000, 11_000_000, 10_500_000):
        t.push_asset(asset)
    assert t.mdd == pytest.approx(1 - 9_000_000 / 12_000_000)


def test_계속_오르면_MDD는_0이다():
    t = _tracker()
    for asset in (10_000_000, 10_500_000, 11_000_000, 12_000_000):
        t.push_asset(asset)
    assert t.mdd == 0.0


def _score(pid, ret, risk, mdd, eligible=True, ticks=300):
    return PlayerScore(
        player_id=pid, nickname=f"P{pid}", join_tick=0, ticks_played=ticks,
        starting_cash=10_000_000, final_asset=int(10_000_000 * (1 + ret)),
        return_pct=ret, risk_adjusted=risk, mdd=mdd, eligible_for_risk=eligible,
    )


def test_수익률_순위는_높은_순이다():
    scores = assign_ranks([
        _score(1, 0.10, 0.5, 0.05), _score(2, 0.30, 0.1, 0.20),
        _score(3, -0.05, 0.9, 0.02),
    ])
    ranks = {s.player_id: s.rank_return for s in scores}
    assert ranks == {2: 1, 1: 2, 3: 3}


def test_종합은_두_순위의_평균으로_정해진다():
    scores = assign_ranks([
        _score(1, 0.30, 0.50, 0.20),   # 수익 1위, 위험 1위 -> 평균 1.0
        _score(2, 0.20, 0.30, 0.05),   # 수익 2위, 위험 2위 -> 평균 2.0
        _score(3, 0.10, 0.10, 0.01),   # 수익 3위, 위험 3위 -> 평균 3.0
    ])
    by_id = {s.player_id: s for s in scores}
    assert [by_id[i].rank_total for i in (1, 2, 3)] == [1, 2, 3]


def test_종합_동점은_MDD가_낮은_쪽이_앞선다():
    """수익 순위와 위험 순위가 서로 뒤집혀 평균이 전부 2.0 인 경우."""
    scores = assign_ranks([
        _score(1, 0.30, 0.10, 0.30),   # 수익 1위, 위험 3위
        _score(2, 0.10, 0.30, 0.05),   # 수익 2위, 위험 2위
        _score(3, 0.05, 0.50, 0.01),   # 수익 3위, 위험 1위
    ])
    by_id = {s.player_id: s for s in scores}
    assert by_id[3].rank_total == 1    # MDD 0.01 로 가장 낮다
    assert by_id[2].rank_total == 2    # MDD 0.05
    assert by_id[1].rank_total == 3    # MDD 0.30


def test_참여_틱이_모자라면_위험조정과_종합에서_빠진다():
    """중도 참가자가 표본 부족으로 순위를 뒤집는 것을 막는다(결정-09)."""
    scores = assign_ranks([
        _score(1, 0.10, 0.20, 0.10),
        _score(2, 0.50, 9.99, 0.01, eligible=False, ticks=12),
    ])
    by_id = {s.player_id: s for s in scores}
    assert by_id[2].rank_return == 1, "수익률 탭에는 그대로 들어간다"
    assert by_id[2].rank_risk == 0
    assert by_id[2].rank_total == 0
    assert by_id[1].rank_risk == 1


def test_순위_탭별로_집계_대상이_다르다():
    scores = assign_ranks([
        _score(1, 0.10, 0.20, 0.10),
        _score(2, 0.50, 9.99, 0.01, eligible=False, ticks=12),
    ])
    assert len(leaderboard(scores, "return")) == 2
    assert len(leaderboard(scores, "risk")) == 1
    assert len(leaderboard(scores, "total")) == 1
    with pytest.raises(ValueError):
        leaderboard(scores, "없는탭")


def test_참가자가_없으면_빈_목록이다():
    assert assign_ranks([]) == []


def test_중도_참가자의_수익률은_참가_시점_기준이다(session):
    """참가 전 시장 움직임은 성적에 반영되지 않아야 한다."""
    early = session.join("일찍이", "dev-early-0001")
    for _ in range(30):
        session.advance_tick()
    late = session.join("늦게", "dev-late-0001")
    assert late.starting_cash == session.scenario.starting_cash
    assert late.join_tick == 30
    assert late.tracker.prev_asset == session.scenario.starting_cash
    for _ in range(5):
        session.advance_tick()
    assert late.tracker.stats.count == 5
    assert early.tracker.stats.count == 35
