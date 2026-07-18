# ADR-0010: 고정 validation, 자동 one-shot holdout, 원자적 Publication을 사용한다

- 상태: Accepted
- 결정일: 2026-07-12
- 범위: Initial release

## Context

탐색 후에 validation rule을 바꾸거나 사람이 holdout 실행 시점을 선택하면, 좋은 결과에 맞춰 기준과 접근을 반복할 수 있다. sealed data를 읽은 뒤 slot을 소비하면 crash·실패·부분 관측 뒤 재접근을 막을 수 없다. 또한 report artifact를 먼저 노출하면 registry, job 상태, 보고서가 서로 다른 결정을 보이는 partial publication이 생긴다.

## Decision Drivers

1. validation rule과 holdout selector는 탐색 결과보다 먼저 고정되어야 한다.
2. sealed holdout의 한 번뿐인 접근은 결과·실패·crash와 무관하게 lineage를 닫아야 한다.
3. holdout disclosure는 사용자 결과에 필요한 aggregate만 제공하고 연구 입력으로 되돌아가면 안 된다.
4. 사용자에게 보이는 통과 결과, registry, report, job 성공은 하나의 권위 transaction으로 함께 나타나야 한다.

## Decision

### Frozen ValidationProfile and pre-validation

도메인별 immutable `ValidationProfile`은 `SearchRun` 시작 전에 version과 semantic hash로 pin한다. profile은 score metric 정의·방향·quantization·lexicographic order, hard gate, parent/PBO eligibility, purged/embargoed walk-forward fold ID와 policy, CSCV/PBO parameter와 minimum eligible count, holdout policy, `HoldoutDisclosurePolicy` allowlist를 포함한다. SearchRun이 시작된 후 profile, fold, threshold, ranking, disclosure field를 변경할 수 없다.

holdout 이전 pre-validation은 frozen hard gate, purged/embargoed walk-forward, ADR-0009의 complete-ledger PBO certificate를 모두 통과해야 한다. PBO는 selected candidate만으로 계산하지 않으며, incomplete certificate는 validation failure다.

### Automatic pre-access holdout and lineage closure

pre-validation을 통과한 lineage는 reviewer나 사용자의 수동 sealed-run command 없이 holdout 단계로 자동 진행한다. sealed data를 읽기 전에 `BEGIN IMMEDIATE` transaction이 profile hash, valid completeness certificate, 열린 lineage, unused `HoldoutSlot`을 확인하고 slot을 consume하며 lineage를 close한 뒤 commit한다. commit이 성공한 뒤에만 sealed read를 허용한다.

slot 소비와 lineage closure는 성공, validation failure, artifact failure, process crash와 관계없이 되돌리거나 재시도하지 않는다. 같은 lineage에는 추가 holdout access가 없으며, 새 시도는 새로운 lineage로만 시작한다. selector, holdout metric의 상세값, 상세 결과는 candidate generation, search, ranking, PBO, LLM 입력에 노출하지 않는다.

### Disclosure is one-way

frozen `HoldoutDisclosurePolicy`는 최종 report에 허용된 named aggregate decision, metric, threshold만 공개한다. selector, raw row, date/range, per-observation return, fold, detailed trace, reconstruction 가능한 값은 공개하지 않는다. report와 disclosure artifact는 Knowledge, generation, failure memory, search, ranking, PBO, holdout slot, lineage-mutating service가 import할 수 없는 단방향 종단이다.

### Staged reports and atomic PUBLISHED visibility

validation pass, closed lineage, valid disclosure를 만족하지 않는 요청은 typed publication rejection을 append하고 Publication을 만들지 않는다. 적격 Publication은 `AVAILABLE`, `PREPARING`, `PUBLISHED`, `FAILED_RETRYABLE` 상태만 사용한다. owner가 아닌 동시 요청은 artifact를 stage하지 않고 기존 owner/job만 반환한다.

owner는 canonical JSON ResearchReport와 deterministic escaped HTML을 private staging area에 작성할 수 있지만, staging artifact는 권위 결과나 사용자 조회 결과가 아니다. 성공 경로는 하나의 SQLite transaction에서 exact owner CAS를 확인한 뒤 artifact metadata를 기록하고, validation·strategy·lineage를 연결하며, pass `RegistryEntry`를 삽입하고, Publication을 `PUBLISHED`로 전이하며, `PUBLISHED` event와 owner job의 `SUCCEEDED` result link를 함께 commit한다. 모두 commit되거나 모두 rollback되어야 한다.

사용자 API, CLI, registry query, 보고서 조회는 `PUBLISHED` aggregate만 표시한다. stage 실패는 `FAILED_RETRYABLE`로 남아 retry할 수 있으나, pre-access에 이미 consume된 holdout slot이나 closed lineage를 다시 열지 않는다. 이미 `PUBLISHED`인 Publication은 restart나 후속 job failure로 demote하지 않는다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| SearchRun 후 profile을 수정하거나 reviewer가 holdout을 수동 시작 | 결과를 본 뒤 validation 기준·접근 순서를 조정할 수 있다. |
| sealed read 뒤 holdout slot consume | crash 또는 부분 관측 뒤 동일 lineage의 재접근을 막지 못한다. |
| holdout detail과 intermediate report를 연구자에게 노출 | disclosure가 다음 generation·ranking·memory에 feedback으로 들어간다. |
| report/registry/job을 별도 transaction으로 공개 | partial visibility와 성공 상태 불일치를 만든다. |
| frozen profile, pre-access consumption, one-way disclosure, atomic Publication | **채택.** leakage와 공개 상태를 하나의 검증 가능한 경계로 만든다. |

## Consequences

- 긍정: validation 기준과 holdout 접근은 선택 결과보다 먼저 고정되고 lineage별로 한 번만 실행된다.
- 긍정: holdout failure와 crash도 재접근을 허용하지 않아 one-shot 의미를 유지한다.
- 긍정: 사용자에게는 complete JSON/HTML report와 pass registry가 동시에 나타난다.
- 긍정: 공개 aggregate가 탐색으로 역류하지 않아 post-validation feedback을 막는다.
- 부정: 잘못 제출하거나 장애가 난 lineage의 holdout slot도 복구할 수 없다.
- 부정: report staging과 Publication owner/CAS 상태를 별도로 관리해야 한다.

## 강제 방법

profile immutability test는 SearchRun 후 profile·fold·threshold·disclosure 변경을 거절한다. holdout race/crash test는 sealed read 전에 consume/close commit이 일어나고 재사용·reopen이 불가능함을 검증한다. architecture marker test는 report/disclosure에서 Knowledge·generation·search·ranking·PBO·lineage mutation으로의 import를 거절한다. publication fault/race test는 non-owner staging 금지, preflight rejection, atomic artifact/registry/`PUBLISHED`/job-success commit, `PUBLISHED` 불변성을 검증한다.

## 재검토 조건

holdout selector, disclosure allowlist, PBO eligibility, Publication visibility state를 변경하려면 새 immutable ValidationProfile version과 semantic hash, leakage·feedback·crash·concurrency tests, 기존 consumed slot과 PUBLISHED report의 불변성 계획을 제시한 ADR이 필요하다. 수동 holdout trigger, read-after-consume, 공개 결과의 연구 피드백, partial publication은 재검토 대상이 아니다.
