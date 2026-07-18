# ADR-0001: 모듈형 단일 애플리케이션으로 시작한다

- 상태: Accepted
- 결정일: 2026-07-11
- 범위: MVP와 Beta 초기

## Context

초기 릴리스는 8개 도메인 계약과 연구급 수치 엔진, 결정론적 탐색·검증 흐름을 완성해야 한다. 서비스 분리는 배포·관측·일관성 문제를 먼저 만들며 현재 부하와 조직 규모는 이를 정당화하지 않는다. 반면 Lab, Engine, Storage, LLM의 경계는 향후 독립 확장을 위해 필요하다.

## Decision

하나의 repository, Python package, release를 사용한다. `domain`, `application`, `labs`, `engines`, `validation`, `infrastructure`, `interfaces`를 module boundary로 두고 public port와 import test로 의존 방향을 강제한다. MVP API와 Worker는 같은 process에서 실행한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 초기 microservices | transaction·배포·debug 비용이 MVP 가치보다 큼 |
| 계층 없는 단일 package | 빠르지만 도메인·인프라 결합을 막을 수 없음 |
| 각 Lab 독립 service | 공통 계보와 검증 계약이 안정되기 전에 중복 발생 |

## Consequences

- 긍정: 한 번의 실행과 transaction으로 E2E를 빠르게 검증한다.
- 긍정: port가 유지되므로 adapter와 process를 나중에 분리할 수 있다.
- 부정: 한 process 장애가 전체 작업에 영향을 준다.
- 부정: import 규칙을 CI로 강제하지 않으면 경계가 약해진다.

## 강제 방법

Architecture dependency test, module public surface, bootstrap 단일 조립 지점을 사용한다.

## 재검토 조건

독립 확장이 필요한 Worker 부하가 4주 연속 API 자원의 70%를 넘거나, Lab별 release 주기가 실제로 독립되고, 분산 transaction 없이 분리 가능한 port가 검증되면 process 분리를 검토한다.
