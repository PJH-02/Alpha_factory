# ADR-0008: 도메인 결합은 제한된 interface와 Meta Portfolio에서만 수행한다

- 상태: Accepted
- 결정일: 2026-07-11

## Context

도메인 경계 없이 signal, spread, order-book feature를 결합하면 질문 의미, 시간 가용성, engine 책임이 불명확해진다. 반면 검증된 전략의 자본 배분이나 명시적 risk input은 실제로 필요하다.

## Decision

각 연구는 하나의 주 도메인을 가진다. 다른 도메인 정보는 등록된 보조 interface로만 입력한다. MVP 허용 interface는 `RISK_EXPOSURE`, `REGIME_LABEL`, `EVENT_CALENDAR`, `LIQUIDITY_LIMIT`이다. 보조 입력은 독립 dataset/version과 ablation 결과를 기록한다. 검증 완료 전략의 결합은 Meta Portfolio allocator만 수행한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 자유로운 feature 공유 | 누수와 책임 경계를 감사하기 어려움 |
| 모든 결합 금지 | 위험·유동성·이벤트 제약을 재사용할 수 없음 |
| 하나의 거대 Meta model | domain engine과 검증 의미를 상실 |

## Consequences

- 긍정: 주 engine과 validation 책임이 명확하다.
- 긍정: 보조 정보의 기여를 ablation으로 검증한다.
- 부정: 새 결합 방식은 interface 등록과 검토가 필요하다.
- 부정: 빠른 임의 실험은 제한된다.

## 강제 방법

DomainRoute의 허용 interface enum, Lab import test, input provenance, ablation gate를 사용한다.

## 재검토 조건

새 interface는 세 개 이상의 정당한 use case, point-in-time 규칙, 소유 Lab, ablation 계획, validation rule을 제시한 ADR이 승인될 때만 추가한다.
