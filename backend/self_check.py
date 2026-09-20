"""자가 검증 루프 — 사람이 지적하기를 기다리지 않고 직접 돌려 보고 고친다.

스펙 17번의 판정 항목 8개를 자동으로 본다. 실패나 경고가 나오면 시나리오
파라미터를 조정해 다시 돌리고, 최대 5회까지 반복한다. 그래도 안 되면 고치려 한
내용과 남은 문제를 그대로 보고한다.

스펙 자체를 바꿔야 풀리는 문제(틱 길이, 라운드 시간 같은 것)는 임의로 바꾸지 않고
보고서에 제안으로만 남긴다.

실행:
    python3 -m backend.self_check                 30회, 조정 반영
    python3 -m backend.self_check --runs 10       회차 줄이기
    python3 -m backend.self_check --no-apply      판정만 하고 파일은 안 건드림
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from backend.event_scheduler import EventScheduler
from backend.models import Scenario
from backend.scenario_loader import load_scenario
from backend.simulate_tuning import prepare, run_round

import random

DEFAULT_SCENARIO = Path("scenarios/festival_01.json")
REPORT_PATH = Path("self_check_report.md")
MAX_ROUNDS = 5

# 판정 기준
FLAT_RUN_TICKS = 20            # 연속 이 틱 이상 거의 안 변하면 정체
FEAR_FLAT_EPS = 0.15           # 공포지수가 이보다 적게 움직이면 '거의 안 변함'
FX_FLAT_EPS = 0.40             # 환율 기준(원)
FEAR_PIN_LOW, FEAR_PIN_HIGH = 1.0, 99.0
FX_MAX_DEVIATION = 0.15
MIN_HEADLINE_GAP_SEC = 90.0
MAX_HEADLINE_GAP_SEC = 420.0
RUIN_THRESHOLD = -0.80
RUIN_RATIO_LIMIT = 0.10
FLAT_BOT_BAND = 0.05
# "확실히 낮은지" 를 판정하려면 마진이 필요하다. 1.3배 정도는 우연으로도 나온다.
STABLE_VOL_MARGIN = 1.5
# 정체를 피하려면 틱당 변화가 판정 임계치를 넘는 일이 절반 넘게 일어나야 한다.
# 정규분포에서 P(|X| < eps) < 0.5 이려면 sigma > 1.48 * eps 다. 여유를 둬 1.6 을 쓴다.
FLAT_SIGMA_FACTOR = 1.6
STABLE = ("A002", "A004", "A005")      # 한별에너지, 늘봄유통, 파란로지스틱스
WILD = ("A006", "A007")                # 씨드게임즈, 미르소재

PASS, WARN, FAIL = "통과", "경고", "실패"


@dataclass
class Check:
    number: int
    name: str
    status: str
    detail: str

    @property
    def bad(self) -> bool:
        return self.status in (WARN, FAIL)


@dataclass
class RoundReport:
    attempt: int
    checks: list[Check]
    adjustments: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]


def longest_flat_run(series: list[float], eps: float) -> int:
    longest = current = 0
    for a, b in zip(series, series[1:]):
        current = current + 1 if abs(b - a) < eps else 0
        longest = max(longest, current)
    return longest


def evaluate(scenario: Scenario, seeds: list[int], bots: int, herding: float) -> tuple[list[Check], dict]:
    prepared = prepare(scenario)
    params = scenario.params
    results = [
        run_round(prepared, seed, params.resistance_coef, params.dampening_cap,
                  bots, herding)
        for seed in seeds
    ]

    symbols = tuple(s.symbol for s in scenario.stocks)
    name_of = {s.symbol: s.name for s in scenario.stocks}
    checks: list[Check] = []

    # (1) 종목 등락률 분포
    same_direction = 0
    for r in results:
        signs = {1 if r.price_returns[s] > 0 else -1 for s in symbols}
        if len(signs) == 1:
            same_direction += 1
    mean_returns = {
        s: statistics.mean(r.price_returns[s] for r in results) for s in symbols
    }
    checks.append(Check(
        1, "종목 등락률 분포",
        FAIL if same_direction else PASS,
        (f"{same_direction}/{len(results)}회에서 7종목이 전부 같은 방향"
         if same_direction else
         "상승과 하락이 매 회차 섞인다. "
         + ", ".join(f"{name_of[s]} {mean_returns[s] * 100:+.1f}%" for s in symbols)),
    ))

    # (2) 안정형 대조군
    vols = {
        s: statistics.mean(r.tick_volatility[s] for r in results) for s in symbols
    }
    stable_max = max(vols[s] for s in STABLE)
    wild_min = min(vols[s] for s in WILD)
    ratio = wild_min / stable_max if stable_max else 0
    checks.append(Check(
        2, "안정형 대조군 변동성",
        PASS if ratio >= STABLE_VOL_MARGIN else FAIL,
        f"안정형 최대 {stable_max * 100:.3f}% 대 밈·비전형 최소 {wild_min * 100:.3f}% "
        f"({ratio:.1f}배, 기준 {STABLE_VOL_MARGIN}배 이상)",
    ))

    # (3) 지수 생동감
    fear_flat = max(longest_flat_run(r.fear_path, FEAR_FLAT_EPS) for r in results)
    fx_flat = max(longest_flat_run(r.fx_path, FX_FLAT_EPS) for r in results)
    flat_bad = fear_flat >= FLAT_RUN_TICKS or fx_flat >= FLAT_RUN_TICKS
    checks.append(Check(
        3, "지수 생동감",
        FAIL if flat_bad else PASS,
        f"연속 정체 최대 공포 {fear_flat}틱, 환율 {fx_flat}틱 "
        f"(기준 {FLAT_RUN_TICKS}틱 미만)",
    ))

    # (4) 지수 폭주
    fear_min = min(min(r.fear_path) for r in results)
    fear_max = max(max(r.fear_path) for r in results)
    fx_dev = max(
        max(abs(v / scenario.fx_rate.initial - 1) for v in r.fx_path) for r in results
    )
    runaway = (
        fear_min <= FEAR_PIN_LOW or fear_max >= FEAR_PIN_HIGH
        or fx_dev > FX_MAX_DEVIATION
    )
    checks.append(Check(
        4, "지수 폭주",
        FAIL if runaway else PASS,
        f"공포지수 {fear_min:.1f}~{fear_max:.1f}, 환율 최대 이탈 {fx_dev * 100:.2f}% "
        f"(기준 ±{FX_MAX_DEVIATION * 100:.0f}%)",
    ))

    # (5) 뉴스 간격
    gaps = EventScheduler(scenario, random.Random(0)).headline_gaps_seconds()
    bad_gaps = [
        g for g in gaps if g < MIN_HEADLINE_GAP_SEC or g > MAX_HEADLINE_GAP_SEC
    ]
    checks.append(Check(
        5, "뉴스 간격",
        WARN if bad_gaps else PASS,
        (f"기준을 벗어난 간격 {[round(g) for g in bad_gaps]}초"
         if bad_gaps else
         f"{len(gaps)}개 간격이 {min(gaps):.0f}~{max(gaps):.0f}초 안에 있다"),
    ))

    # (6) 공매도 함정
    trap_fail, pump_peaks = [], []
    for seed in seeds:
        sched = EventScheduler(scenario, random.Random(seed))
        for e in sched.events:
            if not e.is_short_pressure:
                continue
            if e.pump_total >= e.dump_total or e.net_multiplier() >= 1.0:
                trap_fail.append(f"시드 {seed} {e.key}")
            mult = 1.0
            for d in e.deltas[: e.pump_ticks]:
                mult *= 1.0 + d
            pump_peaks.append(mult)
    weak_bait = [m for m in pump_peaks if m < params.min_visible_pump]
    checks.append(Check(
        6, "공매도 함정 작동",
        FAIL if trap_fail else (WARN if weak_bait else PASS),
        (f"불변 조건 위반 {trap_fail[:3]}" if trap_fail else
         f"펌프 상승 배율 {min(pump_peaks):.3f}~{max(pump_peaks):.3f} "
         f"(미끼 하한 {params.min_visible_pump}), 미달 {len(weak_bait)}건"),
    ))

    # (7) 승부 긴장감
    frozen = sum(1 for r in results if r.leader_changes_second_half == 0)
    mean_changes = statistics.mean(r.leader_changes_second_half for r in results)
    checks.append(Check(
        7, "승부 긴장감",
        WARN if frozen > len(results) * 0.2 else PASS,
        f"후반 1위 교체 평균 {mean_changes:.1f}회, 한 번도 안 바뀐 회차 "
        f"{frozen}/{len(results)}",
    ))

    # (8) 파산·무의미
    ruined = statistics.mean(r.ruined_bot_ratio for r in results)
    flat = statistics.mean(r.flat_bot_ratio for r in results)
    harsh = ruined > RUIN_RATIO_LIMIT
    bland = flat >= 1.0
    checks.append(Check(
        8, "파산·무의미",
        WARN if (harsh or bland) else PASS,
        f"-80% 밑 봇 {ruined * 100:.1f}% (기준 {RUIN_RATIO_LIMIT * 100:.0f}%), "
        f"±{FLAT_BOT_BAND * 100:.0f}% 안에 든 봇 {flat * 100:.1f}%",
    ))

    stats = {
        "mean_returns": mean_returns,
        "volatility": vols,
        "vol_ratio": ratio,
        "fear_range": [fear_min, fear_max],
        "fx_deviation": fx_dev,
        "fear_flat": fear_flat,
        "fx_flat": fx_flat,
        "leader_changes": mean_changes,
        "ruined": ruined,
        "flat_bots": flat,
        "bot_returns": [
            statistics.mean(r.bot_returns.values()) for r in results
        ],
    }
    return checks, stats


def adjust(raw: dict, checks: list[Check], stats: dict) -> list[str]:
    """실패·경고 항목에 맞춰 시나리오 파라미터를 조정한다.

    고정 배율로 밀면 초기값이 크게 어긋났을 때 5회 안에 못 따라잡는다.
    그래서 측정값에서 필요한 값을 역산해 한 번에 맞춘다.

    스펙을 바꿔야 풀리는 문제는 손대지 않는다. 여기서 만지는 것은 전부
    시나리오 JSON 안의 튜닝 파라미터다.
    """
    notes: list[str] = []
    by_number = {c.number: c for c in checks}

    c3 = by_number.get(3)
    if c3 and c3.status == FAIL:
        needed = {
            "fear_index": ("공포지수", FEAR_FLAT_EPS * FLAT_SIGMA_FACTOR),
            "fx_rate": ("환율", FX_FLAT_EPS * FLAT_SIGMA_FACTOR),
        }
        flat_by_key = {"fear_index": stats["fear_flat"], "fx_rate": stats["fx_flat"]}
        for key, (label, target) in needed.items():
            if flat_by_key[key] < FLAT_RUN_TICKS:
                continue
            before = raw[key]["noise_sigma"]
            raw[key]["noise_sigma"] = round(max(before, target), 4)
            before_theta = raw[key]["theta"]
            raw[key]["theta"] = round(max(before_theta, 0.05), 4)
            notes.append(
                f"(3) {label} 정체 {flat_by_key[key]}틱: noise_sigma {before} -> "
                f"{raw[key]['noise_sigma']} (임계치 대비 {FLAT_SIGMA_FACTOR}배), "
                f"theta {before_theta} -> {raw[key]['theta']}"
            )

    c4 = by_number.get(4)
    if c4 and c4.status == FAIL:
        for key, label in (("fear_index", "공포지수"), ("fx_rate", "환율")):
            before = raw[key]["noise_sigma"]
            raw[key]["noise_sigma"] = round(before * 0.75, 4)
            notes.append(
                f"(4) {label} 폭주: noise_sigma {before} -> {raw[key]['noise_sigma']}"
            )

    c2 = by_number.get(2)
    if c2 and c2.status == FAIL:
        ratio = stats.get("vol_ratio", 1.0) or 1.0
        # 목표 배율에 못 미친 만큼만 양쪽으로 벌린다. 한 번에 과하게 벌리면
        # (4) 지수 폭주나 (8) 파산 쪽이 터진다.
        gap = STABLE_VOL_MARGIN / ratio
        down = max(0.45, 1.0 / (gap ** 0.7))
        up = min(1.8, gap ** 0.7)
        for stock in raw["stocks"]:
            if stock["symbol"] in STABLE:
                before = stock.get("noise_sigma", raw["params"]["noise_sigma"])
                stock["noise_sigma"] = round(before * down, 5)
                notes.append(
                    f"(2) {stock['name']} noise_sigma {before} -> {stock['noise_sigma']}"
                )
            elif stock["symbol"] in WILD:
                before = stock.get("noise_sigma", raw["params"]["noise_sigma"])
                stock["noise_sigma"] = round(before * up, 5)
                notes.append(
                    f"(2) {stock['name']} noise_sigma {before} -> {stock['noise_sigma']}"
                )

    c6 = by_number.get(6)
    if c6 and c6.status == FAIL:
        before = raw["params"].get("net_loss_margin", 0.15)
        raw["params"]["net_loss_margin"] = round(before * 1.5, 4)
        notes.append(f"(6) net_loss_margin {before} -> {raw['params']['net_loss_margin']}")
    elif c6 and c6.status == WARN:
        before = raw["params"].get("min_visible_pump", 1.05)
        raw["params"]["min_visible_pump"] = round(before + 0.01, 4)
        notes.append(f"(6) 미끼가 약함. min_visible_pump {before} -> "
                     f"{raw['params']['min_visible_pump']}")

    c8 = by_number.get(8)
    if c8 and c8.status == WARN and stats.get("ruined", 0) > RUIN_RATIO_LIMIT:
        before = raw["params"]["noise_sigma"]
        raw["params"]["noise_sigma"] = round(before * 0.85, 5)
        notes.append(f"(8) 너무 가혹함. 전역 noise_sigma {before} -> "
                     f"{raw['params']['noise_sigma']}")

    return notes


def render_report(rounds: list[RoundReport], scenario: Scenario, args) -> str:
    lines = [
        "# 자가 검증 결과",
        "",
        f"- 시나리오: {scenario.name} ({scenario.source_file})",
        f"- 회차당 시드 {args.runs}개, 봇 {args.bots}마리, 군집 확률 {args.herding}",
        f"- 반복 {len(rounds)}회 (최대 {MAX_ROUNDS}회)",
        "",
    ]
    for report in rounds:
        lines += [f"## {report.attempt}회차", ""]
        lines += ["| 항목 | 판정 | 내용 |", "|---|---|---|"]
        for c in report.checks:
            lines.append(f"| ({c.number}) {c.name} | {c.status} | {c.detail} |")
        lines.append("")
        if report.adjustments:
            lines += ["조정한 내용", ""]
            lines += [f"- {a}" for a in report.adjustments]
            lines.append("")
        else:
            lines += ["조정한 내용 없음", ""]

    final = rounds[-1]
    lines += ["## 최종 상태", ""]
    if not final.failures and not final.warnings:
        lines.append("8개 항목 전부 통과했다.")
    else:
        if final.failures:
            lines.append("남은 실패 항목")
            lines += [f"- ({c.number}) {c.name}: {c.detail}" for c in final.failures]
            lines.append("")
        if final.warnings:
            lines.append("남은 경고 항목")
            lines += [f"- ({c.number}) {c.name}: {c.detail}" for c in final.warnings]
            lines.append("")

    p = scenario.params
    lines += [
        "",
        "## 최종 파라미터",
        "",
        "```",
        f"resistance_coef      {p.resistance_coef}",
        f"dampening_cap        {p.dampening_cap}",
        f"noise_sigma (전역)   {p.noise_sigma}",
        f"fear_sensitivity     {p.fear_sensitivity}",
        f"fx_sensitivity       {p.fx_sensitivity}",
        f"net_loss_margin      {p.net_loss_margin}",
        f"min_visible_pump     {p.min_visible_pump}",
        f"fear theta/sigma     {scenario.fear_index.theta} / {scenario.fear_index.noise_sigma}",
        f"fx   theta/sigma     {scenario.fx_rate.theta} / {scenario.fx_rate.noise_sigma}",
        "```",
        "",
        "## 종목별 최종 등락률과 틱 변동성",
        "",
        "| 종목 | 평균 등락률 | 틱 변동성 |",
        "|---|---|---|",
    ]
    stats = final.stats
    for stock in scenario.stocks:
        lines.append(
            f"| {stock.name} | {stats['mean_returns'][stock.symbol] * 100:+.1f}% | "
            f"{stats['volatility'][stock.symbol] * 100:.3f}% |"
        )
    lines += [
        "",
        "이 수치는 봇 시뮬레이션 결과다. 실제 참가자 50명의 행동은 봇과 다르므로",
        "확정된 값으로 읽으면 안 된다. 리허설에서 다시 확인해야 한다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="자가 검증 루프")
    parser.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--bots", type=int, default=40)
    parser.add_argument("--herding", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=90210)
    parser.add_argument(
        "--no-apply", action="store_true",
        help="판정만 하고 시나리오 파일은 고치지 않는다",
    )
    args = parser.parse_args()

    path = Path(args.scenario)
    backup = path.with_suffix(".json.bak")
    if not args.no_apply:
        shutil.copy(path, backup)

    seeds = [args.seed + i for i in range(args.runs)]
    rounds: list[RoundReport] = []

    for attempt in range(1, MAX_ROUNDS + 1):
        scenario = load_scenario(path)
        checks, stats = evaluate(scenario, seeds, args.bots, args.herding)
        report = RoundReport(attempt=attempt, checks=checks, stats=stats)
        rounds.append(report)

        print(f"\n--- {attempt}회차 ---")
        for c in checks:
            mark = {PASS: " ", WARN: "!", FAIL: "X"}[c.status]
            print(f" [{mark}] ({c.number}) {c.name}: {c.detail}")

        bad = [c for c in checks if c.bad]
        if not bad:
            print("\n8개 항목 전부 통과했다.")
            break
        if attempt == MAX_ROUNDS:
            print(f"\n{MAX_ROUNDS}회를 다 썼다. 남은 문제를 보고서에 기록한다.")
            break
        if args.no_apply:
            print("\n--no-apply 라 조정하지 않고 멈춘다.")
            break

        raw = json.loads(path.read_text(encoding="utf-8"))
        notes = adjust(raw, checks, stats)
        if not notes:
            print("\n자동으로 조정할 수 있는 항목이 없다. 보고서에 남긴다.")
            break
        report.adjustments = notes
        for note in notes:
            print(f"   조정: {note}")
        path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    scenario = load_scenario(path)
    REPORT_PATH.write_text(render_report(rounds, scenario, args), encoding="utf-8")
    print(f"\n보고서: {REPORT_PATH}")
    if backup.exists():
        print(f"조정 전 원본: {backup}")
    return 0 if not rounds[-1].failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
