# Alpha Foundry API 계약

상태: Approved  
Base path: `/api/v1`  
Media type: `application/json; charset=utf-8`

이 문서는 REST·CLI의 외부 contract다. REST route와 CLI command는 같은 application service command/query, canonical command input 및 serializer를 사용한다. 같은 정규화 입력은 같은 command fingerprint와 결과를 만든다. 영속 제약은 [05_Database.md](./05_Database.md), identity와 state machine은 [02_Architecture.md](./02_Architecture.md#3-권위-계약)을 따른다.

## 1. 공통 규칙

- field는 `snake_case`, ID는 UUID string, timestamp는 UTC RFC3339 microsecond, 기간은 `[start, end)`, Decimal은 JSON string이다.
- hash의 wire form은 `sha256:<64 lowercase hex>`다. AF-CANON input에서는 같은 digest의 raw 32 bytes를 사용한다.
- request model은 unknown field를 허용하지 않으며 `400 AF-SCHEMA-001`이다. NaN/Infinity와 implementation-defined identity field도 거절한다.
- mutation은 `Idempotency-Key`가 필수다. 같은 key/같은 canonical body는 최초 result, 다른 body는 `409 AF-IDEMPOTENCY-001`이다.
- response는 `X-Correlation-ID`와 아래 envelope을 사용한다. `message`는 machine decision에 사용하지 않고 client는 `code`로 분기한다.

```json
{
  "data": {},
  "meta": {"correlation_id": "uuid", "api_version": "1.0"}
}
```

```json
{
  "error": {
    "code": "AF-SCHEMA-001",
    "message": "request schema validation failed",
    "details": [{"path": "constraints", "reason": "field is required"}],
    "retryable": false
  },
  "meta": {"correlation_id": "uuid", "api_version": "1.0"}
}
```

## 2. 공통 타입과 provenance

```text
Domain = FACTOR | STAT_ARB | MARKET_MAKING | STRUCTURAL_FLOW |
         CROSS_VENUE | DERIVATIVES | EVENT_FUNDAMENTAL | TIME_SERIES
JobStatus = QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
GenerationRequestState = AVAILABLE | RUNNING | ACCEPTED | FAILED
GenerationAttemptState = STARTED | SUCCEEDED_VALID | FAILED | INVALID | INTERRUPTED
CandidateTrialTerminal = EVALUATED | REJECTED_PREFLIGHT | REJECTED_HARD_GATE |
                         ENGINE_FAILED | INTERRUPTED
SearchStopReason = PLATEAU | UNIVERSE_EXHAUSTED | FAILED_INVARIANT | INTERRUPTED
PublicationState = AVAILABLE | PREPARING | PUBLISHED | FAILED_RETRYABLE
Decision = PASS | FAIL
```

생성 resource는 가능한 경우 다음 provenance를 반환한다. response에서 owner token, lease secret, sealed selector/detail은 절대 반환하지 않는다.

```json
{
  "created_at": "2026-07-12T03:04:05.123456Z",
  "created_by": "local:user",
  "schema_version": "1.0.0",
  "code_version": "git:abc1234",
  "content_hash": "sha256:..."
}
```

## 3. Shared service contract

REST adapter는 request JSON을 command DTO로 정규화하고 application service를 한 번 호출한다. CLI는 같은 DTO를 file/stdin/flag에서 만들고 같은 service를 호출한다. adapter는 state transition, CAS, fallback, hash, ranking 또는 disclosure filtering을 재구현하지 않는다.

각 mutation response는 `command_fingerprint`, `job` 또는 final resource를 포함한다. command fingerprint는 operation name, validated canonical DTO, pinned immutable resource hashes 및 code/schema version을 포함한다. 기존 completed fingerprint는 재계산하거나 engine/LLM을 다시 호출하지 않는다.

## 4. Immutable knowledge and capability

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `GET /health` | process/storage health | `200` |
| `GET /capabilities/{capability_snapshot_id}` | immutable dataset, policy, schema, ordered provider/model chain 조회 | `200` |
| `GET /schemas/questions/{domain}` | domain question schema | `200` |
| `GET /schemas/strategies/{domain}` | domain typed StrategySpec schema | `200` |
| `POST /knowledge/packs` | immutable KnowledgePack/claim version 등록 | `201` |
| `GET /knowledge/packs/{pack_id}/versions/{version}` | pinned pack/claim hash 조회 | `200` |
| `GET /knowledge/claims` | deterministic filter/full-text claim 조회 | `200` |

Claim pin은 `{ "id": "claim-id", "version": "1.0.0", "hash": "sha256:..." }`다. request가 pin과 immutable resource hash를 참조하면 server는 exact ID/version/hash 일치를 확인한다. 최신 version으로 묵시적으로 대체하지 않는다.

## 5. Generation request, attempt, fallback and replay

### 5.1 `POST /generation/requests`

이 endpoint는 immutable CapabilitySnapshot의 ordered provider/model chain과 exact claim pin을 사용해 GenerationRequest를 acquire하거나 기존 resource를 반환한다. client는 provider/model fallback을 직접 선택하거나 retry ordinal을 지정하지 않는다.

```json
{
  "capability_snapshot_hash": "sha256:...",
  "knowledge_pack_hash": "sha256:...",
  "claim_pins": [{"id": "claim-1", "version": "1.0.0", "hash": "sha256:..."}],
  "domain": "FACTOR",
  "constraints": {"forbidden_operators": ["arbitrary_code"]},
  "sampling": {"temperature": "0"},
  "user_request": {"objective": "research request"},
  "request_schema_hash": "sha256:..."
}
```

성공 `202` body는 `generation_request_id`, `request_hash`, `state`, `job_id`(새/existing owner job), `accepted_artifact_ref`(ACCEPTED일 때만)을 포함한다. `RUNNING` duplicate는 existing owner job을 반환하고 provider call을 만들지 않는다. `ACCEPTED` duplicate/replay는 artifact를 반환하며 실제 LLM call은 0회다. `FAILED` duplicate도 terminal result를 반환한다.

CapabilitySnapshot이 가진 provider chain은 ordinal order의 `{ordinal, provider, model, config_hash}`다. server는 `STARTED` attempt를 provider call 전에 기록하고, terminal attempt·ordinal advance 또는 accepted artifact/CAS를 write-ahead contract에 따라 기록한다. `GET /generation/requests/{generation_request_id}/attempts`는 ordinal, provider, model, request/response hash, terminal state, error code 및 accepted artifact relation만 반환한다. lease expiry는 live owner transfer API가 아니며 owner job terminality만 interrupted release/transfer의 근거다.

### 5.2 generation replay

`POST /generation/requests/{generation_request_id}/replay`는 mutation이 아니라 stored accepted JSON artifact를 반환하는 idempotent replay command다. `ACCEPTED` 외 state는 `409 AF-STATE-001`이고, replay는 LLM adapter를 호출하지 않는다.

## 6. Finite SearchRun and CandidateTrial

### 6.1 `POST /search-runs`

SearchRun은 one primary domain의 pinned SearchSpec/Universe/ValidationProfile을 받아 시작한다. request는 [Architecture §3.1](./02_Architecture.md#31-af-canon-identity-registry)의 exhaustive SearchSpec fields를 모두 전달하거나 그 exact immutable resource ID+hash를 전달해야 한다. `max_generations`와 manual candidate injection은 request, response, DB에 없다.

```json
{
  "search_spec": {
    "domain": "FACTOR",
    "seed": 42,
    "initial_population_size": 8,
    "offspring_count": 4,
    "parent_pool_size": 4,
    "patience": 3,
    "operator_schedule": ["mutate_signal", "crossover_signal"],
    "operator_set_hash": "sha256:...",
    "universe_hash": "sha256:...",
    "profile_hash": "sha256:...",
    "dataset_hashes": ["sha256:..."],
    "policy_hashes": ["sha256:..."],
    "schema_hashes": ["sha256:..."],
    "code_hash": "sha256:..."
  }
}
```

성공 `202`는 `search_run_id`, `search_spec_hash`, `universe_hash`, `profile_hash`, `job`, `command_fingerprint`를 반환한다. 동일 resource hash의 terminal run을 새 lineage 없이 재실행하지 않는다. `GET /search-runs/{search_run_id}`는 pinned hashes, state, normal stop reason, visited/proposed count, plateau summary와 job link를 반환한다. normal `stop_reason`은 `PLATEAU` 또는 `UNIVERSE_EXHAUSTED`뿐이다.

### 6.2 search observability

`GET /search-runs/{search_run_id}/parent-pools`는 generation index, exact ordered candidate hashes, parent-pool digest, profile/search-spec hash를 반환한다. 이 resource는 immutable이며 slot 중 live ranking을 노출하거나 수행하지 않는다.

`GET /internal/search-runs/{search_run_id}/trials`는 trusted internal operator surface다. contiguous ledger position, candidate/operator relation, consumed alternative events, `STARTED`와 one terminal, fold completeness/score eligibility를 반환한다. public researcher route는 CandidateTrial, rejected candidate, intermediate score 또는 undisclosed validation result를 반환하지 않는다.

## 7. Validation, automatic holdout and disclosure

`GET /validations/{validation_id}`는 pre-validation decision, frozen profile hash, non-sealed gate result와 PBO certificate summary를 반환한다. certificate incomplete/invalid은 `AF-PBO-INCOMPLETE`이며 holdout/publication을 차단한다.

sealed holdout을 시작하는 REST/CLI endpoint, `run_sealed_holdout` request field, reviewer approval field는 없다. complete pre-validation pass 뒤 ValidationService가 자동으로 slot을 atomic consume하고 lineage를 close한 후에만 sealed data를 읽는다. `GET /holdout-slots/{lineage_id}`는 slot state와 consumed timestamp만 반환하며 selector, row, range, folds, trace, per-observation return 또는 reconstructive metric을 반환하지 않는다.

final ResearchReport response는 frozen HoldoutDisclosurePolicy가 허용한 named aggregate decision/metric/threshold와 strategy/evidence IDs, provider attempts, dataset/config/code/schema versions, lineage, validation decision, artifact hashes만 포함한다. disclosure data는 generation/search/ranking/PBO/failure-memory input이 아니며 그러한 field를 받아들이는 endpoint도 없다.

## 8. Publication, reports and rejection visibility

Publication은 eligible validation에 대해 자동으로 시작되며 client가 `PUBLISHED` state를 set할 수 없다. normal publication은 JSON/HTML report artifact metadata, pass RegistryEntry, `PUBLISHED` event/state와 owner job `RUNNING→SUCCEEDED`를 하나의 transaction으로 commit한다.

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `GET /registry/strategies` | complete `PUBLISHED` strategy aggregate만 조회 | `200` list |
| `GET /registry/strategies/{strategy_id}` | PUBLISHED strategy와 allowed report summary | `200` |
| `GET /reports/{publication_id}` | canonical JSON ResearchReport | `200` |
| `GET /reports/{publication_id}/html` | deterministic escaped HTML rendering | `200` |
| `GET /internal/rejections` | append-only rejection/failure lineage 조회 | `200` list |
| `GET /internal/rejections/{rejection_id}` | internal reason, lineage, terminal evidence 조회 | `200` |

`/internal/*`은 trusted local operator adapter만 노출하는 내부 관측 surface이며 public researcher result와 섞지 않는다. rejection은 public strategy/report list에 절대 나타나지 않으며 internal query도 sealed selector/detail을 반환하지 않는다. `PREPARING`, `FAILED_RETRYABLE`, `AVAILABLE` publication은 public result query에서 보이지 않는다.

## 9. Jobs and recovery

```json
{
  "job_id": "uuid",
  "kind": "GENERATE | RUN_SEARCH | PUBLISH",
  "resource_id": "uuid",
  "status": "RUNNING",
  "stage": "SEARCH",
  "progress": {"completed": 3, "total": 10},
  "error": null,
  "result_ref": null,
  "created_at": "2026-07-12T03:04:05.123456Z",
  "started_at": "2026-07-12T03:04:06.123456Z",
  "finished_at": null
}
```

`GET /jobs/{job_id}`와 `POST /jobs/{job_id}/cancel`을 제공한다. process startup은 persisted `RUNNING` 모든 job을 무조건 `FAILED`/`AF-JOB-INTERRUPTED`로 만든다. 자동 stage resume은 없고 사용자가 새 command를 제출한다. 이미 completed fingerprint, consumed holdout, accepted generation artifact와 PUBLISHED publication은 재계산·재개·재사용하지 않는다. PUBLISHED와 RUNNING이 동시에 남은 legacy row도 job은 `FAILED`로 표시한다.

## 10. Endpoint summary

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `POST /mandates` | immutable research mandate 생성 | `201` |
| `POST /mandates/{mandate_id}/run` | shared research command job 생성 | `202` |
| `GET /mandates/{mandate_id}` | mandate/status 조회 | `200` |
| `POST /experiments` | explicit non-sealed experiment job 생성 | `202` |
| `GET /experiments/{experiment_id}` | config/result 조회 | `200` |
| `GET /generation/requests/{id}` | request/replay state 조회 | `200` |
| `POST /generation/requests` | generation acquire/replay job | `202` |
| `POST /search-runs` | finite SearchRun job | `202` |
| `GET /search-runs/{id}` | run state/stop reason | `200` |
| `GET /validations/{id}` | validation/PBO summary | `200` |
| `GET /jobs/{id}` | job state | `200` |
| `POST /jobs/{id}/cancel` | cancellation request | `202` |

`POST /experiments/{id}/sealed-evaluation`, holdout approval/consume endpoint, `POST /publications`, state-filtered public registry endpoint는 존재하지 않는다. published-only filter는 server가 고정하며 client parameter가 아니다.

## 11. Error code and CLI mapping

| HTTP | Code | 의미 | 재시도 |
| --- | --- | --- | --- |
| 400 | `AF-SCHEMA-001` | request/schema/canonical input 위반 | 아니오 |
| 400 | `AF-DOMAIN-001` | domain/payload 또는 primary-domain 경계 위반 | 아니오 |
| 400 | `AF-POLICY-001` | execution policy 값 위반 | 아니오 |
| 404 | `AF-RESOURCE-001` | resource 없음 또는 public visibility 없음 | 아니오 |
| 409 | `AF-STATE-001` | state/owner/CAS 전이 불가 | 아니오 |
| 409 | `AF-IDEMPOTENCY-001` | idempotency body 충돌 | 아니오 |
| 409 | `AF-HOLDOUT-001` | holdout slot consumed/closed | 아니오 |
| 422 | `AF-CAPABILITY-001` | data/engine/policy 미지원 또는 누락 | 조건 변경 후 |
| 422 | `AF-PBO-INCOMPLETE` | exact-set/fold PBO certificate 불완전 | 새 lineage 필요 |
| 422 | `AF-VALIDATION-001` | validation/preflight/disclosure 실패 | 새 revision 필요 |
| 500 | `AF-JOB-INTERRUPTED` | restart가 persisted RUNNING job을 종료 | 새 command 필요 |
| 502 | `AF-LLM-001` | ordered chain의 terminal provider failure | 조건 변경 후 |
| 500 | `AF-STORAGE-001` | CAS/SQLite storage failure | 경우에 따라 |

CLI는 운영자용 REST surface를 그대로 복제하지 않고 두 user intent만 제공한다.

```text
alpha-foundry article-add [ARTICLE_ROOT]
alpha-foundry alpha-generate "<request>" --domain DOMAIN
```

`ARTICLE_ROOT` 기본값은 `article`이며 markdown은 반드시 `article/<DOMAIN>/` 아래에 있어야 한다. `alpha-generate`는 domain의 최신 article KnowledgePack/version과 wiki query를 내부에서 선택한다. `--json`은 compact JSON을 출력하며 exit code는 success `0`, schema/usage `2`, validation rejection `3`, temporary error `4`, internal error `5`다.

## 12. Versioning

URL major는 breaking change에서만 증가한다. optional response field와 새 non-breaking resource는 minor change다. request required field 추가, field 의미 변경, enum 제거는 major change다. resource schema는 semantic version을 가지며 provenance와 identity hash에 기록한다. OpenAPI는 route/model에서 생성하고 이 endpoint table과 diff 검사한다.
