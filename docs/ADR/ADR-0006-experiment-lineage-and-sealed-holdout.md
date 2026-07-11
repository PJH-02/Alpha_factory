# ADR-0006: 탐색을 계보화하고 sealed holdout을 계보당 한 번만 허용한다

- 상태: Accepted
- 결정일: 2026-07-11

## Context

후보를 반복 평가하고 좋은 결과만 선택하면 holdout도 사실상 학습 데이터가 된다. 단순 experiment ID만으로는 어떤 수정이 어떤 결과를 보고 이뤄졌는지 추적할 수 없다.

## Decision

모든 Mandate는 lineage ID를 가진다. 질문·가설·전략 revision과 실험·탈락은 append-only search event로 연결한다. sealed holdout access는 Reviewer 승인 후 lineage당 한 번 transaction으로 예약한다. 실행이 중단되어도 access를 되돌리지 않는다. LLM과 후속 후보 생성에는 sealed 상세를 제공하지 않는다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| 사용자 주의에 의존 | 자동화된 반복 탐색을 막을 수 없음 |
| experiment당 1회 | 새 experiment ID로 우회 가능 |
| 실패 시 access 복원 | 결과 일부 노출 여부를 증명하기 어려움 |

## Consequences

- 긍정: 선택 편향과 holdout 오염을 감사할 수 있다.
- 긍정: 실패 연구도 재사용 가능한 기억이 된다.
- 부정: 실수나 장애로 소비한 holdout을 같은 lineage에서 복구할 수 없다.
- 부정: 새 lineage 생성 사유를 사람이 검토해야 한다.

## 강제 방법

`holdout_accesses.lineage_id` UNIQUE, `BEGIN IMMEDIATE` 예약, append-only search event, sealed redaction test를 사용한다.

## 재검토 조건

1회 제한은 완화하지 않는다. 여러 독립 holdout이 필요하면 dataset 준비 단계에서 목적과 selector를 분리하고 별도 사전 등록 lineage 정책을 새 ADR로 정의한다.
