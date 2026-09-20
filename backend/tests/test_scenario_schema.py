"""시나리오 파일 검증. 잘못된 파일은 행사 당일이 아니라 로드 시점에 걸려야 한다."""

import copy
import json

import pytest

from backend.scenario_loader import ScenarioError, list_scenarios, load_scenario

SCENARIO = "scenarios/festival_01.json"


@pytest.fixture(scope="module")
def raw():
    with open(SCENARIO, encoding="utf-8") as fp:
        return json.load(fp)


def _write(tmp_path, data, name="broken.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _expect_reject(tmp_path, raw, mutate, match):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(ScenarioError, match=match):
        load_scenario(_write(tmp_path, data))


# ---------- 정상 파일 ----------

def test_기본_시나리오가_로드되고_경고가_없다():
    sc = load_scenario(SCENARIO)
    assert sc.name == "2026 축제 1회차"
    assert sc.total_ticks == 375
    assert sc.tick_seconds == 4.8
    assert len(sc.stocks) == 7
    assert sc.warnings == (), f"경고가 남아 있다: {sc.warnings}"


def test_타임라인이_스펙_6번과_맞는다():
    sc = load_scenario(SCENARIO)
    assert sc.total_ticks * sc.tick_seconds == pytest.approx(1800)  # 30분
    assert sc.starting_cash == 10_000_000


def test_종목별_노이즈_오버라이드가_반영된다():
    """결정-07. 안정형 3종목은 전역값보다 확실히 낮아야 한다."""
    sc = load_scenario(SCENARIO)
    stable = [sc.noise_sigma_for(s) for s in ("A002", "A004", "A005")]
    wild = [sc.noise_sigma_for(s) for s in ("A006", "A007")]
    assert max(stable) < min(wild)
    assert max(stable) < sc.params.noise_sigma


def test_폴더_스캔이_동작한다():
    """새 시나리오는 코드 수정 없이 파일만 넣으면 잡혀야 한다."""
    found = list_scenarios("scenarios")
    assert any(item["file"] == "festival_01.json" and item["valid"] for item in found)


def test_깨진_파일도_목록에는_이유와_함께_나온다(tmp_path, raw):
    data = copy.deepcopy(raw)
    data["params"]["dampening_cap"] = 1.0
    _write(tmp_path, data, "bad.json")
    (tmp_path / "ok.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    found = {item["file"]: item for item in list_scenarios(tmp_path)}
    assert found["bad.json"]["valid"] is False
    assert "dampening_cap" in found["bad.json"]["error"]
    assert found["ok.json"]["valid"] is True


# ---------- 거부해야 하는 것들 ----------

def test_dampening_cap이_1이상이면_거부한다(tmp_path, raw):
    """가격 방향 보존 불변 조건이 깨진다."""
    for bad in (1.0, 1.2):
        _expect_reject(
            tmp_path, raw,
            lambda d, v=bad: d["params"].__setitem__("dampening_cap", v),
            "dampening_cap",
        )


def test_유동성_0인_종목을_거부한다(tmp_path, raw):
    _expect_reject(
        tmp_path, raw,
        lambda d: d["stocks"][0].__setitem__("liquidity", 0),
        "liquidity",
    )


def test_구간에_빈틈이_있으면_거부한다(tmp_path, raw):
    def mutate(d):
        d["fear_index"]["segments"][1]["from"] = 95   # 90 에서 시작해야 한다
    _expect_reject(tmp_path, raw, mutate, "빈틈 또는 겹침")


def test_구간이_total_ticks에서_끝나지_않으면_거부한다(tmp_path, raw):
    def mutate(d):
        d["fx_rate"]["segments"][-1]["to"] = 370
    _expect_reject(tmp_path, raw, mutate, "total_ticks")


def test_드리프트_구간이_빠진_종목을_거부한다(tmp_path, raw):
    def mutate(d):
        d["stocks"][2]["drift_segments"] = []
    _expect_reject(tmp_path, raw, mutate, "비어 있다")


def test_모르는_난이도를_거부한다(tmp_path, raw):
    def mutate(d):
        d["events"][0]["difficulty"] = "hard"
    _expect_reject(tmp_path, raw, mutate, "difficulty")


def test_없는_섹터를_가리키는_이벤트를_거부한다(tmp_path, raw):
    def mutate(d):
        d["events"][0]["sector"] = "우주항공"
    _expect_reject(tmp_path, raw, mutate, "없는 섹터")


def test_섹터와_종목을_동시에_지정하면_거부한다(tmp_path, raw):
    def mutate(d):
        d["events"][0]["symbol"] = "A001"
    _expect_reject(tmp_path, raw, mutate, "정확히 하나")


def test_반영이_라운드를_넘어가는_이벤트를_거부한다(tmp_path, raw):
    def mutate(d):
        d["events"][0]["tick"] = 370
        d["events"][0]["spread_ticks"] = 20
    _expect_reject(tmp_path, raw, mutate, "total_ticks")


def test_덤프가_라운드를_넘어가는_공매도를_거부한다(tmp_path, raw):
    def mutate(d):
        short = next(e for e in d["events"] if e["type"] == "short_pressure")
        short["tick"] = 368
    _expect_reject(tmp_path, raw, mutate, "덤프 종료")


def test_theta가_범위를_벗어나면_거부한다(tmp_path, raw):
    _expect_reject(
        tmp_path, raw,
        lambda d: d["fear_index"].__setitem__("theta", 1.4),
        "theta",
    )


def test_중복된_이벤트_키를_거부한다(tmp_path, raw):
    def mutate(d):
        d["events"][1]["key"] = d["events"][0]["key"]
    _expect_reject(tmp_path, raw, mutate, "중복")


def test_params에_오타가_있으면_거부한다(tmp_path, raw):
    """조용히 무시되면 튜닝 결과가 거짓말이 된다."""
    def mutate(d):
        d["params"]["resistence_coef"] = 0.3
    _expect_reject(tmp_path, raw, mutate, "모르는 항목")


def test_spread_ticks가_0이면_거부한다(tmp_path, raw):
    """반영중 배지 구간이 사라진다."""
    _expect_reject(
        tmp_path, raw,
        lambda d: d["events"][0].__setitem__("spread_ticks", 0),
        "spread_ticks",
    )


# ---------- 경고로만 잡는 것들 ----------

def test_초반_90틱_헤드라인은_경고로_잡는다(tmp_path, raw):
    data = copy.deepcopy(raw)
    data["events"][0]["tick"] = 40
    sc = load_scenario(_write(tmp_path, data))
    assert any("헤드라인이 없어야" in w for w in sc.warnings)


def test_반례형이_2개면_경고한다(tmp_path, raw):
    data = copy.deepcopy(raw)
    for event in data["events"]:
        if event.get("difficulty") == "indirect" and event.get("headline"):
            event["difficulty"] = "counterexample"
            break
    sc = load_scenario(_write(tmp_path, data))
    assert any("반례형" in w for w in sc.warnings)


def test_헤드라인이_몰리면_경고한다(tmp_path, raw):
    data = copy.deepcopy(raw)
    data["events"][2]["tick"] = data["events"][1]["tick"] + 5
    sc = load_scenario(_write(tmp_path, data))
    assert any("간격이 좁다" in w for w in sc.warnings)
