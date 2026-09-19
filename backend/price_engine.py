"""가격 엔진.

FastAPI, DB, WebSocket, 시각, 난수에 전혀 의존하지 않는 순수 계산 모듈이다.
난수(noise)조차 호출자가 시드를 고정한 Random 으로 뽑아 주입한다. 엔진 안에서
gauss 를 직접 호출하면 같은 입력이 같은 출력을 보장하지 못해 불변 조건을
단위테스트로 강제할 수 없기 때문이다.

틱 단위 계산 순서는 스펙 2번을 그대로 따른다.
    1) base_drift  = 시나리오 목표 변동률 + 주입된 가우시안 노이즈
    2) event_delta = 활성 뉴스 이벤트의 해당 틱 기여분 합
    3) fear_shock  = -beta * fear_sensitivity * (공포지수 변화 / 100)
    4) fx_shock    = fx_exposure * fx_sensitivity * 환율 변화율
    5) total_script_delta = 1 + 2 + 3 + 4
    6) f    = clip((매수수량 - 매도수량) / L, -1, 1)
    7) damp = min(resistance_coef * |f|, dampening_cap)
    8) actual_delta = total_script_delta * (1 - damp)
    9) new_price = max(round(prev_price * (1 + actual_delta)), 최저가)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_PRICE_DEFAULT = 100


@dataclass(frozen=True)
class TickInput:
    """한 종목의 한 틱 계산에 필요한 모든 입력. 전부 호출자가 준비한다."""

    symbol: str
    prev_price: int
    liquidity: int              # L_i
    beta: float
    fx_exposure: float
    scenario_drift: float       # 시나리오가 정한 해당 틱 목표 변동률
    noise: float                # 호출자가 주입한 gauss(0, noise_sigma) 표본
    event_delta: float          # impact_delay 경과분만 합산된 이벤트 기여
    fear_change: float          # fear_index[t] - fear_index[t-1], 포인트 단위
    fx_change_pct: float        # (fx[t] - fx[t-1]) / fx[t-1], 소수 (결정-05)
    buy_qty: int
    sell_qty: int
    resistance_coef: float
    dampening_cap: float
    fear_sensitivity: float = 1.0   # 결정-06. 1.0 이면 스펙 원식과 동일
    fx_sensitivity: float = 1.0     # 결정-06. 1.0 이면 스펙 원식과 동일
    min_price: int = MIN_PRICE_DEFAULT


@dataclass(frozen=True)
class TickResult:
    """tick_log 에 그대로 기록되는 계산 결과."""

    symbol: str
    base_drift: float
    event_delta: float
    fear_shock: float
    fx_shock: float
    total_script_delta: float
    f: float
    damp: float
    actual_delta: float
    price: int
    clamped: bool               # 최저가 하한에 걸려 가격이 보정되었는지


def _require_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} 값이 NaN 또는 inf 다: {value!r}")
    return value


def _clip(value: float, low: float, high: float) -> float:
    return low if value < low else (high if value > high else value)


def compute_tick(inp: TickInput) -> TickResult:
    """한 종목의 한 틱 가격을 계산한다. 부작용 없음, 입력이 같으면 출력도 같다."""

    # L_i == 0 은 스펙이 명시적으로 요구한 예외다. 음수 유동성도 같은 이유로 막는다.
    if inp.liquidity == 0:
        raise ValueError(f"{inp.symbol}: 유동성 L_i 가 0 이라 f 를 계산할 수 없다")
    if inp.liquidity < 0:
        raise ValueError(f"{inp.symbol}: 유동성 L_i 는 음수일 수 없다 ({inp.liquidity})")
    if inp.prev_price <= 0:
        raise ValueError(f"{inp.symbol}: 직전 가격은 양수여야 한다 ({inp.prev_price})")
    if inp.min_price <= 0:
        raise ValueError(f"{inp.symbol}: 최저가는 양수여야 한다 ({inp.min_price})")
    if inp.buy_qty < 0 or inp.sell_qty < 0:
        raise ValueError(f"{inp.symbol}: 체결 수량은 음수일 수 없다")
    # dampening_cap 이 1 이상이면 (1 - damp) 가 0 이하가 되어 방향 보존 불변 조건이
    # 깨진다. 시나리오 로더가 먼저 거부하지만, 엔진 자체로도 성립을 보장한다.
    if not (0.0 <= inp.dampening_cap < 1.0):
        raise ValueError(
            f"{inp.symbol}: dampening_cap 은 0 이상 1 미만이어야 한다 ({inp.dampening_cap})"
        )
    if inp.resistance_coef < 0:
        raise ValueError(f"{inp.symbol}: resistance_coef 는 음수일 수 없다")

    base_drift = _require_finite(
        "base_drift", inp.scenario_drift + inp.noise
    )
    event_delta = _require_finite("event_delta", inp.event_delta)
    fear_shock = _require_finite(
        "fear_shock", -inp.beta * inp.fear_sensitivity * (inp.fear_change / 100.0)
    )
    fx_shock = _require_finite(
        "fx_shock", inp.fx_exposure * inp.fx_sensitivity * inp.fx_change_pct
    )

    total_script_delta = _require_finite(
        "total_script_delta", base_drift + event_delta + fear_shock + fx_shock
    )

    f = _clip((inp.buy_qty - inp.sell_qty) / inp.liquidity, -1.0, 1.0)
    damp = min(inp.resistance_coef * abs(f), inp.dampening_cap)

    actual_delta = _require_finite(
        "actual_delta", total_script_delta * (1.0 - damp)
    )

    raw_price = round(inp.prev_price * (1.0 + actual_delta))
    _require_finite("raw_price", raw_price)
    price = max(int(raw_price), inp.min_price)
    clamped = price != int(raw_price)

    return TickResult(
        symbol=inp.symbol,
        base_drift=base_drift,
        event_delta=event_delta,
        fear_shock=fear_shock,
        fx_shock=fx_shock,
        total_script_delta=total_script_delta,
        f=f,
        damp=damp,
        actual_delta=actual_delta,
        price=price,
        clamped=clamped,
    )
