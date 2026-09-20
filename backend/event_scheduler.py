"""뉴스 / 공매도 집중 이벤트 스케줄러.

핵심 방침은 사전 전개(precompute)다. 세션 시작 시 시드 하나로 모든 이벤트의
틱별 delta 배열을 확정한다. 그래야
  (가) 펌프 < 덤프 불변 조건을 게임이 시작되기 전에 검증할 수 있고
  (나) 자가 검증이 375틱을 다 돌리지 않고 스케줄만 검사할 수 있으며
  (다) 서버가 재시작돼도 같은 스케줄이 복원된다.

price_engine 의 계산식은 이 모듈 때문에 바뀌지 않는다. 스케줄러는 그 틱의
event_delta 값 하나를 넘겨줄 뿐이다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from backend.models import NormalEventSpec, Scenario, ShortPressureSpec

# 공매도 집중 이벤트의 단계 구분
PHASE_PENDING = "pending"
PHASE_PUMP = "pump"
PHASE_DUMP = "dump"
PHASE_DONE = "done"

# 뉴스 반영 상태 3단계 배지 (스펙 4번)
STATE_PENDING = "pending"      # 반영 대기
STATE_APPLYING = "applying"    # 반영중
STATE_DONE = "done"            # 반영 완료


@dataclass(frozen=True)
class ScheduledEvent:
    """사전 전개가 끝난 이벤트 하나. events_fired 테이블에 그대로 저장된다."""

    key: str
    type: str                       # normal | short_pressure
    headline: str
    body: str
    headline_tick: int
    impact_start_tick: int
    impact_end_tick: int
    difficulty: str | None
    sector: str | None
    symbol: str | None
    targets: tuple[str, ...]        # 실제로 영향을 받는 종목들
    deltas: tuple[float, ...]       # impact_start 부터 틱 순서대로
    is_short_pressure: bool = False
    pump_ticks: int = 0
    dump_ticks: int = 0
    pump_total: float = 0.0
    dump_total: float = 0.0
    badge: str | None = None

    def state_at(self, tick: int) -> str:
        if tick < self.impact_start_tick:
            return STATE_PENDING
        if tick <= self.impact_end_tick:
            return STATE_APPLYING
        return STATE_DONE

    def phase_at(self, tick: int) -> str:
        """공매도 집중 전용. 일반 이벤트는 호출하지 않는다."""
        if not self.is_short_pressure:
            raise ValueError("공매도 집중 이벤트가 아니다")
        if tick < self.impact_start_tick:
            return PHASE_PENDING
        if tick < self.impact_start_tick + self.pump_ticks:
            return PHASE_PUMP
        if tick <= self.impact_end_tick:
            return PHASE_DUMP
        return PHASE_DONE

    def net_multiplier(self) -> float:
        """펌프+덤프를 모두 거친 뒤의 누적 배율. 1 보다 작아야 한다."""
        result = 1.0
        for d in self.deltas:
            result *= 1.0 + d
        return result


def _decay_weights(n: int) -> tuple[float, ...]:
    """앞이 큰 지수 감쇠형. 정보 반영은 초기 충격이 크고 꼬리가 길다."""
    tau = max(1.0, n / 2.0)
    raw = [math.exp(-k / tau) for k in range(n)]
    total = sum(raw)
    return tuple(w / total for w in raw)


def _ramp_weights(n: int) -> tuple[float, ...]:
    """뒤로 갈수록 커지는 가속형. 미끼가 서서히 매력적으로 보여야 함정이 작동한다."""
    raw = [float(k + 1) for k in range(n)]
    total = sum(raw)
    return tuple(w / total for w in raw)


def _distribute(total: float, weights: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(total * w for w in weights)


class ScheduleError(ValueError):
    """불변 조건을 만족하는 스케줄을 만들지 못한 경우."""


class EventScheduler:
    """시나리오의 이벤트 전체를 틱별 delta 로 전개해 들고 있는다."""

    def __init__(self, scenario: Scenario, rng: random.Random) -> None:
        self.scenario = scenario
        self.events: list[ScheduledEvent] = []
        self._by_tick: dict[int, dict[str, float]] = {}

        for spec in scenario.normal_events:
            self._add(self._build_normal(spec, scenario))
        for spec in scenario.short_events:
            self._add(self._build_short(spec, scenario, rng))

        self.events.sort(key=lambda e: (e.headline_tick, e.key))

    # ---------- 조회 API ----------

    def delta_for(self, tick: int, symbol: str) -> float:
        return self._by_tick.get(tick, {}).get(symbol, 0.0)

    def deltas_at(self, tick: int) -> dict[str, float]:
        return dict(self._by_tick.get(tick, {}))

    def headlines_at(self, tick: int) -> list[ScheduledEvent]:
        """화면에 새로 띄울 헤드라인. 연쇄 효과용 무명 이벤트는 제외한다."""
        return [
            e for e in self.events
            if e.headline_tick == tick and e.headline.strip()
        ]

    def active_badges(self, tick: int) -> dict[str, dict[str, str]]:
        """공매도 집중 배지. 헤드라인 노출부터 덤프 종료까지 경고 톤을 유지한다."""
        badges: dict[str, dict[str, str]] = {}
        for e in self.events:
            if not e.is_short_pressure:
                continue
            if e.headline_tick <= tick <= e.impact_end_tick:
                badges[e.symbol] = {
                    "kind": "short_pressure",
                    "phase": e.phase_at(tick),
                    "label": e.badge or "",
                    "event_key": e.key,
                }
        return badges

    def headline_gaps_seconds(self) -> list[float]:
        """인접 헤드라인 간격(초). 자가 검증 (5) 항목이 쓴다."""
        ticks = sorted(
            e.headline_tick for e in self.events if e.headline.strip()
        )
        return [
            (b - a) * self.scenario.tick_seconds for a, b in zip(ticks, ticks[1:])
        ]

    # ---------- 전개 ----------

    def _add(self, event: ScheduledEvent) -> None:
        self.events.append(event)
        for offset, delta in enumerate(event.deltas):
            tick = event.impact_start_tick + offset
            bucket = self._by_tick.setdefault(tick, {})
            for symbol in event.targets:
                bucket[symbol] = bucket.get(symbol, 0.0) + delta

    def _build_normal(
        self, spec: NormalEventSpec, scenario: Scenario
    ) -> ScheduledEvent:
        if spec.symbol:
            targets = (spec.symbol,)
        elif spec.sector:
            targets = scenario.symbols_in_sector(spec.sector)
        else:
            raise ScheduleError(f"{spec.key}: sector 나 symbol 중 하나는 있어야 한다")
        if not targets:
            raise ScheduleError(f"{spec.key}: 영향받는 종목이 없다 (sector={spec.sector})")

        start = spec.tick + spec.impact_delay_ticks
        deltas = _distribute(spec.delta, _decay_weights(spec.spread_ticks))
        return ScheduledEvent(
            key=spec.key,
            type="normal",
            headline=spec.headline,
            body=spec.body,
            headline_tick=spec.tick,
            impact_start_tick=start,
            impact_end_tick=start + spec.spread_ticks - 1,
            difficulty=spec.difficulty,
            sector=spec.sector,
            symbol=spec.symbol,
            targets=targets,
            deltas=deltas,
        )

    def _build_short(
        self, spec: ShortPressureSpec, scenario: Scenario, rng: random.Random
    ) -> ScheduledEvent:
        params = scenario.params
        scenario.stock(spec.symbol)  # 없는 종목이면 여기서 KeyError

        pump_total = rng.uniform(spec.pump.min_total, spec.pump.max_total)
        # 미끼가 눈에 띄지 않으면 함정이 작동하지 않는다(자가 검증 (6) 와 같은 기준).
        pump_total = max(pump_total, params.min_visible_pump - 1.0)

        dump_total = rng.uniform(spec.dump.min_total, spec.dump.max_total)
        # 스펙의 샘플링 범위는 펌프 0.08~0.15, 덤프 0.15~0.25 로 경계에서 겹친다.
        # 둘 다 0.15 가 뽑히면 '펌프 총합 < 덤프 총합' 이 등호가 되어 조건이 깨지므로
        # 여유를 강제한다(결정-01).
        dump_total = max(dump_total, pump_total * (1.0 + params.net_loss_margin))

        pump_deltas = _distribute(pump_total, _ramp_weights(spec.pump.ticks))
        dump_deltas = _distribute(-dump_total, _decay_weights(spec.dump.ticks))

        # 산술 합 조건만으로는 복리 순효과가 손실이라는 보장이 없다. 실제 배율이
        # 1 미만이 될 때까지 덤프를 키운다. 경고가 거짓말이 되면 안 된다.
        for _ in range(200):
            multiplier = 1.0
            for d in pump_deltas + dump_deltas:
                multiplier *= 1.0 + d
            if multiplier < 1.0:
                break
            dump_total *= 1.02
            dump_deltas = _distribute(-dump_total, _decay_weights(spec.dump.ticks))
        else:
            raise ScheduleError(
                f"{spec.key}: 복리 순효과를 손실로 만들지 못했다 "
                f"(pump={pump_total:.4f}, dump={dump_total:.4f})"
            )

        start = spec.tick + spec.impact_delay_ticks
        deltas = pump_deltas + dump_deltas
        return ScheduledEvent(
            key=spec.key,
            type="short_pressure",
            headline=spec.headline,
            body=spec.body,
            headline_tick=spec.tick,
            impact_start_tick=start,
            impact_end_tick=start + len(deltas) - 1,
            difficulty=None,
            sector=None,
            symbol=spec.symbol,
            targets=(spec.symbol,),
            deltas=deltas,
            is_short_pressure=True,
            pump_ticks=spec.pump.ticks,
            dump_ticks=spec.dump.ticks,
            pump_total=pump_total,
            dump_total=dump_total,
            badge=spec.badge,
        )
