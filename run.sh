#!/usr/bin/env bash
# 서버 실행. 프론트 빌드 결과를 같은 포트로 서빙하므로 QR 주소가 하나다.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

if [ -z "${ADMIN_PASSWORD:-}" ]; then
  echo "ADMIN_PASSWORD 가 없다. .env.example 을 .env 로 복사하고 값을 바꿔라." >&2
  exit 1
fi

if [ ! -d frontend/dist ]; then
  echo "frontend/dist 가 없다. 먼저 ./setup.sh 를 실행해라." >&2
  exit 1
fi

PORT="${PORT:-8000}"
echo
echo "  참가자 접속 주소 (휴대폰 QR 에 넣을 주소)"
for ip in $(hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || echo ""); do
  echo "    http://${ip}:${PORT}/onboard"
done
echo "  프로젝터   http://localhost:${PORT}/board"
echo "  관리자     http://localhost:${PORT}/admin"
echo

exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
