# ADR-0002: 공통 외피와 도메인 판별형 계약을 사용한다

- 상태: Accepted
- 결정일: 2026-07-11

## Context

Factor, StatArb, Market Making 등은 연구 객체와 필수 입력이 다르다. 하나의 model에 모든 field를 optional로 추가하면 유효한 조합을 판정하기 어렵고 Factor 관점이 다른 도메인을 왜곡한다.

## Decision

Question과 Strategy는 ID, provenance, 가정, 반증 조건, 데이터 요구, 예산, 계보만 공통 외피로 공유한다. 본문은 `domain/type` discriminator가 있는 8개 독립 payload union으로 정의한다. 각 Lab이 자신의 payload schema와 validation을 소유한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 범용 optional schema | 잘못된 field 조합과 의미 없는 null이 증가 |
| 모든 전략을 단일 AST로 표현 | 공식형 Factor 외 도메인의 상태와 사건을 손실 |
| schema 없는 dict | AI와 사람 사이의 구현 계약이 검증 불가능 |

## Consequences

- 긍정: domain별 필수 field와 진화가 명확하다.
- 긍정: AI 출력과 plugin을 동일 JSON Schema로 검증한다.
- 부정: 공통 처리 전에 union 분기가 필요하다.
- 부정: domain 추가 시 schema·plugin·contract fixture를 함께 추가해야 한다.

## 강제 방법

Pydantic discriminated union, `extra="forbid"`, 8개 Lab parameterized contract test를 사용한다.

## 재검토 조건

서로 다른 세 개 이상 domain에서 의미와 validation이 완전히 같은 field 집합이 반복될 때만 공통 value object로 승격한다. payload 통합은 허용하지 않는다.
