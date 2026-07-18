# Alpha Foundry Coding Guidelines

상태: Approved  
대상: 사람과 모든 AI 개발 에이전트

## 1. 권위와 우선순위

규칙 충돌은 승인 ADR, [02_Architecture.md](./02_Architecture.md), [04_API.md](./04_API.md), [05_Database.md](./05_Database.md), 이 문서, library convention 순으로 해석한다. contract 변경은 구현 우회가 아니라 같은 변경에서 권위 문서와 test vector를 갱신하는 일이다.

다음은 절대 바꾸거나 약화하지 않는다: immutable resource/version/hash, attempt/trial/event history, completed fingerprint, parent-pool snapshot, holdout consumption/lineage closure, PUBLISHED authority. 수정은 additive migration과 새 resource/revision으로 한다.

## 2. Toolchain과 기본 코드 규칙

| 목적 | 규칙 |
| --- | --- |
| Runtime | Python 3.12만 사용한다. |
| Package | `uv.lock`을 pin하고 `uv lock --check`, `uv sync --locked --group dev`, 이후 `uv run --locked --no-sync ...`를 사용한다. |
| Schema | Pydantic v2; public input은 `extra="forbid"`이다. |
| Numeric | money/rate/fee는 `Decimal`; float와 NaN/Infinity persistence/API를 금지한다. |
| CLI | stdlib `argparse`; REST와 같은 application command/query DTO를 만든다. |
| Quality | Ruff, mypy strict, pytest, contract vector와 architecture check를 warning 무시 없이 사용한다. |

module/function/variable은 `snake_case`, class/protocol은 `PascalCase`, enum/constant는 `UPPER_SNAKE_CASE`다. `data`, `manager`, `helper`, `util`, `process` 같은 책임 없는 public 이름을 쓰지 않는다. public parameter/return type을 명시하고 `Any`, untyped dict, broad `# type: ignore`는 좁은 사유 없이는 금지한다.

## 3. Architecture and interface rules

- `domain`은 standard library와 pure value object만 의존한다. application service는 repository/provider 구현이 아닌 port를 받는다. composition은 `bootstrap.py`에서만 한다.
- REST route와 CLI command는 input을 validate/normalize한 뒤 같은 application service를 정확히 한 번 호출한다. route/CLI가 repository, CAS, engine, provider를 직접 호출하거나 state machine을 재구현하지 않는다.
- application service는 command fingerprint를 동일 canonical DTO, pinned resource hash, schema/code version에서 계산한다. 같은 input은 REST/CLI에서 같은 fingerprint/result여야 한다.
- Lab은 하나의 primary `Domain`의 typed schema/compiler/engine contract만 소유한다. Lab 간 internal import, generic optional-field mega-model, composition package/API/schema는 금지한다.
- Engine은 LLM, HTTP, DB, application service를 호출하지 않는다. explicit immutable config/data/policy만 읽고 결과/artifact DTO를 반환한다.
- reporting/disclosure module은 knowledge, generation, failure-memory, search, ranking, PBO 또는 lineage-mutating module을 import하지 않는다. 역방향 import도 금지한다.

## 4. Schema, canonical identity and immutable resources

외부 JSON은 typed discriminator union과 `extra="forbid"`로 검증한다. domain payload discriminator는 primary domain과 같아야 한다. timestamp는 timezone-aware UTC, 기간은 `start <= t < end`, 단위는 `latency_ms`, `AnnualBps`처럼 이름/type에 포함한다.

모든 AF-CANON digest는 [Architecture §3.1](./02_Architecture.md#31-af-canon-identity-registry)를 byte-for-byte 구현한 하나의 `DigestRegistry`를 통해서만 계산한다.

- SHA-256 preimage는 domain separator, NUL, u32 field count, named field encoding이다. field name은 UTF-8 byte sort, parameter name은 ASCII sort다.
- integer는 minimal base-10 ASCII, Decimal은 normalized non-exponent ASCII/no negative zero, string은 NFC UTF-8, timestamp는 UTC microsecond RFC3339, hash는 raw bytes다. list order는 semantic declared order이며 object는 recursively named-field sorted다.
- type tag, length prefix, unknown identity field rejection을 개별 caller가 구현하지 않는다. canonical JSON is not a substitute for AF-CANON binary identity encoding.
- Candidate, Universe, SearchSpec, Traversal, Parent alternative, Parameter alternative, ParentPool, GenerationRequest의 exhaustive field를 줄이거나 default/optional implementation field를 추가하지 않는다. `max_generations`는 금지한다.
- semantic resource hash는 full transitive dependencies를 포함한다: operator arity/commutativity/compiler version/parameter type-grid-order/validity/schema; profile score definition/direction/quantization/order/gate/tie-break/patience/folds/PBO/disclosure; universe member/order/version; dataset PIT/manifest; policy value; schema; code identity. semantic change는 resource and SearchSpec digest를 바꿔야 한다.
- comments, display labels와 non-semantic metadata는 digest에 넣지 않는다. hash tuple/string을 정렬할 때 raw bytes/명시된 canonical encoded value order를 사용하며 locale/default Python ordering을 쓰지 않는다.

Canonical encoder와 resource validator는 Windows/Linux golden encoded bytes/digest fixture를 공유한다. semantic mutation contract test는 direct SearchSpec field와 각 transitive semantic component 변이가 해당 resource hash와 SearchSpec hash를 바꾸는지 독립적으로 검증한다.

## 5. Durable ownership, CAS and artifacts

상태 변경은 entity method 또는 application command 한 곳에 모으고, mutable update는 `WHERE id=? AND row_version=?`에 expected state와 owner token/epoch/ordinal을 함께 넣는다. update count가 1이 아니면 stale/concurrent authority 오류다; 재시도해서 다른 owner를 덮어쓰지 않는다.

CAS write 순서는 same-filesystem temporary write → byte/hash verification → atomic rename → matching DB metadata/owner-reference transaction이다. rename 전 artifact를 authority로 참조하지 않는다. rename 후 authority CAS 전의 file은 orphan일 수 있으며 accepted/published artifact가 아니다.

외부 call, engine work 또는 publication staging 전에 write-ahead intent와 `STARTED` event를 짧은 transaction으로 commit한다. terminal event와 authority state를 별도 best-effort write로 나누지 않는다. event row는 append-only다; delete/update로 history를 고치지 않는다.

## 6. GenerationRequest and ordered fallback

GenerationService는 immutable CapabilitySnapshot의 ordered `{ordinal, provider, model, config_hash}` chain만 사용한다. base URL/model/provider를 environment default나 adapter configuration에서 묵시적으로 fallback하지 않으며, 실제 chain은 request identity/provenance에 pin한다.

1. unique request hash는 `AVAILABLE`, epoch 0, owner null을 만든다. owner-null/state/version CAS만 first `ACQUIRED`를 만든다.
2. live `RUNNING` duplicate는 existing job을 돌려주고 provider call을 하지 않는다. lease expiry는 warning일 뿐 transfer 권한이 아니다.
3. `TRANSFERRED`는 prior `RELEASED_INTERRUPTED`과 terminal owner job을 증명하는 matching CAS에서만 가능하다. token, epoch, version, ordinal을 모두 검사한다.
4. provider call 직전에 unique `(request_id, ordinal)` `STARTED` attempt를 insert한다. invalid/failure terminal과 ordinal advance는 하나의 guarded transaction이다.
5. valid output은 plain JSON → typed schema/allowlist validation → hash/atomic rename 순이다. 그 뒤 `SUCCEEDED_VALID`, request `RUNNING→ACCEPTED`, artifact link, owner clear, `COMPLETED`를 한 transaction에서 CAS한다.
6. crash로 uncertain call이 생기면 owner-job interruption이 attempt를 `INTERRUPTED`로 terminalize하고 ordinal을 skip한다. accepted artifact가 있으면 replay는 adapter call 0회다. `ACCEPTED/FAILED` request는 resubmit으로 변하지 않는다.

LLM은 typed question/hypothesis/strategy candidate와 allowed reason explanation만 만들 수 있다. SQL/Python/shell 실행, authority state/registry/validation/holdout 변경, policy/data 존재 판단, missing config 추정은 할 수 없다. provider SDK types, secret, raw prompt/response를 infrastructure boundary 밖으로 내보내지 않는다.

## 7. Finite deterministic SearchRun

SearchRun은 finite duplicate-free Universe, frozen SearchSpec/ValidationProfile, seed와 deterministic iterator만 사용한다. random source는 passed seed에서만 derive하고 iteration 전에 every collection ordering을 명시한다.

- generation 0은 `(TraversalDigest, candidate_hash)` 순으로 `min(P,N)`을 시작한다. generation/slot/parameter index는 zero-based다.
- generation `g>=1` slot 0 전, terminal-before-start `EVALUATED` + all hard gates + complete finite quantized score vector만 profile lexicographic direction/order, raw candidate hash ascending으로 rank한다. `K=min(parent_pool_size, eligible_count)` exact order/hash/profile/spec digest를 immutable parent-pool row로 insert한다.
- slot code는 live ranking query를 하지 않고 persisted pool만 읽는다. K=0/insufficient parent는 parent iterator가 empty여야 하며 direct fallback을 우회하지 않는다.
- iterator 순서는 schedule offset ascending, prescribed schedule modulo index, Parent digest/hash, Parameter digest/vector다. mutation parent는 unique, crossover pair는 ordered distinct이며 commutative operator는 canonical one pair다.
- consumed alternative는 `INVALID`, `OUTSIDE_UNIVERSE` 또는 `DUPLICATE` event를 남긴다. first schema-valid/in-universe/run-unseen candidate만 accepted proposal + proposed set + exactly one `STARTED` trial + visited set을 atomic commit하고 slot을 끝낸다. unconsumed alternative은 event가 없다.
- alternatives exhaust 뒤 direct fallback은 traversal universe one pass의 first unseen candidate를 선택하고 `DIRECT_FALLBACK`, accepted proposal, proposed/visited set 및 exactly one `STARTED` trial을 atomic commit한다. 없으면 `SLOT_EXHAUSTED`. proposed/visited는 run-wide이며 reset되지 않는다.
- slot당 0/1 trial, generation당 최대 offspring count다. `max_generations`/unbounded loop를 만들지 않는다. zero-trial generation은 plateau를 바꾸지 않고 visited=universe면 exhaustion, 아니면 invariant failure다.
- slot은 sequential이며 evaluation order는 contiguous ledger position이다.
- plateau counters와 stop precedence를 Architecture §3.3 그대로 구현한다. normal terminal is only `PLATEAU`/`UNIVERSE_EXHAUSTED`; cancellation/interruption is typed failure.

## 8. CandidateTrial, PBO and numeric integrity

preflight/engine 전에 contiguous ledger position, immutable candidate/operator relation, CandidateTrial `STARTED`, `TRIAL_STARTED` event를 one short transaction으로 write ahead 한다. Every start has exactly one of `EVALUATED`, `REJECTED_PREFLIGHT`, `REJECTED_HARD_GATE`, `ENGINE_FAILED`, `INTERRUPTED`. Startup terminalizes dangling starts and fails the run; resubmission creates a new lineage.

PBO code constructs an explicit certificate, not a filtered convenience list. It compares set and count equality `started=terminal`, `eligible=matrix`; requires every eligible fold set equal to frozen profile fold set; rejects duplicate/missing/non-finite values; requires normal stop and minimum eligible count. Only complete finite EVALUATED trials are initially eligible; profile-listed hard-gate reject needs identical complete folds. Never impute scores/folds/returns. Any breach is `AF-PBO-INCOMPLETE` and prevents holdout/publication.

Engine code must preflight every required dataset and explicit policy; it must return a typed reason code rather than estimate missing cost/latency/fill/slippage/impact/borrow/funding/accounting input. Check accounting, position, cash, return and cost invariants at each engine step. Use stable reductions and named numeric tolerances when ordering could affect output.

## 9. Automatic holdout and no-feedback disclosure

After pre-validation and a passing complete PBO certificate, ValidationService automatically executes holdout. Do not implement manual approval, a client `run_sealed_holdout` flag, a sealed endpoint or retry/reopen path.

The only permitted order is a `BEGIN IMMEDIATE` transaction that verifies frozen profile/certificate/open lineage/unused slot, consumes slot, closes lineage and commits **before** any sealed data read. Success, fail and crash do not undo consumption. A code path that touches sealed data before that committed transaction is a release blocker.

HoldoutDisclosurePolicy is immutable/profile-pinned and emits only named aggregate decision/metric/threshold fields in final reports. Never serialize/log/pass to research input a selector, row, revealing range, per-observation return, fold, detailed trace or reconstructive value. Do not import report/disclosure output into knowledge, generation, failure memory, search, ranking, PBO or mutation services.

## 10. Publication, visibility and jobs

Preflight-invalid publication work appends only an idempotent internal rejection with one of `VALIDATION_NOT_PASS`, `LINEAGE_NOT_CLOSED`, `DISCLOSURE_INVALID`, `INPUT_HASH_MISMATCH`; it does not create a Publication.

Publication state is exactly `AVAILABLE`, `PREPARING`, `PUBLISHED`, `FAILED_RETRYABLE`. Acquire by CAS from AVAILABLE/FAILED_RETRYABLE with fresh token/job/epoch/attempt and append `ACQUIRED`. Live `PREPARING` duplicate returns owner job; expiry alone cannot steal it. Retryable failure kind is only `ARTIFACT_IO`, `REPORT_RENDER`, `STORAGE_COMMIT`, `OWNER_INTERRUPTED` and must clear ownership.

Normal publish uses one transaction with matching PREPARING/token/epoch/version predicate. It inserts JSON/HTML report metadata and links, exactly one pass RegistryEntry, `PUBLISHED` state/event and exact owner job `RUNNING→SUCCEEDED` with immutable result reference. All commit or all rollback. Do not separately “finish the job” after publication.

Public query code joins the fixed PUBLISHED aggregate only; it cannot accept a caller-provided status filter. Rejections and intermediate candidates are stored append-only in an internal registry/query surface, never public results. Internal views still redact sealed data.

At process startup, unconditionally mark every persisted `RUNNING` job `FAILED` with `AF-JOB-INTERRUPTED`; do not auto-resume a stage. Release a PREPARING publication owned by that failed job as `FAILED_RETRYABLE/OWNER_INTERRUPTED` with typed owner events. A PUBLISHED row stays PUBLISHED while any legacy RUNNING job becomes FAILED, never SUCCEEDED. Completed fingerprints, consumed slots, accepted artifacts and PUBLISHED rows are immutable.

## 11. Error handling, logging and review

domain/application has no HTTP status knowledge. adapters map typed errors to [API §11](./04_API.md#11-error-code-and-cli-mapping). `except Exception` is allowed only at job/application boundary to persist a typed failure then re-raise/map; it must not conceal success. Do not auto-correct user input.

Structured logs include correlation/job/request hash, attempt ordinal, search run/generation/slot/trial, publication ID, stage, duration and error code. Never log credentials, tokens, raw prompt/response, dataset row, sealed selector or sealed detail. Provider log may include provider/model, prompt hash, schema version, token count, duration and status.

Tests use fake providers and fixed time/UUID/seed. Add contract vectors for canonical bytes/digests and mutation coverage; focused fault/race tests for every write-ahead cut, live-expiry behavior, accepted replay, parent-pool freeze, first-winner cutoff, all PBO set/fold branches, pre-read holdout consumption, no-feedback imports, publication/job atomicity and blanket RUNNING→FAILED restart. Use independent small numerical oracles and cross-OS golden results where applicable.

PR review confirms the changed contract/ADR, resource hash coverage, migration impact, race/fault test, public/internal visibility, no sealed feedback, and removal of obsolete code/docs/examples. A reviewer and author cannot be the same AI; a human owns final merge.
