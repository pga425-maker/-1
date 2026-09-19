#!/usr/bin/env bash
# 설치와 빌드. 인터넷이 있는 곳에서 미리 한 번 돌려 둔다.
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] 파이썬 버전 확인"
python3 --version
python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"파이썬 3.11 이상이 필요하다 (현재 {sys.version.split()[0]})")
PY

echo "[2/4] 파이썬 패키지 설치"
python3 -m pip install --quiet -r requirements.txt

echo "[3/4] 프론트 의존성 설치와 빌드"
cd frontend
npm install --no-audit --no-fund
npm run build
cd ..

echo "[4/4] 폰트 번들 확인"
python3 - <<'PY'
from pathlib import Path
files = list(Path("frontend/dist/fonts").glob("*.woff2"))
css = Path("frontend/dist/fonts/fonts.css")
if not files or not css.exists():
    raise SystemExit("폰트 번들이 없다. frontend/public/fonts 를 확인해라.")
if "http" in css.read_text():
    raise SystemExit("폰트 CSS 가 외부 주소를 참조한다. 오프라인에서 깨진다.")
print(f"  woff2 {len(files)}개, 외부 참조 없음")
PY

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "  .env 를 만들었다. ADMIN_PASSWORD 를 바꿔라."
fi

echo
echo "준비 끝. ./run.sh 로 실행해라."
