# ADR-0005: PostgreSQL·Parquet·Blob의 역할을 분리하고 MVP부터 PostgreSQL을 사용한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-037~044, FR-065~076, NFR-001, NFR-017~018 |

## Context

Alpha Foundry는 강한 transaction이 필요한 lineage·budget·holdout·job과 대규모 columnar scan이 필요한 시장 데이터·결과 series를 동시에 다룬다. 원 기획은 4일 동안 SQLite 사용 가능성을 열어 두었지만 SQLite는 `FOR UPDATE SKIP LOCKED`, RLS, PostgreSQL JSON/index/constraint, 다중 worker 의미를 재현하지 못한다. MVP와 Production의 영속 의미가 다르면 가장 중요한 concurrency test가 무효가 된다.

## Decision

- Metadata, state, lineage, job, audit는 MVP부터 PostgreSQL 16을 사용한다.
- 시장·panel·result series는 immutable Parquet manifest로 저장한다.
- report/model/diagnostic/Parquet part는 content-addressed Azure Blob에 저장한다.
- code, static schema, static wiki는 Git에 저장한다.
- shared mutable cache는 source of truth로 두지 않는다. immutable key의 process-local cache만 허용한다.
- SQLite compatibility와 Redis 의존 job state를 제공하지 않는다.

## 고려한 대안

| 대안 | 장점 | 기각 이유 |
|---|---|---|
| MVP SQLite → Production PostgreSQL | 초기 설치 단순 | transaction·locking·JSON·constraint가 달라 재작성과 false confidence 발생 |
| 모든 데이터 PostgreSQL | 단일 저장소 | tick/panel/series scan과 storage cost, row bloat 부적합 |
| 모든 metadata object store | 저렴·확장 | state transition, unique holdout, job claim의 원자성 부족 |
| Redis queue/cache 필수 | 빠른 queue | 영속 audit와 DB 상태 이중화, 추가 복구 의미 |

## Consequences

- local/CI에 PostgreSQL container가 필수다.
- DB와 blob 사이 atomic transaction이 없으므로 content-addressed upload 후 metadata commit과 orphan cleanup이 필요하다.
- Parquet schema/manifest versioning이 별도 계약이 된다.
- MVP에서 concurrency와 migration을 Production과 같은 의미로 검증할 수 있다.

## 강제 방법

- SQLite driver/dependency 금지
- Testcontainers PostgreSQL integration
- artifact checksum/manifest tests
- DB schema drift와 RLS tests

## 재검토 조건

job enqueue가 지속 1,000건/s를 넘거나 DB queue가 metadata SLO를 20% 이상 악화시키면 별도 durable broker를 검토한다. 그래도 authoritative job state와 holdout은 PostgreSQL에 남기며 새 ADR이 필요하다.

