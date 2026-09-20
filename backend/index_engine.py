"""공포지수 / 환율 엔진 — 평균회귀(Ornstein-Uhlenbeck 계열).

구간마다 값 하나를 고정해 두고 구간이 바뀔 때만 점프시키면 화면에 몇 분 내내
같은 숫자가 떠 있다가 갑자기 튀어 죽은 숫자처럼 보인다. 그래서 두 지표 모두
매 틱 갱신하고, 목표값 자체도 구간 시작에서 끝을 잇는 완만한 램프로 보간한다.

    value[t] = clamp(value[t-1] + theta * (target[t] - value[t-1]) + noise)

theta 는 회귀 속도다. 크면 목표를 빨리 따라가고 작으면 느긋하게 흔들린다.
이벤트로 충격을 받으면 튀었다가 서서히 목표로 돌아오는 움직임이 여기서 나온다.
price_engine 과 마찬가지로 난수는 호출자가 주입한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FEAR_MIN = 0.0
FEAR_MAX = 100.0


@dataclass(frozen=True)
class Segment:
    """목표값 구간. target 은 이 구간이 '끝나는 시점'의 목표값이다."""

    from_tick: int
    to_tick: int
    target: float


def build_target_series(
    segments: list[Segment], total_ticks: int, initial: float
) -> list[float]:
    """틱 0..total_ticks 의 목표값 배열을 만든다.

    각 구간은 직전 구간의 target(첫 구간은 initial)에서 자기 target 까지
    선형으로 보간된다. 계단식 점프가 생기지 않는 이유가 이 앵커 방식이다.
    구간의 연속성·빈틈은 scenario_loader 가 로드 시점에 이미 검증한다.
    """
    if total_ticks <= 0:
        raise ValueError(f"total_ticks 는 양수여야 한다 ({total_ticks})")
    if not segments:
        raise ValueError("목표값 구간이 비어 있다")

    series = [float(initial)] * (total_ticks + 1)
    anchor = float(initial)

    for seg in segments:
        span = seg.to_tick - seg.from_tick
        if span <= 0:
            raise ValueError(
                f"구간의 from({seg.from_tick}) 은 to({seg.to_tick}) 보다 작아야 한다"
            )
        for tick in range(seg.from_tick + 1, min(seg.to_tick, total_ticks) + 1):
            ratio = (tick - seg.from_tick) / span
            series[tick] = anchor + (seg.target - anchor) * ratio
        anchor = float(seg.target)

    # 마지막 구간이 total_ticks 에 못 미치면 남은 구간은 마지막 목표값을 유지한다.
    last_to = segments[-1].to_tick
    if last_to < total_ticks:
        for tick in range(last_to + 1, total_ticks + 1):
            series[tick] = anchor

    return series


def step(prev: float, target: float, theta: float, noise: float) -> float:
    """평균회귀 한 스텝. 범위 제한이 없는 일반형(환율용)."""
    if not (0.0 <= theta <= 1.0):
        raise ValueError(f"theta 는 0 이상 1 이하여야 한다 ({theta})")
    value = prev + theta * (target - prev) + noise
    if not math.isfinite(value):
        raise ValueError(f"평균회귀 결과가 NaN/inf 다 (prev={prev}, target={target})")
    return value


def step_fear(prev: float, target: float, theta: float, noise: float) -> float:
    """공포지수 한 스텝. 0~100 을 벗어나지 않도록 clamp 한다."""
    value = step(prev, target, theta, noise)
    return min(FEAR_MAX, max(FEAR_MIN, value))


def change_pct(prev: float, current: float) -> float:
    """환율 변화율. price_engine 의 fx_change_pct 로 넘어가는 '소수' 값이다(결정-05).

    퍼센트 숫자(-0.09)가 아니라 소수(-0.0009)다. 이 둘을 헷갈리면 fx_exposure 0.8
    종목이 틱당 ±7% 흔들려 게임이 붕괴한다.
    """
    if prev == 0:
        raise ValueError("직전 환율이 0 이라 변화율을 계산할 수 없다")
    return (current - prev) / prev


def display_delta(history: list[float], tick: int, interval: int) -> float:
    """화면에 보여줄 변화분(▲4 / ▼6). 계산값과 분리해 스냅샷 주기로만 갱신한다.

    매 틱 갱신하면 숫자가 산만해서 읽히지 않는다. 계산용 원본값은 매 틱 갱신되지만
    화면 화살표만 interval 틱 전 값과 비교한다.
    """
    if interval <= 0:
        raise ValueError(f"스냅샷 주기는 양수여야 한다 ({interval})")
    anchor_tick = max(0, (tick // interval) * interval - interval)
    if tick >= len(history):
        raise IndexError(f"틱 {tick} 이 기록 범위를 벗어난다")
    return history[tick] - history[anchor_tick]
