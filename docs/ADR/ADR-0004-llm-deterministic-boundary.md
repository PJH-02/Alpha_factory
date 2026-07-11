# ADR-0004: LLM 생성과 결정론적 판정의 경계를 강제한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-004, FR-018~025, FR-040~044, FR-054~064, NFR-019, NFR-024 |

## Context

LLM은 자연어 구조화, 문헌 연결, 경제 메커니즘, 질문 다양화에 유용하지만 수치 계산, schema 안정성, 반복 종료, 데이터 시점, 통과 판정에 신뢰할 수 있는 권위가 아니다. 자유로운 code 생성과 self-reflection loop는 감사 불가능한 탐색 횟수와 holdout leakage를 만든다.

## Decision

- LLM은 mandate/question/hypothesis/strategy draft와 정성 해석만 생성한다.
- 모든 output은 versioned strict schema를 통과해야 한다.
- 수치, 통계, backtest, 비용·위험, fingerprint, 예산, 상태 전이, gate decision은 deterministic code가 수행한다.
- schema repair는 1회, lineage revision은 mandate budget 이하, 기본 최대 2회다.
- validation은 coarse feedback만, sealed result는 LLM context에 전혀 제공하지 않는다.
- LLM이 생성한 code는 실행하지 않는다. StrategySpec은 declarative schema와 등록 plugin만 사용한다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| LLM이 code를 만들고 sandbox 실행 | code/data exfiltration, 비결정성, 탐색 원장 누락 위험 |
| 성과가 오를 때까지 agent loop | multiple testing과 holdout 오염, 종료 불명 |
| LLM을 전혀 사용하지 않음 | 자연어 mandate와 지식 기반 질문 다양성 목표 상실 |
| LLM score로 최종 판정 | 계산 근거와 threshold를 재현할 수 없음 |

## Consequences

- creativity와 correctness가 분리되고 실패 원인을 명확히 감사할 수 있다.
- schema가 표현하지 못하는 새 아이디어는 즉시 실행되지 않고 contract 변경 절차를 거친다.
- provider 교체가 engine 결과를 직접 바꾸지 않지만 질문 집합은 달라질 수 있어 model/prompt가 search event가 된다.
- token과 latency 비용을 사전에 예산화할 수 있다.

## 강제 방법

- structured output + local strict validation
- LLM port와 engine package의 import 분리
- feedback view type과 sealed repository 권한
- prompt/model 변경 search ledger test
- AI actor의 approval transition 거부

## 재검토 조건

검증 가능한 constrained program synthesis가 도입돼도 generated artifact의 정적 분석, deterministic sandbox, lineage, risk review를 포함한 별도 ADR 없이는 실행 범위를 넓히지 않는다.

