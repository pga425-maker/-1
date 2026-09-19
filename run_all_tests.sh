#!/usr/bin/env bash
# 모든 테스트를 한 번에 돌리고 test_report.md 에 결과를 남긴다.
#
#   ./run_all_tests.sh          전체
#   ./run_all_tests.sh --unit   단위 테스트만
#
# 아직 구현되지 않은 단계는 통과로 세지 않고 '미구현' 으로 기록한다.
set -uo pipefail
cd "$(dirname "$0")"

ONLY="${1:-all}"
REPORT="test_report.md"
STARTED="$(date '+%Y-%m-%d %H:%M:%S')"
FAILED=0

: > "$REPORT"
{
  echo "# 테스트 결과"
  echo
  echo "- 실행 시각: ${STARTED}"
  echo "- 실행 범위: ${ONLY}"
  echo "- 실행 환경: $(python3 --version 2>&1)"
  echo
} >> "$REPORT"

run_stage() {
  local name="$1" file="$2"; shift 2
  echo "" >> "$REPORT"
  echo "## ${name}" >> "$REPORT"
  echo "" >> "$REPORT"
  if [ ! -e "$file" ]; then
    echo "미구현 — ${file} 이 아직 없다." >> "$REPORT"
    printf '  %-28s 미구현\n' "$name"
    return 0
  fi
  echo '```' >> "$REPORT"
  if "$@" >> "$REPORT" 2>&1; then
    echo '```' >> "$REPORT"
    printf '  %-28s 통과\n' "$name"
  else
    echo '```' >> "$REPORT"
    echo "" >> "$REPORT"
    echo "**실패했다.** 위 출력의 원인을 확인할 것." >> "$REPORT"
    printf '  %-28s 실패\n' "$name"
    FAILED=1
  fi
}

echo "테스트 실행 (${ONLY})"

run_stage "단위 테스트" "backend/tests" \
  python3 -m pytest backend/tests -q --tb=short

if [ "$ONLY" = "all" ]; then
  run_stage "동시성 테스트" "backend/tests/test_concurrency.py" \
    python3 -m pytest backend/tests/test_concurrency.py -q
  run_stage "부하 테스트" "scripts/load_test.py" \
    python3 scripts/load_test.py
  run_stage "E2E 스모크" "scripts/e2e_smoke.py" \
    python3 scripts/e2e_smoke.py
fi

{
  echo
  echo "---"
  echo
  if [ "$FAILED" -eq 0 ]; then
    echo "실행한 단계는 모두 통과했다. '미구현' 으로 표시된 단계는 실행되지 않았다."
  else
    echo "실패한 단계가 있다. 위 로그를 그대로 남겨 둔다."
  fi
} >> "$REPORT"

echo
echo "결과는 ${REPORT} 에 기록했다."
exit "$FAILED"
