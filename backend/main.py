"""FastAPI 앱 — API, WebSocket, 정적 파일 서빙.

프론트 빌드 결과(frontend/dist)를 같은 프로세스에서 서빙한다. 포트가 하나여야
QR 주소가 갈리지 않는다(결정-12).
"""

from __future__ import annotations

import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from backend import analysis, db as dbmod
from backend.game_loop import (
    STATUS_ENDED, STATUS_RUNNING, GameSession, OrderRejected,
)
from backend.models import BUY_REASONS
from backend.profanity import check as profanity_check
from backend.scenario_loader import list_scenarios, load_scenario
from backend.ws_hub import WebSocketHub

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios"
DIST_DIR = ROOT / "frontend" / "dist"
DEFAULT_SCENARIO = "festival_01.json"

EXPORT_TABLES = (
    "players", "orders", "tick_log", "index_log", "events_fired", "result",
)


class AppState:
    """앱 하나가 들고 있는 전역 상태. 한 번에 한 판만 돌린다."""

    def __init__(self) -> None:
        self.conn = None
        self.session: GameSession | None = None
        self.hub = WebSocketHub()
        self.player_tokens: dict[str, int] = {}
        self.admin_tokens: set[str] = set()

    def require_session(self) -> GameSession:
        if self.session is None:
            raise HTTPException(409, "아직 시작된 판이 없습니다")
        return self.session


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.conn = dbmod.connect(os.environ.get("FESTIVAL_DB", "festival.db"))
    dbmod.init_schema(state.conn)
    yield
    if state.conn is not None:
        state.conn.close()


app = FastAPI(title="모의투자 리그", lifespan=lifespan)


# ---------------------------------------------------------------- 인증

def player_id_from(token: str | None) -> int:
    if not token or token not in state.player_tokens:
        raise HTTPException(401, "참가 정보를 찾을 수 없습니다. 다시 접속해 주세요")
    return state.player_tokens[token]


def current_player(x_player_token: str | None = Header(default=None)) -> int:
    return player_id_from(x_player_token)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not x_admin_token or x_admin_token not in state.admin_tokens:
        raise HTTPException(401, "관리자 인증이 필요합니다")


# ---------------------------------------------------------------- 모델

class JoinRequest(BaseModel):
    nickname: str
    device_token: str = Field(min_length=8, max_length=64)


class OrderRequest(BaseModel):
    symbol: str
    side: str
    qty: int
    reason: str | None = None


class AdminLogin(BaseModel):
    password: str


class StartRequest(BaseModel):
    scenario_file: str = DEFAULT_SCENARIO
    seed: int | None = None
    reset_db: bool = True
    speed: float = Field(default=1.0, gt=0, le=200)
    """배속. 리허설과 자동 테스트 전용이다.

    본 라운드는 반드시 1.0 으로 둔다. 틱 길이 4.8초는 주식을 처음 해 보는 참가자가
    상황을 판단할 시간을 확보하기 위한 값이다.
    """


class KickRequest(BaseModel):
    player_id: int


# ---------------------------------------------------------------- 참가자 API

@app.post("/api/join")
async def join(req: JoinRequest):
    session = state.require_session()
    nickname = req.nickname.strip()

    existing = session.by_token.get(req.device_token)
    if existing is None:
        reason = profanity_check(nickname)
        if reason:
            raise HTTPException(400, reason)

    async with session.lock:
        try:
            player = session.join(nickname, req.device_token)
        except OrderRejected as exc:
            raise HTTPException(409, exc.message) from exc

    token = next(
        (t for t, pid in state.player_tokens.items() if pid == player.player_id),
        None,
    )
    if token is None:
        token = secrets.token_urlsafe(24)
        state.player_tokens[token] = player.player_id
    return {
        "player_token": token,
        "player_id": player.player_id,
        "nickname": player.nickname,
        "rejoined": existing is not None,
        "snapshot": session.snapshot(player.player_id),
    }


@app.get("/api/state")
async def get_state(player_id: int = Depends(current_player)):
    return state.require_session().snapshot(player_id)


@app.post("/api/order")
async def post_order(req: OrderRequest, player_id: int = Depends(current_player)):
    session = state.require_session()
    async with session.lock:
        try:
            order = session.submit_order(
                player_id, req.symbol, req.side, req.qty, req.reason
            )
        except OrderRejected as exc:
            return JSONResponse(
                status_code=400,
                content={"reject_code": exc.code, "detail": exc.message},
            )
        params = session.scenario.params
        gross = order.quoted_price * order.qty
        return {
            "order_id": order.order_id,
            "status": "pending",
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "quoted_price": order.quoted_price,
            "expected_amount": gross,
            "expected_fee": int(round(gross * params.fee_rate)),
            "reserved": order.reserved,
            "message": "다음 틱 가격으로 체결됩니다",
        }


@app.get("/api/orders")
async def get_orders(player_id: int = Depends(current_player)):
    session = state.require_session()
    rows = session.conn.execute(
        """SELECT id, symbol, side, requested_qty, filled_qty, reason, submit_tick,
                  fill_tick, quoted_price, fill_price, fee, status, reject_code
           FROM orders WHERE session_id=? AND player_id=?
           ORDER BY id DESC LIMIT 200""",
        (session.session_id, player_id),
    ).fetchall()
    return {"orders": [dict(r) for r in rows]}


@app.get("/api/rank")
async def get_rank(tab: str = Query("return", pattern="^(return|risk|total)$")):
    session = state.require_session()
    rows = session.board(tab, limit=0)
    return {
        "tab": tab,
        "min_ticks_for_risk_rank": session.scenario.params.min_ticks_for_risk_rank,
        "rows": [
            {
                "player_id": s.player_id, "nickname": s.nickname,
                "return_pct": s.return_pct, "risk_adjusted": s.risk_adjusted,
                "mdd": s.mdd, "final_asset": s.final_asset,
                "rank_return": s.rank_return, "rank_risk": s.rank_risk,
                "rank_total": s.rank_total,
                "eligible_for_risk": s.eligible_for_risk,
                "ticks_played": s.ticks_played,
            }
            for s in rows
        ],
    }


@app.get("/api/result")
async def get_result(player_id: int = Depends(current_player)):
    session = state.require_session()
    if session.status != STATUS_ENDED:
        raise HTTPException(409, "아직 진행 중입니다")
    return analysis.build_result(session, player_id)


# ---------------------------------------------------------------- WebSocket

@app.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket, token: str = Query(default="")):
    await websocket.accept()
    player_id = state.player_tokens.get(token)
    conn = await state.hub.register(websocket, player_id, "player")
    try:
        if state.session is not None:
            await websocket.send_json(
                {"type": "snapshot", **state.session.snapshot(player_id)}
            )
        while True:
            await websocket.receive_text()      # 클라이언트 ping 유지용
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await state.hub.unregister(conn)


@app.websocket("/ws/board")
async def ws_board(websocket: WebSocket):
    await websocket.accept()
    conn = await state.hub.register(websocket, None, "board")
    try:
        if state.session is not None:
            await websocket.send_json({"type": "snapshot", **state.session.snapshot()})
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await state.hub.unregister(conn)


# ---------------------------------------------------------------- 관리자 API

@app.post("/api/admin/login")
async def admin_login(req: AdminLogin):
    expected = os.environ.get("ADMIN_PASSWORD", "")
    if not expected:
        raise HTTPException(500, ".env 에 ADMIN_PASSWORD 를 설정해야 합니다")
    if not secrets.compare_digest(req.password, expected):
        raise HTTPException(401, "비밀번호가 다릅니다")
    token = secrets.token_urlsafe(24)
    state.admin_tokens.add(token)
    return {"admin_token": token}


@app.get("/api/admin/scenarios", dependencies=[Depends(require_admin)])
async def admin_scenarios():
    return {"scenarios": list_scenarios(SCENARIO_DIR)}


@app.post("/api/admin/start", dependencies=[Depends(require_admin)])
async def admin_start(req: StartRequest):
    if state.session is not None and state.session.status == STATUS_RUNNING:
        raise HTTPException(409, "이미 진행 중인 판이 있습니다")
    path = SCENARIO_DIR / req.scenario_file
    if not path.exists():
        raise HTTPException(404, f"시나리오 파일이 없습니다: {req.scenario_file}")
    scenario = load_scenario(path)

    if req.reset_db:
        state.conn = dbmod.reset(os.environ.get("FESTIVAL_DB", "festival.db"))
        state.player_tokens.clear()

    session = GameSession(scenario, state.conn, req.seed, req.speed)
    session.hub = state.hub
    session.persist_session()
    state.session = session
    await session.start()
    return {
        "status": session.status, "seed": session.seed, "speed": session.speed,
        "scenario": scenario.name, "warnings": list(scenario.warnings),
    }


@app.post("/api/admin/pause", dependencies=[Depends(require_admin)])
async def admin_pause():
    await state.require_session().pause()
    return {"status": state.require_session().status}


@app.post("/api/admin/resume", dependencies=[Depends(require_admin)])
async def admin_resume():
    await state.require_session().resume()
    return {"status": state.require_session().status}


@app.post("/api/admin/end", dependencies=[Depends(require_admin)])
async def admin_end():
    await state.require_session().end()
    return {"status": state.require_session().status}


@app.post("/api/admin/kick", dependencies=[Depends(require_admin)])
async def admin_kick(req: KickRequest):
    session = state.require_session()
    async with session.lock:
        try:
            session.kick(req.player_id)
        except OrderRejected as exc:
            raise HTTPException(404, exc.message) from exc
    return {"ok": True}


@app.get("/api/admin/health", dependencies=[Depends(require_admin)])
async def admin_health():
    session = state.session
    durations = session.tick_durations if session else []
    return {
        "status": session.status if session else "none",
        "tick": session.tick if session else 0,
        "speed": session.speed if session else 1.0,
        "players": len(session.players) if session else 0,
        "ws_connections": len(state.hub.connections),
        "ws_dropped": state.hub.dropped,
        "broadcast_latency_ms": state.hub.latency_percentiles(),
        "tick_duration_ms": {
            "last": durations[-1] if durations else 0.0,
            "max": max(durations) if durations else 0.0,
            "mean": sum(durations) / len(durations) if durations else 0.0,
        },
        "exceptions": session.exceptions if session else [],
    }


@app.get("/api/admin/export", dependencies=[Depends(require_admin)])
async def admin_export(table: str = Query("result")):
    if table not in EXPORT_TABLES:
        raise HTTPException(400, f"내보낼 수 없는 표입니다. 가능: {EXPORT_TABLES}")
    session = state.require_session()
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if table == "result":
        writer.writerow([
            "player_id", "nickname", "join_tick", "ticks_played", "final_asset",
            "return_pct", "risk_adjusted", "mdd", "rank_return", "rank_risk",
            "rank_total", "eligible_for_risk",
        ])
        for s in session.scores():
            writer.writerow([
                s.player_id, s.nickname, s.join_tick, s.ticks_played, s.final_asset,
                f"{s.return_pct:.6f}", f"{s.risk_adjusted:.6f}", f"{s.mdd:.6f}",
                s.rank_return, s.rank_risk, s.rank_total, int(s.eligible_for_risk),
            ])
    else:
        rows = session.conn.execute(
            f"SELECT * FROM {table} WHERE session_id=?", (session.session_id,)
        ).fetchall()
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows([tuple(r) for r in rows])

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


@app.get("/api/meta")
async def meta():
    return {
        "reasons": list(BUY_REASONS),
        "has_session": state.session is not None,
        "status": state.session.status if state.session else "none",
    }


# ---------------------------------------------------------------- 정적 파일

if DIST_DIR.exists():
    app.mount(
        "/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets"
    )

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")
