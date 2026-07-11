# ADR-0006: 모든 탐색을 계보화하고 sealed holdout을 계보당 한 번만 허용한다

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 결정일 | 2026-07-11 |
| 관련 요구사항 | FR-037~038, FR-054~065, NFR-001~003, NFR-018~019 |

## Context

질문, prompt, feature, window, 비용, 기간, engine을 바꾸며 validation/holdout을 반복하면 nominal OOS도 사실상 training data가 된다. 이름 변경이나 새 strategy ID만으로 같은 탐색을 숨길 수 있다. 최종 수치를 생성기에 노출하면 선택 편향과 backtest overfitting을 통제할 수 없다.

## Decision

- mandate root에서 question, hypothesis, strategy, experiment로 이어지는 `lineage_id`를 보존한다.
- 연구 결과에 영향을 줄 수 있는 모든 변경을 append-only `search_event`로 기록하고 예산을 차감한다.
- train은 상세 feedback, validation은 coarse generator feedback, sealed는 generator 접근 금지다.
- G0~G8 통과, strategy/config freeze, Human Reviewer 승인 후 lineage당 sealed access를 정확히 한 번 허용한다.
- access 예약 시점에 소비하며 job 실패·취소로 되돌리지 않는다. 같은 access-bound job만 재개할 수 있다.
- sealed 결과 이후 같은 lineage 수정과 재평가를 금지한다. 새 연구는 새 root mandate와 별도 holdout 정책으로 시작한다.

## 고려한 대안

| 대안 | 기각 이유 |
|---|---|
| holdout 반복 후 Bonferroni 보정 | 적응적 변경·prompt 탐색을 완전히 복원하지 못함 |
| rolling OOS만 사용하고 sealed 없음 | 최종 선택 과정의 누적 피드백 통제 부족 |
| strategy ID별 1회 | 이름 변경으로 우회 가능 |
| 결과를 연구자·generator 모두 완전 은폐 | 연구 검토와 사람 승인에 필요한 evidence 부족 |

## Consequences

- 연구 속도보다 최종 검증의 독립성을 우선한다.
- infrastructure 실패도 access budget을 소모하므로 sealed 실행 전 preflight와 운영 안정성이 중요하다.
- 여러 아이디어를 독립 holdout으로 평가하려면 처음부터 별도 mandate와 계보가 필요하다.
- 감사자는 prompt/model/config 변화와 metric exposure를 재구성할 수 있다.

## 강제 방법

- `(tenant_id,lineage_id)` unique holdout constraint
- lineage row lock과 irreversible sealed state
- alias/name/content duplicate tests
- sealed DB role, feedback view, concurrent access red team

## 재검토 조건

새 데이터가 시간 경과로 축적돼 기존 연구 당시 존재하지 않은 완전히 새로운 holdout period가 생기면 새 root mandate로 재검증할 수 있다. 기존 holdout 재개방은 허용하지 않는다.

