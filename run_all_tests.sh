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
  python3 -m pytest backend/tests -q --tb=short --ignore=backend/tests/test_concurrency.py

if [ "$ONLY" = "all" ]; then
  run_stage "동시성 테스트" "backend/tests/test_concurrency.py" \
    python3 -m pytest backend/tests/test_concurrency.py -q
  run_stage "재접속 복구" "backend/tests/test_reconnect.py" \
    python3 -m pytest backend/tests/test_reconnect.py -q
  run_stage "부하 테스트" "scripts/load_test.py" \
    python3 scripts/load_test.py
  run_stage "E2E 스모크" "scripts/e2e_smoke.py" \
    python3 scripts/e2e_smoke.py
  run_stage "분석 성향 구분" "backend/tests/test_analysis.py" \
    python3 -m pytest backend/tests/test_analysis.py -q
fi

{
  echo
  echo "## 스펙 16번 합격 기준 대응"
  echo
  echo "| 기준 | 어디서 확인하는가 |"
  echo "|---|---|"
  echo "| price_engine 불변 조건 4개 | backend/tests/test_price_engine.py |"
  echo "| 공매도 펌프 < 덤프 불변 조건 | backend/tests/test_event_scheduler.py |"
  echo "| 공포지수 0~100 clamp | backend/tests/test_index_engine.py |"
  echo "| 뉴스 지연 타이밍 | backend/tests/test_event_scheduler.py |"
  echo "| scoring 표준편차 0 | backend/tests/test_scoring.py |"
  echo "| 주문 검증 7종 거부 | backend/tests/test_orders.py |"
  echo "| 동시 주문 100건, 잔고 보존 | backend/tests/test_concurrency.py |"
  echo "| 재접속 시 보유·잔고 복구 | backend/tests/test_reconnect.py |"
  echo "| WS 50개로 375틱, p95 < 300ms | scripts/load_test.py |"
  echo "| 1판 무인 완주 + CSV 생성 | scripts/e2e_smoke.py |"
  echo "| 성향 5유형 구분 | backend/tests/test_analysis.py |"
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
