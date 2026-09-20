"""WebSocket 연결 관리와 브로드캐스트.

매 틱 공통 페이로드를 모든 연결에 같은 내용으로 보낸다. 개인 자산은 클라이언트가
보유 수량과 새 가격으로 직접 계산한다. 50명 x 375틱 전체를 개인화해 직렬화하면
틱 예산 4.8초 안에서 지연 p95 300ms 를 맞추기 어렵다.

보유 수량과 현금이 바뀌는 순간(체결·거부)에만 해당 연결에 개인 메시지를 따로 보낸다.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field


@dataclass
class Connection:
    websocket: object
    player_id: int | None = None
    kind: str = "player"          # player | board | admin
    send_latencies: list[float] = field(default_factory=list)


class WebSocketHub:
    def __init__(self) -> None:
        self.connections: list[Connection] = []
        self.lock = asyncio.Lock()
        self.latencies: list[float] = []
        self.dropped = 0

    async def register(
        self, websocket, player_id: int | None = None, kind: str = "player"
    ) -> Connection:
        conn = Connection(websocket=websocket, player_id=player_id, kind=kind)
        async with self.lock:
            self.connections.append(conn)
        return conn

    async def unregister(self, conn: Connection) -> None:
        async with self.lock:
            if conn in self.connections:
                self.connections.remove(conn)

    async def broadcast(self, payload: dict) -> None:
        """공통 페이로드를 뿌리고, 체결이 있었던 참가자에게만 개인 메시지를 덧붙인다."""
        fills = payload.pop("fills", {}) or {}
        common = json.dumps(payload, ensure_ascii=False, default=str)

        async with self.lock:
            targets = list(self.connections)

        started = time.perf_counter()
        results = await asyncio.gather(
            *(self._send(conn, common, fills) for conn in targets),
            return_exceptions=True,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        self.latencies.append(elapsed)

        dead = [c for c, r in zip(targets, results) if isinstance(r, Exception)]
        if dead:
            self.dropped += len(dead)
            async with self.lock:
                for conn in dead:
                    if conn in self.connections:
                        self.connections.remove(conn)

    async def _send(self, conn: Connection, common: str, fills: dict) -> None:
        await conn.websocket.send_text(common)
        if conn.player_id is not None:
            personal = fills.get(conn.player_id)
            if personal:
                await conn.websocket.send_text(json.dumps(
                    {"type": "fills", **personal},
                    ensure_ascii=False, default=str,
                ))

    def latency_percentiles(self) -> dict[str, float]:
        if not self.latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "count": 0}
        ordered = sorted(self.latencies)
        def pct(q: float) -> float:
            return ordered[min(len(ordered) - 1, int(len(ordered) * q))]
        return {
            "p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
            "max": ordered[-1], "count": len(ordered),
        }

    @property
    def player_count(self) -> int:
        return sum(1 for c in self.connections if c.kind == "player")
