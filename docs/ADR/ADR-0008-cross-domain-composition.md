# ADR-0008: 초기 릴리스에서는 교차 도메인 구성과 Meta Portfolio를 연기한다

- 상태: Accepted
- 결정일: 2026-07-12
- 범위: Initial release

## Context

Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series는 입력의 point-in-time 의미, 실행·비용·회계 정책, 검증 불변식이 서로 다르다. 제한된 보조 interface나 Meta Portfolio도 결국 한 Lab의 신호·상태·검증 결과를 다른 Lab 또는 allocator의 입력으로 만들므로, 계보·누수·책임 경계를 별도로 증명해야 한다.

초기 릴리스는 먼저 8개 독립 Lab의 typed 계약과 연구급 엔진, 유한 탐색, 검증을 완성해야 한다. 교차 도메인 구성을 이 범위에 포함하면 검증되지 않은 공통 표현과 portfolio 계층을 먼저 고정하게 된다.

## Decision Drivers

1. 도메인별 데이터 가용성, execution policy, accounting invariant와 validation 책임을 명시적으로 유지한다.
2. 교차 Lab 신호 전달로 생기는 시간 누수와 다중 탐색 경로를 초기 릴리스에서 만들지 않는다.
3. 8개 엔진의 검증 가능한 완성을 우선하고, 추측성 composition 계층을 추가하지 않는다.
4. KISS 원칙에 따라 초기 릴리스의 사용자 결과를 단일 도메인 전략 revision으로 한정한다.

## Decision

초기 릴리스의 모든 `StrategySpec`, `SearchRun`, `ValidationProfile`, `RegistryEntry`는 정확히 하나의 `primary_domain`을 가진다. 각 Lab은 자신의 typed 계약, 데이터, execution policy, engine, validation evidence만 소유하고 다른 Lab의 내부 signal, feature, execution state, validation artifact를 읽지 않는다.

교차 도메인 feature·strategy composition과 `Meta Portfolio` allocator는 명시적으로 연기한다. 초기 릴리스에는 composition package, API endpoint, schema, database table, registry type, portfolio 결과물이 없다. canonical encoding, artifact storage처럼 도메인 의미나 전략 정보를 전달하지 않는 proven-identical primitive만 공용으로 둘 수 있다.

후속 릴리스에서 composition을 도입하려면 이 결정을 대체하는 새 ADR과 별도의 typed 계약을 승인해야 한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 제한된 보조 interface와 Meta Portfolio를 초기부터 허용 | 허용된 interface도 cross-Lab 입력·계보·validation 의미를 만들며, composition 책임을 숨길 수 있다. |
| 자유로운 feature와 전략 결합 | point-in-time 가용성, 비용·회계, 누수 방지를 감사할 수 없다. |
| 하나의 거대 Meta model | 도메인별 engine과 validation의 의미·소유권을 잃는다. |
| composition을 별도 후속 ADR로 연기 | **채택.** 독립 Lab의 권위 계약을 먼저 검증한다. |

## Consequences

- 긍정: 각 전략의 데이터, 실행, 회계, validation 책임이 하나의 Lab에 남는다.
- 긍정: 초기 릴리스의 search lineage와 holdout 증거가 단일 도메인 의미를 유지한다.
- 긍정: 구현 범위가 8개 독립 수직 경로로 제한된다.
- 부정: 여러 도메인 신호를 합친 전략과 자본 배분 결과를 제공하지 않는다.
- 부정: 공통 risk·regime·event 정보를 전략 입력으로 재사용하는 편의도 후속 계약까지 제공하지 않는다.

## 강제 방법

architecture 검사와 계약 테스트는 composition module·import, Meta Portfolio API/schema/database 자산, 다중 도메인 `primary_domain`, cross-Lab 내부 참조를 거절한다. OpenAPI, persistence schema, registry, 보고서에는 단일 도메인 revision만 노출한다.

## 재검토 조건

8개 독립 Lab의 데이터·engine·validation·holdout 계약이 검증된 뒤에만 재검토한다. 제안 ADR은 최소한 source/target Lab 소유권, point-in-time availability, typed input schema와 semantic hash, execution·accounting 책임, ablation과 validation 계획, lineage/PBO/holdout 영향, disclosure와 no-feedback 경계를 명시해야 한다. 이 증거 없이 composition 또는 `Meta Portfolio`를 추가하지 않는다.
