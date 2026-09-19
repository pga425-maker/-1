"""시나리오 로더 — scenarios/*.json 을 스캔·검증·파싱한다.

목적은 내년에 새 시나리오를 추가할 때 코드를 고치지 않고 파일만 넣으면 되게 하는
것이다. 그래서 폴더의 모든 .json 을 자동 인식하고, 잘못된 파일은 게임이 시작되기
전에 걸러낸다.

검증을 엔진이 아니라 여기서 하는 이유는 엔진을 순수하게 유지하고, 파일이 틀렸을 때
행사 당일 375틱을 돌리다가 터지는 대신 관리자 화면에서 바로 보이게 하기 위해서다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.index_engine import Segment
from backend.models import (
    DIFFICULTIES,
    NARRATIVES,
    DriftSegment,
    IndexSpec,
    NormalEventSpec,
    Params,
    PhaseSpec,
    Scenario,
    ShortPressureSpec,
    StockSpec,
)

# 자가 검증 (5) 와 같은 기준. 여기서는 경고만 내고 막지는 않는다.
MIN_HEADLINE_GAP_SEC = 90.0
MAX_HEADLINE_GAP_SEC = 420.0
QUIET_OPENING_TICKS = 90       # 스펙 4번: 0~90틱은 헤드라인 없음


class ScenarioError(ValueError):
    """시나리오 파일이 규칙을 위반했다. 게임을 시작할 수 없다."""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ScenarioError(message)


def _check_contiguous(
    raw: list[dict], total_ticks: int, label: str
) -> None:
    _require(bool(raw), f"{label}: 구간이 비어 있다")
    expect = 0
    for seg in raw:
        _require(
            "from" in seg and "to" in seg,
            f"{label}: 구간에 from/to 가 필요하다 ({seg})",
        )
        _require(
            seg["from"] == expect,
            f"{label}: 구간이 {expect} 에서 시작해야 하는데 {seg['from']} 이다 (빈틈 또는 겹침)",
        )
        _require(
            seg["to"] > seg["from"],
            f"{label}: from({seg['from']}) 은 to({seg['to']}) 보다 작아야 한다",
        )
        expect = seg["to"]
    _require(
        expect == total_ticks,
        f"{label}: 마지막 구간이 {expect} 에서 끝난다. total_ticks({total_ticks}) 와 맞아야 한다",
    )


def _parse_params(raw: dict) -> Params:
    p = Params(**{k: v for k, v in raw.items() if k in Params.__dataclass_fields__})
    unknown = set(raw) - set(Params.__dataclass_fields__)
    _require(not unknown, f"params 에 모르는 항목이 있다: {sorted(unknown)}")

    # dampening_cap 이 1 이상이면 (1 - damp) 가 0 이하가 되어 가격 방향이 뒤집힌다.
    # price_engine 의 불변 조건 1·2 가 성립하려면 여기서 막아야 한다.
    _require(
        0.0 <= p.dampening_cap < 1.0,
        f"dampening_cap 은 0 이상 1 미만이어야 한다 ({p.dampening_cap})",
    )
    _require(p.resistance_coef >= 0, f"resistance_coef 는 음수일 수 없다 ({p.resistance_coef})")
    _require(p.noise_sigma >= 0, f"noise_sigma 는 음수일 수 없다 ({p.noise_sigma})")
    _require(p.min_price > 0, f"min_price 는 양수여야 한다 ({p.min_price})")
    _require(p.fee_rate >= 0, f"fee_rate 는 음수일 수 없다 ({p.fee_rate})")
    _require(
        0 < p.max_position_ratio <= 1,
        f"max_position_ratio 는 0 초과 1 이하여야 한다 ({p.max_position_ratio})",
    )
    _require(p.net_loss_margin >= 0, "net_loss_margin 은 음수일 수 없다")
    _require(p.min_visible_pump >= 1.0, "min_visible_pump 는 1.0 이상이어야 한다")
    _require(p.snapshot_interval_ticks > 0, "snapshot_interval_ticks 는 양수여야 한다")
    _require(p.min_ticks_for_risk_rank >= 0, "min_ticks_for_risk_rank 는 음수일 수 없다")
    return p


def _parse_index(raw: dict, total_ticks: int, label: str) -> IndexSpec:
    for key in ("initial", "theta", "noise_sigma", "segments"):
        _require(key in raw, f"{label}: {key} 가 없다")
    _require(0.0 <= raw["theta"] <= 1.0, f"{label}: theta 는 0~1 이어야 한다 ({raw['theta']})")
    _require(raw["noise_sigma"] >= 0, f"{label}: noise_sigma 는 음수일 수 없다")
    _check_contiguous(raw["segments"], total_ticks, f"{label}.segments")
    segments = tuple(
        Segment(from_tick=s["from"], to_tick=s["to"], target=float(s["target"]))
        for s in raw["segments"]
    )
    return IndexSpec(
        initial=float(raw["initial"]),
        theta=float(raw["theta"]),
        noise_sigma=float(raw["noise_sigma"]),
        segments=segments,
    )


def _parse_stock(raw: dict, total_ticks: int) -> StockSpec:
    for key in (
        "symbol", "name", "sector", "initial_price", "liquidity",
        "beta", "fx_exposure", "narrative", "avatar_color",
    ):
        _require(key in raw, f"종목 정의에 {key} 가 없다: {raw.get('symbol', raw)}")
    sym = raw["symbol"]
    _require(raw["liquidity"] > 0, f"{sym}: liquidity 는 양수여야 한다 (0 이면 f 계산 불가)")
    _require(raw["initial_price"] > 0, f"{sym}: initial_price 는 양수여야 한다")
    _require(
        raw["narrative"] in NARRATIVES,
        f"{sym}: 모르는 narrative 다 ({raw['narrative']}). 가능한 값 {NARRATIVES}",
    )
    noise_sigma = raw.get("noise_sigma")
    if noise_sigma is not None:
        _require(noise_sigma >= 0, f"{sym}: noise_sigma 는 음수일 수 없다")
    _check_contiguous(raw.get("drift_segments", []), total_ticks, f"{sym}.drift_segments")

    return StockSpec(
        symbol=sym,
        name=raw["name"],
        sector=raw["sector"],
        initial_price=int(raw["initial_price"]),
        liquidity=int(raw["liquidity"]),
        beta=float(raw["beta"]),
        fx_exposure=float(raw["fx_exposure"]),
        narrative=raw["narrative"],
        avatar_color=raw["avatar_color"],
        short_pressure_weight=float(raw.get("short_pressure_weight", 0.0)),
        noise_sigma=None if noise_sigma is None else float(noise_sigma),
        drift_segments=tuple(
            DriftSegment(from_tick=s["from"], to_tick=s["to"], drift=float(s["drift"]))
            for s in raw.get("drift_segments", [])
        ),
    )


def _parse_events(
    raw_events: list[dict], stocks: tuple[StockSpec, ...], total_ticks: int
) -> tuple[tuple[NormalEventSpec, ...], tuple[ShortPressureSpec, ...]]:
    symbols = {s.symbol for s in stocks}
    sectors = {s.sector for s in stocks}
    normals: list[NormalEventSpec] = []
    shorts: list[ShortPressureSpec] = []
    seen_keys: set[str] = set()

    for idx, raw in enumerate(raw_events):
        key = raw.get("key") or f"EV{idx:02d}"
        _require(key not in seen_keys, f"이벤트 key 가 중복이다: {key}")
        seen_keys.add(key)
        _require("tick" in raw, f"{key}: tick 이 없다")
        tick = int(raw["tick"])
        _require(0 <= tick < total_ticks, f"{key}: tick 이 범위를 벗어난다 ({tick})")
        delay = int(raw.get("impact_delay_ticks", 0))
        _require(delay >= 0, f"{key}: impact_delay_ticks 는 음수일 수 없다")
        etype = raw.get("type", "normal")

        if etype == "normal":
            _require("delta" in raw, f"{key}: delta 가 없다")
            spread = int(raw.get("spread_ticks", 6))
            _require(spread >= 1, f"{key}: spread_ticks 는 1 이상이어야 한다")
            end = tick + delay + spread - 1
            _require(
                end < total_ticks,
                f"{key}: 반영 종료 틱({end})이 total_ticks({total_ticks}) 를 넘는다",
            )
            difficulty = raw.get("difficulty", "direct")
            _require(
                difficulty in DIFFICULTIES,
                f"{key}: 모르는 difficulty 다 ({difficulty})",
            )
            sector = raw.get("sector")
            symbol = raw.get("symbol")
            _require(
                bool(sector) != bool(symbol),
                f"{key}: sector 와 symbol 중 정확히 하나만 지정해야 한다",
            )
            if sector:
                _require(sector in sectors, f"{key}: 없는 섹터다 ({sector})")
            if symbol:
                _require(symbol in symbols, f"{key}: 없는 종목이다 ({symbol})")
            normals.append(
                NormalEventSpec(
                    key=key, tick=tick, delta=float(raw["delta"]),
                    impact_delay_ticks=delay, spread_ticks=spread,
                    difficulty=difficulty, headline=raw.get("headline", ""),
                    body=raw.get("body", ""), sector=sector, symbol=symbol,
                )
            )

        elif etype == "short_pressure":
            symbol = raw.get("symbol")
            _require(symbol in symbols, f"{key}: 없는 종목이다 ({symbol})")
            for phase in ("pump", "dump"):
                _require(phase in raw, f"{key}: {phase} 정의가 없다")
                block = raw[phase]
                for field_name in ("ticks", "min", "max"):
                    _require(field_name in block, f"{key}.{phase}: {field_name} 가 없다")
                _require(block["ticks"] >= 1, f"{key}.{phase}: ticks 는 1 이상이어야 한다")
                _require(block["min"] > 0, f"{key}.{phase}: min 은 양수여야 한다")
                _require(
                    block["min"] <= block["max"],
                    f"{key}.{phase}: min 이 max 보다 크다",
                )
            end = tick + delay + raw["pump"]["ticks"] + raw["dump"]["ticks"] - 1
            _require(
                end < total_ticks,
                f"{key}: 덤프 종료 틱({end})이 total_ticks({total_ticks}) 를 넘는다",
            )
            shorts.append(
                ShortPressureSpec(
                    key=key, tick=tick, symbol=symbol, impact_delay_ticks=delay,
                    pump=PhaseSpec(
                        ticks=int(raw["pump"]["ticks"]),
                        min_total=float(raw["pump"]["min"]),
                        max_total=float(raw["pump"]["max"]),
                    ),
                    dump=PhaseSpec(
                        ticks=int(raw["dump"]["ticks"]),
                        min_total=float(raw["dump"]["min"]),
                        max_total=float(raw["dump"]["max"]),
                    ),
                    headline=raw.get("headline", ""),
                )
            )
        else:
            raise ScenarioError(f"{key}: 모르는 이벤트 type 이다 ({etype})")

    return tuple(normals), tuple(shorts)


def _collect_warnings(
    normals: tuple[NormalEventSpec, ...],
    shorts: tuple[ShortPressureSpec, ...],
    tick_seconds: float,
) -> tuple[str, ...]:
    """치명적이지 않지만 설계 의도와 어긋나는 것들. 자가 검증이 다시 본다."""
    warnings: list[str] = []
    # 간접형 뉴스는 헤드라인 하나가 다른 섹터까지 연쇄로 번지는 형태다. 그 연쇄분은
    # headline 이 빈 이벤트로 같은 틱에 얹는다. 화면에 뜨지 않으므로 헤드라인 간격과
    # 난이도 집계에서는 제외해야 한다.
    headline_normals = tuple(e for e in normals if e.headline.strip())
    ticks = sorted([e.tick for e in headline_normals] + [e.tick for e in shorts])

    early = [t for t in ticks if t < QUIET_OPENING_TICKS]
    if early:
        warnings.append(
            f"0~{QUIET_OPENING_TICKS}틱은 헤드라인이 없어야 하는데 {early} 에 있다"
        )

    for a, b in zip(ticks, ticks[1:]):
        gap = (b - a) * tick_seconds
        if gap < MIN_HEADLINE_GAP_SEC:
            warnings.append(f"헤드라인 간격이 좁다: {a}틱 -> {b}틱 ({gap:.0f}초)")
        elif gap > MAX_HEADLINE_GAP_SEC:
            warnings.append(f"헤드라인 간격이 넓다: {a}틱 -> {b}틱 ({gap:.0f}초)")

    counts = {d: sum(1 for e in headline_normals if e.difficulty == d) for d in DIFFICULTIES}
    if counts["direct"] < 3:
        warnings.append(f"직접형 뉴스가 3개 미만이다 ({counts['direct']}개)")
    if counts["indirect"] < 2:
        warnings.append(f"간접형 뉴스가 2개 미만이다 ({counts['indirect']}개)")
    if counts["counterexample"] != 1:
        warnings.append(
            f"반례형은 라운드당 1개여야 하는데 {counts['counterexample']}개다"
        )
    if not shorts:
        warnings.append("공매도 집중 이벤트가 하나도 없다")
    elif len(shorts) > 2:
        warnings.append(f"공매도 집중이 라운드당 1~2회여야 하는데 {len(shorts)}회다")

    return tuple(warnings)


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    _require(path.exists(), f"시나리오 파일이 없다: {path}")
    blob = path.read_bytes()
    raw = json.loads(blob.decode("utf-8"))

    for key in ("name", "tick_seconds", "total_ticks", "starting_cash", "stocks"):
        _require(key in raw, f"시나리오에 {key} 가 없다")
    total_ticks = int(raw["total_ticks"])
    _require(total_ticks > 0, f"total_ticks 는 양수여야 한다 ({total_ticks})")
    _require(raw["tick_seconds"] > 0, "tick_seconds 는 양수여야 한다")
    _require(raw["starting_cash"] > 0, "starting_cash 는 양수여야 한다")

    params = _parse_params(raw.get("params", {}))
    stocks = tuple(_parse_stock(s, total_ticks) for s in raw["stocks"])
    _require(len(stocks) > 0, "종목이 하나도 없다")
    dupes = [s.symbol for s in stocks if [x.symbol for x in stocks].count(s.symbol) > 1]
    _require(not dupes, f"종목 코드가 중복이다: {sorted(set(dupes))}")

    fear = _parse_index(raw["fear_index"], total_ticks, "fear_index")
    fx = _parse_index(raw["fx_rate"], total_ticks, "fx_rate")
    _require(fear.initial >= 0 and fear.initial <= 100, "공포지수 초기값은 0~100 이어야 한다")
    _require(fx.initial > 0, "환율 초기값은 양수여야 한다")

    normals, shorts = _parse_events(raw.get("events", []), stocks, total_ticks)

    return Scenario(
        name=raw["name"],
        source_file=path.name,
        sha256=hashlib.sha256(blob).hexdigest(),
        tick_seconds=float(raw["tick_seconds"]),
        total_ticks=total_ticks,
        starting_cash=int(raw["starting_cash"]),
        params=params,
        fear_index=fear,
        fx_rate=fx,
        stocks=stocks,
        normal_events=normals,
        short_events=shorts,
        warnings=_collect_warnings(normals, shorts, float(raw["tick_seconds"])),
    )


def list_scenarios(directory: str | Path = "scenarios") -> list[dict]:
    """관리자 화면의 시나리오 선택 목록. 깨진 파일도 이유와 함께 보여준다."""
    directory = Path(directory)
    results: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            sc = load_scenario(path)
        except (ScenarioError, json.JSONDecodeError, KeyError, TypeError) as exc:
            results.append({
                "file": path.name, "name": None, "valid": False,
                "error": str(exc), "warnings": [],
            })
        else:
            results.append({
                "file": path.name, "name": sc.name, "valid": True,
                "error": None, "warnings": list(sc.warnings),
                "total_ticks": sc.total_ticks, "tick_seconds": sc.tick_seconds,
                "stocks": len(sc.stocks),
                "events": len(sc.normal_events) + len(sc.short_events),
            })
    return results
