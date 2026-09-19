#!/usr/bin/env bash
# 리허설 한 줄 실행.
#
#   ./scripts/run_rehearsal.sh 50        봇 50명으로 본 리허설 + 스윕
#   ./scripts/run_rehearsal.sh 50 --no-sweep
#
# 실제 서버를 띄워 HTTP 와 WebSocket 으로 붙으므로 서버, DB, 체결 로직이 전부 돈다.
# 결과는 rehearsal_report.md 에 쌓인다.
set -euo pipefail
cd "$(dirname "$0")/.."

PLAYERS="${1:-50}"
shift || true

echo "리허설 시작: 봇 ${PLAYERS}명"
exec python3 scripts/rehearsal.py "${PLAYERS}" "$@"
