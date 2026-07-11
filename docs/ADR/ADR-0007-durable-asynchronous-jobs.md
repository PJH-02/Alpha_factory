# ADR-0007: PostgreSQL lease 기반 영속 비동기 job과 transactional outbox를 사용한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-071~076, NFR-004~007, NFR-017~018 |

## Context

질문 생성, backtest, validation, report는 HTTP request lifetime을 넘는다. Process memory queue는 재시작 시 작업과 lineage를 잃고 sync API는 timeout과 중복 재요청을 유발한다. 초기 규모에서 별도 broker/workflow platform은 DB aggregate와 job 상태의 이중 기록 문제를 만든다.

## Decision

- API는 장시간 command를 `jobs`에 기록하고 202를 반환한다.
- Worker는 `FOR UPDATE SKIP LOCKED`로 claim하고 60초 lease, 15초 heartbeat를 사용한다.
- transient infrastructure failure만 최대 3회 재시도한다.
- side effect는 idempotency key, fingerprint, content hash, aggregate version으로 deduplicate한다.
- business state와 integration event는 같은 transaction의 outbox row로 기록한다.
- outbox 전달은 at-least-once이며 consumer가 event ID로 deduplicate한다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| synchronous HTTP | timeout, retry 중복, progress/recovery 부재 |
| in-memory/background task | process 종료 시 유실, scale-out claim 불가 |
| Celery/Redis | broker·result backend·DB 상태의 3중 의미와 운영 복잡도 |
| 외부 workflow engine | 초기 범위에 비해 contract·deployment 비용 과다 |

## Consequences

- PostgreSQL queue 부하를 모니터링하고 partial index/retention을 관리해야 한다.
- exactly-once 전달을 주장하지 않으며 모든 handler가 idempotent해야 한다.
- CPU engine은 job coordinator와 별도 subprocess로 격리한다.
- DB 장애 시 새 작업은 중단되지만 committed job과 상태는 보존된다.

## 강제 방법

- concurrent claim/lease recovery/process-kill tests
- state+outbox atomic rollback test
- duplicate event 100회 test
- retryable error taxonomy contract

## 재검토 조건

queue throughput, scheduling 기능, multi-day DAG가 PostgreSQL SLO를 넘으면 broker/workflow engine 분리를 검토한다. 분리 후에도 authoritative job/holdout/ledger state는 PostgreSQL이고 outbox bridge를 사용한다.

