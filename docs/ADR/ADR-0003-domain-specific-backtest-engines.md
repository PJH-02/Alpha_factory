# ADR-0003: 공통 Engine 계약 위에 도메인별 실행기를 둔다

- 상태: Accepted
- 결정일: 2026-07-11

## Context

패널 포트폴리오, 복수 leg 순차 상태, 주문장 사건, 시장 간 실행은 시간과 회계 모델이 다르다. 단일 백테스터를 확장하면 조건 분기와 암묵적 가정이 누적된다.

## Decision

모든 engine은 `validate_config`와 `run(config, data) -> result` 계약만 공유한다. Factor, StatArb, Market Making, Structural Flow, Cross Venue, Derivatives, Event Fundamental, Time Series는 각각 고유한 clock·fill·비용·회계 정책을 가진 연구급 수치 engine을 사용한다. Engine Registry가 명시적 key/version으로 선택한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 단일 범용 engine | 도메인별 clock·fill·회계 차이를 숨김 |
| plugin 없이 직접 호출 | routing·capability·version 추적이 어려움 |
| 8개 engine을 별도 service로 분리 | 단일 Worker·SQLite 범위에서 배포·일관성 비용만 늘어남 |

## Consequences

- 긍정: engine별 invariant와 수치 oracle이 명확하다.
- 긍정: 새 engine이 기존 domain 계산을 변경하지 않는다.
- 부정: 공통 result로 정규화하는 adapter가 필요하다.
- 부정: 일부 공통 계산이 작게 중복될 수 있다.

## 강제 방법

Engine protocol, Registry, engine contract suite, domain별 numeric oracle을 사용한다.

## 재검토 조건

두 engine에서 같은 계산이 세 번 이상 반복되고 입력·출력·오차 규칙까지 동일할 때 순수 계산 library로 추출한다. engine 자체는 통합하지 않는다.
