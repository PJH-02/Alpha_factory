# ADR-0005: MVP는 SQLite와 로컬 content-addressed artifact를 사용한다

- 상태: Accepted
- 결정일: 2026-07-11
- 대체 대상: 초기 PostgreSQL 우선 결정

## Context

MVP는 신뢰된 단일 조직, 단일 process, 작은 fixture에서 계약과 수치 흐름을 검증한다. 처음부터 PostgreSQL, object storage, RLS, partition을 도입하면 핵심 연구 흐름과 무관한 운영 작업이 커진다. 동시에 metadata와 큰 artifact의 저장 책임은 분리해야 한다.

## Decision

MVP metadata·상태·계보는 SQLite WAL에 저장한다. 결과·보고서·대형 배열은 SHA-256 기반 로컬 artifact 경로에 저장한다. application은 repository와 artifact port만 사용한다. Beta B1에서 동일 계약을 PostgreSQL과 object storage adapter로 교체한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| MVP부터 PostgreSQL | 설치·운영 비용이 단일 writer 범위에 불필요 |
| 모든 데이터를 SQLite BLOB | 대형 artifact dedup·검증·이동이 불편 |
| 파일만 사용 | 상태 전이, FK, idempotency, query 무결성이 약함 |

## Consequences

- 긍정: 빈 환경 설치와 로컬 재현이 단순하다.
- 긍정: metadata transaction과 artifact hash 역할이 명확하다.
- 부정: 다중 writer와 대규모 query에 적합하지 않다.
- 부정: Beta 전환 migration을 검증해야 한다.

## 강제 방법

SQLite 전용 코드는 infrastructure adapter에만 둔다. migration, backup, artifact atomic write test를 CI에 포함한다.

## 재검토 조건

다중 Worker, 원격 사용자, DB 10GB 초과, write lock 대기 p95 100ms 초과 중 하나가 발생하면 PostgreSQL 전환을 실행한다. 전환 절차는 Database 문서 10절을 따른다.
