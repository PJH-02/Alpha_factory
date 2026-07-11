# ADR-0001: 모듈형 단일 애플리케이션으로 시작한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 소유자 | Principal Architect |
| 관련 요구사항 | NFR-015, NFR-016, FR-071~075 |

## Context

Alpha Foundry는 8개 연구 도메인, 여러 engine, knowledge, validation, ledger, job을 포함하지만 MVP 기간은 4일이고 전체 기준선도 12주다. 초기부터 microservice로 나누면 API/event contract, network failure, 분산 tracing, 배포, schema coordination이 domain correctness보다 앞서게 된다. 반대로 경계 없는 monolith는 Factor 중심 모델이 다른 도메인으로 침투하고 향후 분리가 불가능해진다.

## Decision

하나의 repository, 하나의 version, 하나의 release manifest를 갖는 모듈형 단일 애플리케이션을 사용한다.

- `core`, `application`, `orchestration`, `knowledge`, `labs`, `engines`, `validation`, `ledger`, `registry`, `infrastructure`를 package boundary로 분리한다.
- API, Worker, Outbox Dispatcher, Scheduler는 같은 codebase를 사용하는 별도 process다.
- module 간 호출은 typed in-process interface/port를 사용한다.
- import-linter와 architecture test로 dependency 방향을 강제한다.
- DB transaction이 필요한 상태 전이는 한 process boundary 안에서 원자적으로 처리한다.

## 고려한 대안

| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 도메인별 microservice | 독립 배포·scale·failure isolation | 초기 contract·운영 비용이 과도하고 cross-cutting transaction이 분산됨 |
| 경계 없는 monolith | 가장 빠른 초기 코딩 | domain leakage, 순환 의존, engine 재사용 오용 가능성 |
| 외부 workflow/SaaS 중심 | scheduler·UI 확보 | holdout·lineage·domain state의 통제와 재현성이 vendor 모델에 종속 |

## Consequences

### 긍정

- 4일 MVP에서 transaction과 contract correctness에 집중할 수 있다.
- local과 Production이 같은 code path를 사용한다.
- domain package와 process는 독립 scale·분리 후보로 측정할 수 있다.

### 부정

- 전체 codebase가 함께 release되며 한 package의 dependency upgrade가 전체 test를 요구한다.
- CPU-heavy engine isolation을 process 수준에서 별도 구현해야 한다.
- module boundary를 사람이 느슨하게 다루면 구조가 빠르게 무너진다.

## 강제 방법

- forbidden import contract
- domain package 간 직접 import 0건
- application port 외 infrastructure import 차단
- release 전 전체 contract suite

## 재검토 조건

다음 중 둘 이상이 2개 release 연속 발생하면 특정 module의 service 분리를 검토한다.

- 독립 scale 요구가 다른 module 대비 10배 이상
- 장애가 다른 연구 흐름을 반복적으로 중단
- 별도 보안 경계 또는 독립 배포가 규제상 요구
- release cadence 충돌로 월 2회 이상 배포 차단

분리는 새 ADR과 versioned event/API contract가 선행돼야 한다.

