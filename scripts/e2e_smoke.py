#!/usr/bin/env python3
"""E2E 스모크 — 한 판을 무인으로 완주시키고 결과 CSV 가 나오는지 확인한다.

실제 FastAPI 앱에 HTTP 와 WebSocket 으로 붙는다. 배속으로 돌리지만 틱 수와
체결 규칙은 본 라운드와 같다. 공매도 집중이 최소 1회 발동하는 시나리오를 쓴다.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "e2e-test")
os.environ.setdefault("FESTIVAL_DB", str(ROOT / "e2e_smoke.db"))

from fastapi.testclient import TestClient      # noqa: E402

from backend.main import app, state            # noqa: E402

SPEED = float(os.environ.get("E2E_SPEED", "120"))
PLAYERS = int(os.environ.get("E2E_PLAYERS", "12"))
REASONS = ("실적성장", "저평가", "차트추세", "분산", "뉴스", "직감")


def main() -> int:
    failures: list[str] = []
    rng = random.Random(4242)

    with TestClient(app) as client:
        admin = client.post("/api/admin/login", json={"password": "e2e-test"})
        assert admin.status_code == 200, admin.text
        headers = {"X-Admin-Token": admin.json()["admin_token"]}

        started = client.post(
            "/api/admin/start",
            json={"scenario_file": "festival_01.json", "seed": 777, "speed": SPEED},
            headers=headers,
        )
        assert started.status_code == 200, started.text
        print(f"시작: {started.json()['scenario']} (배속 {SPEED}x, 시드 777)")

        tokens = []
        for i in range(PLAYERS):
            res = client.post("/api/join", json={
                "nickname": f"참가자{i:02d}", "device_token": f"dev-{i:04d}-abcdef",
            })
            assert res.status_code == 200, res.text
            tokens.append(res.json()["player_token"])
        print(f"참가 {len(tokens)}명")

        session = state.session
        total = session.scenario.total_ticks
        orders_sent = rejects = 0
        deadline = time.time() + 300

        while session.status == "running" and time.time() < deadline:
            time.sleep(0.05)
            token = rng.choice(tokens)
            snap = client.get("/api/state", headers={"X-Player-Token": token}).json()
            if snap["session"]["status"] != "running":
                break
            stock = rng.choice(snap["stocks"])
            me = snap["me"]
            if me is None:
                continue
            if rng.random() < 0.75:
                budget = int(me["cash"] * rng.uniform(0.1, 0.35))
                qty = budget // stock["price"]
                if qty <= 0:
                    continue
                body = {"symbol": stock["symbol"], "side": "buy", "qty": int(qty),
                        "reason": rng.choice(REASONS)}
            else:
                held = [h for h in me["holdings"] if h["quantity"] > 0]
                if not held:
                    continue
                pick = rng.choice(held)
                body = {"symbol": pick["symbol"], "side": "sell",
                        "qty": max(1, pick["quantity"] // 2), "reason": None}
            res = client.post("/api/order", json=body,
                              headers={"X-Player-Token": token})
            orders_sent += 1
            if res.status_code != 200:
                rejects += 1

        if session.status != "ended":
            client.post("/api/admin/end", headers=headers)

        print(f"완주: 틱 {session.tick}/{total}, 주문 {orders_sent}건 "
              f"(거부 {rejects}건)")

        if session.tick != total:
            failures.append(f"완주 실패: {session.tick}/{total} 틱에서 멈춤")
        if session.exceptions:
            failures.append(f"게임 루프 예외 {len(session.exceptions)}건: "
                            f"{session.exceptions[:3]}")

        fired = session.conn.execute(
            "SELECT COUNT(*) c FROM events_fired WHERE session_id=? AND type=?",
            (session.session_id, "short_pressure"),
        ).fetchone()["c"]
        print(f"공매도 집중 이벤트 {fired}회 편성")
        if fired < 1:
            failures.append("공매도 집중 이벤트가 한 번도 편성되지 않았다")

        result = client.get("/api/result", headers={"X-Player-Token": tokens[0]})
        if result.status_code != 200:
            failures.append(f"/api/result 실패: {result.status_code} {result.text[:200]}")
        else:
            payload = result.json()
            print(f"결과 분석: 원인분해 {len(payload['decomposition']['contributions'])}항목, "
                  f"근거별 {len(payload['reasons'])}종, "
                  f"군중심리 {payload['crowd']['count']}구간, "
                  f"공매도 {len(payload['short_pressure'])}건")
            if payload["me"] is None:
                failures.append("결과에 내 성적이 없다")

        for table in ("result", "orders", "tick_log"):
            res = client.get(f"/api/admin/export?table={table}", headers=headers)
            if res.status_code != 200 or not res.text.strip():
                failures.append(f"CSV 내보내기 실패: {table}")
            else:
                out = ROOT / f"export_{table}.csv"
                out.write_text(res.text, encoding="utf-8")
        print("CSV 내보내기: export_result.csv / export_orders.csv / export_tick_log.csv")

        health = client.get("/api/admin/health", headers=headers).json()
        print(f"틱 소요 평균 {health['tick_duration_ms']['mean']:.1f}ms "
              f"최대 {health['tick_duration_ms']['max']:.1f}ms")

    print()
    if failures:
        print("E2E 실패")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("E2E 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
