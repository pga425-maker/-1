#!/usr/bin/env python3
"""부하 테스트 — 가짜 WebSocket 50개로 375틱을 완주시킨다.

합격 기준(스펙 16번): 예외 0건, 브로드캐스트 지연 p95 < 300ms.

실제 uvicorn 서버를 띄우고 진짜 WebSocket 으로 붙는다. TestClient 로는 동시
연결 50개의 전송 지연을 재는 의미가 없다.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "load-test")
os.environ.setdefault("FESTIVAL_DB", str(ROOT / "load_test.db"))

import httpx           # noqa: E402
import uvicorn         # noqa: E402
import websockets      # noqa: E402

from backend.main import app, state        # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("LOAD_PORT", "8899"))
CLIENTS = int(os.environ.get("LOAD_CLIENTS", "50"))
SPEED = float(os.environ.get("LOAD_SPEED", "60"))
P95_LIMIT_MS = 300.0


class Recorder:
    def __init__(self) -> None:
        self.latencies: list[float] = []
        self.ticks_seen: list[int] = []
        self.errors: list[str] = []


async def client_task(token: str, rec: Recorder, stop: asyncio.Event) -> None:
    url = f"ws://{HOST}:{PORT}/ws/ticks?token={token}"
    try:
        async with websockets.connect(url, ping_interval=None) as ws:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                except asyncio.TimeoutError:
                    if stop.is_set():
                        return
                    rec.errors.append("수신 타임아웃")
                    return
                payload = json.loads(raw)
                if payload.get("type") == "tick":
                    sent = payload.get("ts")
                    if sent:
                        rec.latencies.append(time.time() * 1000.0 - sent)
                    rec.ticks_seen.append(payload["t"])
                elif payload.get("type") == "ended":
                    return
    except Exception as exc:                       # noqa: BLE001
        rec.errors.append(f"{type(exc).__name__}: {exc}")


async def main() -> int:
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    base = f"http://{HOST}:{PORT}"
    rec = Recorder()
    stop = asyncio.Event()
    failures: list[str] = []

    async with httpx.AsyncClient(base_url=base, timeout=20.0) as http:
        login = await http.post("/api/admin/login", json={"password": "load-test"})
        headers = {"X-Admin-Token": login.json()["admin_token"]}

        started = await http.post(
            "/api/admin/start",
            json={"scenario_file": "festival_01.json", "seed": 31337, "speed": SPEED},
            headers=headers,
        )
        assert started.status_code == 200, started.text
        total = state.session.scenario.total_ticks
        print(f"서버 기동, 시나리오 {started.json()['scenario']} "
              f"(배속 {SPEED}x, {total}틱)")

        tokens = []
        for i in range(CLIENTS):
            res = await http.post("/api/join", json={
                "nickname": f"부하{i:02d}", "device_token": f"load-{i:04d}-token",
            })
            tokens.append(res.json()["player_token"])
        print(f"참가 {len(tokens)}명, WebSocket 연결 시작")

        clients = [
            asyncio.create_task(client_task(t, rec, stop)) for t in tokens
        ]
        await asyncio.sleep(1.0)
        print(f"연결 완료: {len(state.hub.connections)}개")

        deadline = time.time() + 600
        while state.session.status == "running" and time.time() < deadline:
            await asyncio.sleep(0.5)

        stop.set()
        await asyncio.gather(*clients, return_exceptions=True)

        session = state.session
        health = (await http.get("/api/admin/health", headers=headers)).json()

    await server.shutdown()
    server_task.cancel()

    print()
    print(f"진행 틱: {session.tick}/{total}")
    print(f"서버 측 예외: {len(session.exceptions)}건")
    print(f"WebSocket 연결 끊김: {state.hub.dropped}건")
    print(f"클라이언트 오류: {len(rec.errors)}건")

    if rec.latencies:
        ordered = sorted(rec.latencies)
        def pct(q):
            return ordered[min(len(ordered) - 1, int(len(ordered) * q))]
        p50, p95, p99 = pct(0.5), pct(0.95), pct(0.99)
        print(f"수신 지연(클라이언트 기준) p50 {p50:.1f}ms  p95 {p95:.1f}ms  "
              f"p99 {p99:.1f}ms  최대 {ordered[-1]:.1f}ms  표본 {len(ordered)}개")
    else:
        p95 = float("inf")
        failures.append("지연 표본이 하나도 없다")

    hub = health["broadcast_latency_ms"]
    print(f"브로드캐스트 소요(서버 기준) p50 {hub['p50']:.1f}ms  "
          f"p95 {hub['p95']:.1f}ms  p99 {hub['p99']:.1f}ms")
    print(f"틱 처리 시간 평균 {health['tick_duration_ms']['mean']:.1f}ms  "
          f"최대 {health['tick_duration_ms']['max']:.1f}ms")

    if session.tick != total:
        failures.append(f"완주 실패: {session.tick}/{total}")
    if session.exceptions:
        failures.append(f"서버 예외 {len(session.exceptions)}건")
    if rec.errors:
        failures.append(f"클라이언트 오류 {len(rec.errors)}건: {rec.errors[:3]}")
    if p95 >= P95_LIMIT_MS:
        failures.append(f"지연 p95 {p95:.1f}ms 가 기준 {P95_LIMIT_MS}ms 이상")

    expected_ticks = total * CLIENTS
    received = len(rec.ticks_seen)
    print(f"수신한 틱 메시지 {received}개 (기대 {expected_ticks}개, "
          f"{received / expected_ticks * 100:.1f}%)")
    if received < expected_ticks * 0.98:
        failures.append(
            f"틱 메시지 유실: {received}/{expected_ticks}"
        )

    print()
    if failures:
        print("부하 테스트 실패")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("부하 테스트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
