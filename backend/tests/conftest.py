"""테스트 공용 픽스처."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import db as dbmod                       # noqa: E402
from backend.game_loop import GameSession             # noqa: E402
from backend.scenario_loader import load_scenario     # noqa: E402

SCENARIO_PATH = ROOT / "scenarios" / "festival_01.json"


@pytest.fixture(scope="session")
def scenario():
    return load_scenario(SCENARIO_PATH)


@pytest.fixture
def session(scenario):
    """메모리 DB 위에서 도는 게임 세션. 틱은 테스트가 직접 진행시킨다."""
    conn = dbmod.connect(":memory:")
    dbmod.init_schema(conn)
    game = GameSession(scenario, conn, seed=20260919)
    game.persist_session()
    game.status = "running"
    yield game
    conn.close()
