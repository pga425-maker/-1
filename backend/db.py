"""SQLite 연결과 스키마.

금액은 전부 원 단위 정수로 다룬다. 부동소수로 누적하면 동시성 테스트의 합격 기준
("현금+평가액 총합이 수수료 오차 내 보존")을 검증할 수 없다. 비율과 가격 계수만 REAL 이다.

참가자별 틱 단위 자산 스냅샷 테이블은 의도적으로 만들지 않는다(결정-11).
위험조정점수에 필요한 통계는 게임 중 메모리에서 누적하고, 사후 분석은
tick_log(가격) + orders(보유 변화) 로 재구성한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
  id              INTEGER PRIMARY KEY,
  scenario_file   TEXT NOT NULL,
  scenario_name   TEXT NOT NULL,
  scenario_sha256 TEXT NOT NULL,
  seed            INTEGER NOT NULL,
  status          TEXT NOT NULL,
  current_tick    INTEGER NOT NULL DEFAULT 0,
  tick_seconds    REAL    NOT NULL,
  total_ticks     INTEGER NOT NULL,
  starting_cash   INTEGER NOT NULL,
  started_at      TEXT,
  ended_at        TEXT,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES sessions(id),
  nickname      TEXT NOT NULL,
  device_token  TEXT NOT NULL,
  cash          INTEGER NOT NULL,
  reserved_cash INTEGER NOT NULL DEFAULT 0,
  starting_cash INTEGER NOT NULL,
  join_tick     INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT NOT NULL,
  UNIQUE(session_id, nickname),
  UNIQUE(session_id, device_token)
);

CREATE TABLE IF NOT EXISTS holdings (
  session_id   INTEGER NOT NULL,
  player_id    INTEGER NOT NULL,
  symbol       TEXT    NOT NULL,
  quantity     INTEGER NOT NULL DEFAULT 0,
  reserved_qty INTEGER NOT NULL DEFAULT 0,
  avg_cost     REAL    NOT NULL DEFAULT 0,
  realized_pnl INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, player_id, symbol)
);

CREATE TABLE IF NOT EXISTS orders (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL,
  player_id     INTEGER NOT NULL,
  symbol        TEXT    NOT NULL,
  side          TEXT    NOT NULL,
  requested_qty INTEGER NOT NULL,
  filled_qty    INTEGER NOT NULL DEFAULT 0,
  reason        TEXT,
  submit_tick   INTEGER NOT NULL,
  fill_tick     INTEGER,
  quoted_price  INTEGER NOT NULL,
  reserved      INTEGER NOT NULL DEFAULT 0,
  fill_price    INTEGER,
  fee           INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL,
  reject_code   TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tick_log (
  session_id         INTEGER NOT NULL,
  tick               INTEGER NOT NULL,
  symbol             TEXT    NOT NULL,
  base_drift         REAL NOT NULL,
  event_delta        REAL NOT NULL,
  fear_shock         REAL NOT NULL,
  fx_shock           REAL NOT NULL,
  total_script_delta REAL NOT NULL,
  f                  REAL NOT NULL,
  damp               REAL NOT NULL,
  actual_delta       REAL NOT NULL,
  price              INTEGER NOT NULL,
  buy_qty_submitted  INTEGER NOT NULL DEFAULT 0,
  sell_qty_submitted INTEGER NOT NULL DEFAULT 0,
  buy_qty_filled     INTEGER NOT NULL DEFAULT 0,
  sell_qty_filled    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, tick, symbol)
);

CREATE TABLE IF NOT EXISTS index_log (
  session_id  INTEGER NOT NULL,
  tick        INTEGER NOT NULL,
  fear_index  REAL NOT NULL,
  fear_target REAL NOT NULL,
  fx_rate     REAL NOT NULL,
  fx_target   REAL NOT NULL,
  PRIMARY KEY (session_id, tick)
);

CREATE TABLE IF NOT EXISTS events_fired (
  id                INTEGER PRIMARY KEY,
  session_id        INTEGER NOT NULL,
  event_key         TEXT NOT NULL,
  type              TEXT NOT NULL,
  difficulty        TEXT,
  sector            TEXT,
  symbol            TEXT,
  headline          TEXT NOT NULL,
  body              TEXT NOT NULL DEFAULT '',
  headline_tick     INTEGER NOT NULL,
  impact_start_tick INTEGER NOT NULL,
  impact_end_tick   INTEGER NOT NULL,
  schedule_json     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_fill ON orders(session_id, fill_tick, symbol);
CREATE INDEX IF NOT EXISTS idx_orders_player ON orders(session_id, player_id);
CREATE INDEX IF NOT EXISTS idx_players_session ON players(session_id, status);
"""

DEFAULT_DB = "festival.db"


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def reset(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """새 DB 를 만든다. 행사 전 리허설과 본 라운드를 분리할 때 쓴다."""
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    conn = connect(path)
    init_schema(conn)
    return conn
