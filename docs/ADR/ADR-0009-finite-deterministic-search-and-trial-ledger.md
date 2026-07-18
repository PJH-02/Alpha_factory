# ADR-0009: 유한 결정론 탐색과 완전 trial ledger를 사용한다

- 상태: Accepted
- 결정일: 2026-07-12
- 범위: Initial release

## Context

전략 탐색은 같은 입력에서 같은 후보, 순위, 종료 사유와 검증 증거를 재현해야 한다. 무제한·비결정론적 탐색, live parent ranking, 후보의 일부만 남기는 원장은 탐색 편향과 PBO 입력 누락을 감사할 수 없게 만든다. 임의의 세대 상한은 유한 종료를 보장하는 계약이 아니며, 탐색 결과를 바꿀 수 있다.

## Decision Drivers

1. 후보와 탐색 설정의 의미적 변경이 반드시 identity 변경으로 이어져야 한다.
2. 모든 slot과 후보 선택은 DB 시점·동시 완료 순서에 영향받지 않아야 한다.
3. PBO는 선택된 후보만이 아니라 같은 lineage의 완전한 trial 집합을 사용해야 한다.
4. 정상 종료는 재현 가능하고 유한해야 하며, 임의의 generation cap에 의존하지 않아야 한다.

## Decision

### Exhaustive canonical identities

모든 normative digest는 `AF-CANON-1`의 canonical typed encoding에 domain separator를 붙여 SHA-256으로 계산한다. named field는 byte 순서로 정렬하고, 정수·`Decimal`·UTC timestamp·list·object의 encoding 규칙을 고정한다. NaN, Infinity, implementation-defined 값, 알려지지 않은 identity field는 거절한다.

다음 identity는 명시한 field 전부를 포함한다. 표에 없는 의미적 입력을 암묵적으로 포함하거나, 표의 field를 무시해서는 안 된다.

| Identity | Domain separator | Exhaustive direct fields |
| --- | --- | --- |
| Candidate | `AF:CANDIDATE:1` | `domain:string`; `operator_set_hash:bytes32`; `strategy_ast:object` |
| Universe | `AF:UNIVERSE:1` | raw-byte 오름차순 `candidate_hashes:list[bytes32]`; `domain:string`; `operator_set_hash:bytes32`; `version:string` |
| SearchSpec | `AF:SEARCH_SPEC:1` | `code_hash:bytes32`; raw-byte 오름차순 `dataset_hashes:list[bytes32]`; `domain:string`; `initial_population_size:int`; `offspring_count:int`; 선언 순서 `operator_schedule:list[string]`; `operator_set_hash:bytes32`; `parent_pool_size:int`; `patience:int`; raw-byte 오름차순 `policy_hashes:list[bytes32]`; `profile_hash:bytes32`; raw-byte 오름차순 `schema_hashes:list[bytes32]`; `seed:int`; `universe_hash:bytes32` |
| Traversal | `AF:TRAVERSAL:1` | `candidate_hash:bytes32`; `seed:int` |
| Parent alternative | `AF:PARENT:1` | `generation_index:int`; `operator_id:string`; operator tuple 순서 `parent_hashes:list[bytes32]`; `schedule_offset:int`; `seed:int`; `slot_index:int` |
| Parameter alternative | `AF:PARAMETER:1` | `generation_index:int`; `operator_id:string`; `parameter_indices:list[int]`; `schedule_offset:int`; `seed:int`; `slot_index:int` |
| Parent-pool snapshot | `AF:PARENT_POOL:1` | `generation_index:int`; exact ranked-order `parent_candidate_hashes:list[bytes32]`; `profile_hash:bytes32`; `search_spec_hash:bytes32` |

`operator_set_hash`는 operator ID/version, arity, commutativity, compiler semantic version, AST type, typed parameter grid와 정렬·deduplication 규칙, mutation/crossover semantics, validity predicate, 참조 schema를 전이적으로 포함한다. `profile_hash`는 score metric·방향·quantization, hard gate, tie-break, patience, fold, PBO, holdout, disclosure policy, 참조 validation/schema 구현을 전이적으로 포함한다. dataset, policy, schema, code hash도 각각의 완전한 immutable resource를 덮는다. 이 항목 중 의미적 변경은 반드시 resource hash와 `SearchSpec` digest를 변경한다.

### Finite universe, ranking, and frozen parent pools

`Universe`는 중복 없는 Candidate hash의 raw-byte 정렬 집합이다. 후보 traversal은 `(TraversalDigest, candidate_hash)` 순으로 고정한다. `SearchSpec`에는 `max_generations`가 없으며, API, persistence, resource, digest, 구현에도 둘 수 없다.

각 offspring generation `g >= 1`의 slot 0 전에, 이전에 terminalized된 `EVALUATED` trial 중 frozen hard gate를 모두 통과하고 complete·finite·quantized score vector를 가진 후보만 읽는다. 후보는 frozen profile의 lexicographic field order, direction, quantized value, raw candidate hash 오름차순으로 정렬하고 상위 `min(parent_pool_size, eligible_count)`을 하나의 immutable Parent-pool snapshot으로 기록한다. generation의 모든 slot은 이 exact order만 읽으며, generation 시작 뒤 완료된 trial은 다음 generation까지 parent가 될 수 없다.

### Deterministic slots and termination

generation 0은 traversal의 처음 `min(initial_population_size, universe_size)` 후보를 시작한다. offspring slot은 schedule offset 오름차순, `schedule[(g * offspring_count + slot + offset) mod schedule_length]`, frozen parent-pool에서 유도한 Parent digest 순서의 legal tuple, Parameter digest 순서의 parameter vector를 차례로 소비한다. mutation parent는 중복 없이 한 번씩, non-commutative crossover는 ordered distinct pair, commutative crossover는 하나의 canonical pair만 사용한다.

각 slot은 schema-valid, in-universe, run-scoped `proposed`/`visited`에 없는 첫 alternative만 채택한다. 소비한 invalid, outside-universe, duplicate alternative는 각각 append-only event로 남긴다. 채택은 proposal 기록, `proposed` 추가, 정확히 하나의 STARTED trial, `visited` 추가를 원자적으로 수행한다. alternative가 소진되면 traversal을 한 번 스캔하는 direct fallback으로 첫 unseen 후보를 시작하고, 없으면 `SLOT_EXHAUSTED`를 남긴다. slot당 trial은 0 또는 1개이며 `proposed`와 `visited`는 run 전체에서 재설정하지 않는다.

정상 종료 사유는 `PLATEAU`와 `UNIVERSE_EXHAUSTED`뿐이다. complete generation의 quantized strict improvement만 patience를 reset하고, 동점·악화·eligible vector 부재는 정의된 patience를 증가시킨다. zero-trial generation은 plateau를 변경하지 않는다. universe exhaustion과 plateau가 동시에 성립하면 exhaustion이 우선한다. cancellation, interruption, invariant 위반은 정상 종료가 아닌 typed failure다.

### Write-ahead trial ledger and PBO completeness

preflight나 engine 실행 전에 contiguous ledger position, immutable candidate/operator relation, `CandidateTrial STARTED`, `TRIAL_STARTED` event를 하나의 짧은 transaction으로 기록한다. 모든 시작 trial은 정확히 하나의 `EVALUATED`, `REJECTED_PREFLIGHT`, `REJECTED_HARD_GATE`, `ENGINE_FAILED`, `INTERRUPTED` terminal outcome을 가진다. 시작만 된 trial은 restart 시 terminalize하고 SearchRun을 실패시키며, 재제출은 새 lineage를 만든다.

PBO 전에는 completeness certificate가 started와 terminal trial ID set/count의 동일성, profile-eligible과 matrix trial의 동일성, eligible trial의 exact fold set, duplicate/missing/non-finite score 부재, minimum eligible count와 normal stop을 증명해야 한다. profile이 명시한 complete-fold hard-gate rejection 외에는 score를 발명하지 않는다. 하나라도 위반하면 `AF-PBO-INCOMPLETE`로 holdout과 Publication을 차단한다. PBO는 selected candidate만이 아니라 이 certificate가 보장한 complete lineage ledger로 계산한다.

## 고려한 대안

| 대안 | 채택하지 않은 이유 |
| --- | --- |
| random/grid search와 임의 generation cap | 같은 입력의 순서·종료·증거를 고정하지 못하고, cap이 결과를 바꾼다. |
| 매 slot의 live parent ranking | 동시 terminalization과 DB 읽기 시점이 다음 후보를 바꾼다. |
| 최종 후보 또는 성공 trial만 PBO에 보관 | multiple-testing 집합과 fold completeness를 증명할 수 없다. |
| 유한 universe와 immutable snapshot, write-ahead ledger | **채택.** 모든 후보 선택과 PBO 입력을 재현·감사할 수 있다. |

## Consequences

- 긍정: 같은 frozen resources와 seed는 같은 identity, event order, ranking, stop reason을 만든다.
- 긍정: parent 선택과 PBO input이 snapshot 및 completeness certificate로 감사된다.
- 긍정: 실패·거절 trial도 lineage 증거로 남아 선택 편향을 숨기지 않는다.
- 부정: universe와 semantic resource를 사전에 versioning하고 hash해야 한다.
- 부정: live adaptive parent selection, 무제한 탐색, generation cap 기반 조기 종료는 제공하지 않는다.

## 강제 방법

canonical vector와 semantic-mutation test는 direct/transitive field 변경이 digest를 바꾸고 비의미적 display 변경은 바꾸지 않음을 검증한다. search test는 parent-pool immutability, first-winner cutoff, no-parent fallback, run-scoped proposal/visited set, plateau/exhaustion precedence를 검증한다. ledger/PBO test는 contiguous write-ahead starts, one terminal per start, exact trial/fold set equality와 `AF-PBO-INCOMPLETE` 차단을 검증한다.

## 재검토 조건

identity field, finite traversal, ranking, stop rule, Parent-pool snapshot 또는 PBO eligibility를 변경하려면 canonical vectors와 semantic mutation suite를 갱신하고, 기존 lineage와 새 lineage의 비교 가능성·migration 방식을 증명하는 새 ADR이 필요하다. 무제한 탐색이나 `max_generations`를 정상 종료 규칙으로 추가하는 제안은 이 결정의 범위 밖이다.
