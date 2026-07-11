# ADR-0002: 공통 외피와 도메인 판별형 계약을 사용한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-016~033, NFR-014~015 |

## Context

팩터는 자산별 score와 portfolio mapping을, StatArb는 관계·hedge·spread를, MM은 order book·queue·inventory를 연구한다. 공통 필드의 거대한 schema는 어떤 도메인에도 정확하지 않고 다수 nullable field와 해석 분기를 만든다. 완전 별도 API는 provenance, budget, evidence, 상태, audit를 중복시킨다.

## Decision

`ResearchQuestion`, `HypothesisSpec`, `StrategySpec`은 공통 외피와 `domain` discriminator가 선택하는 strict payload union으로 정의한다.

- Built-in domain은 8개다.
- 공통 외피는 ID, lineage, evidence, assumption, falsifier, data/engine requirement, budget, provenance만 소유한다.
- 각 lab은 question/hypothesis/strategy payload, audit rule, memory schema를 독립 소유한다.
- `additionalProperties=false`와 schema version을 강제한다.
- domain과 payload, engine, validator, memory가 다르면 compile/run 이전에 실패한다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| 모든 field를 가진 universal schema | nullable 의미, 도메인 오용, validation 복잡도 증가 |
| 자유 형식 JSON payload | AI와 plugin이 숨은 필드·기본값을 발명할 수 있음 |
| 도메인별 완전 별도 시스템 | 공통 lineage·budget·holdout·report가 중복되고 결합 통제가 약해짐 |
| Factor AST를 모든 전략에 확장 | queue, multi-leg, cashflow, event state를 표현하지 못함 |

## Consequences

- 새 domain 추가는 schema, lab, engine profile, validator, fixture를 모두 요구한다.
- 공통 기능은 domain payload를 해석하지 않고 discriminator와 공통 외피만 다룬다.
- schema migration 수가 늘지만 잘못된 공통 추상화보다 국소적이다.
- AI context는 대상 domain schema만 포함해 작고 명확하게 유지된다.

## 강제 방법

- 8개 valid/invalid union contract test
- cross-domain payload swap test
- plugin registration compatibility check
- import-linter와 registry domain equality check

## 재검토 조건

두 domain에서 payload field와 validation 의미가 90% 이상 동일하고 3개 release 동안 독립 변화가 없을 때만 공통 subtype 추출을 검토한다. 단순 이름 유사성은 근거가 아니다.

