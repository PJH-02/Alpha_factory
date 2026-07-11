# ADR-0007: MVP는 영속 단일 Worker job queue를 사용한다

- 상태: Accepted
- 결정일: 2026-07-11
- 범위: MVP

## Context

LLM과 engine 실행은 HTTP 요청보다 길고 process 재시작 후 상태 조회가 필요하다. 그러나 MVP에서 다중 Worker lease, broker, transactional outbox까지 구현하면 복구 모델이 불필요하게 복잡해진다.

## Decision

HTTP/CLI command는 SQLite `jobs`에 기록하고 job ID를 반환한다. process당 Worker 하나가 가장 오래된 QUEUED job을 RUNNING으로 전이해 실행한다. process 시작 시 RUNNING job을 QUEUED로 복구한다. 완료 experiment fingerprint와 artifact commit이 중복 실행을 막는다. sealed access는 복구하지 않는다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 요청 thread에서 동기 실행 | timeout과 재시작 조회 요구를 충족하지 못함 |
| Redis/Celery | 추가 인프라와 두 저장소 일관성 필요 |
| PostgreSQL lease/outbox | 다중 Worker가 없는 MVP에 과도함 |

## Consequences

- 긍정: API는 즉시 응답하고 작업은 재시작 후 조회 가능하다.
- 긍정: queue 상태와 domain 상태가 한 DB에 있다.
- 부정: 한 시점에 한 job만 실행한다.
- 부정: 장기 실행 job이 뒤 작업을 지연할 수 있다.

## 강제 방법

단일 Worker lock, job transition test, startup recovery test, idempotency key와 experiment fingerprint를 사용한다.

## 재검토 조건

queue 대기 p95가 5분을 넘거나 독립 job 2개 이상을 병렬 실행해야 하면 Beta B1에서 PostgreSQL lease 기반 다중 Worker를 도입한다. lease, heartbeat, idempotent side effect, 장애 주입 test가 모두 통과하기 전에는 Worker 수를 늘리지 않는다.
