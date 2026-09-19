# 테스트 결과

- 실행 시각: 2026-09-19 17:17:17
- 실행 범위: all
- 실행 환경: Python 3.11.15


## 단위 테스트

```
........................................................................ [ 66%]
.....................................                                    [100%]
=============================== warnings summary ===============================
../../../usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1
  /usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

../../../usr/local/lib/python3.11/dist-packages/starlette/testclient.py:53
  /usr/local/lib/python3.11/dist-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
109 passed, 2 warnings in 2.08s
```

## 동시성 테스트

```
....                                                                     [100%]
4 passed in 0.06s
```

## 재접속 복구

```
......                                                                   [100%]
=============================== warnings summary ===============================
../../../usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1
  /usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

../../../usr/local/lib/python3.11/dist-packages/starlette/testclient.py:53
  /usr/local/lib/python3.11/dist-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
6 passed, 2 warnings in 0.54s
```

## 부하 테스트

```
서버 기동, 시나리오 2026 축제 1회차 (배속 60.0x, 375틱)
참가 50명, WebSocket 연결 시작
연결 완료: 50개

진행 틱: 375/375
서버 측 예외: 0건
WebSocket 연결 끊김: 0건
클라이언트 오류: 0건
수신 지연(클라이언트 기준) p50 8.5ms  p95 11.6ms  p99 14.8ms  최대 17.8ms  표본 18650개
브로드캐스트 소요(서버 기준) p50 5.3ms  p95 7.6ms  p99 8.6ms
틱 처리 시간 평균 3.5ms  최대 10.6ms
수신한 틱 메시지 18650개 (기대 18750개, 99.5%)

부하 테스트 통과
```

## E2E 스모크

```
/usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
시작: 2026 축제 1회차 (배속 120.0x, 시드 777)
참가 12명
완주: 틱 375/375, 주문 258건 (거부 3건)
공매도 집중 이벤트 2회 편성
결과 분석: 원인분해 4항목, 근거별 6종, 군중심리 1구간, 공매도 2건
CSV 내보내기: export_result.csv / export_orders.csv / export_tick_log.csv
틱 소요 평균 3.5ms 최대 6.1ms

E2E 통과
```

## 분석 성향 구분

```
........                                                                 [100%]
8 passed in 0.29s
```

## 스펙 16번 합격 기준 대응

| 기준 | 어디서 확인하는가 |
|---|---|
| price_engine 불변 조건 4개 | backend/tests/test_price_engine.py |
| 공매도 펌프 < 덤프 불변 조건 | backend/tests/test_event_scheduler.py |
| 공포지수 0~100 clamp | backend/tests/test_index_engine.py |
| 뉴스 지연 타이밍 | backend/tests/test_event_scheduler.py |
| scoring 표준편차 0 | backend/tests/test_scoring.py |
| 주문 검증 7종 거부 | backend/tests/test_orders.py |
| 동시 주문 100건, 잔고 보존 | backend/tests/test_concurrency.py |
| 재접속 시 보유·잔고 복구 | backend/tests/test_reconnect.py |
| WS 50개로 375틱, p95 < 300ms | scripts/load_test.py |
| 1판 무인 완주 + CSV 생성 | scripts/e2e_smoke.py |
| 성향 5유형 구분 | backend/tests/test_analysis.py |

---

실행한 단계는 모두 통과했다. '미구현' 으로 표시된 단계는 실행되지 않았다.
