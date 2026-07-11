# Alpha Foundry API 계약

상태: Approved  
Base path: `/api/v1`  
Media type: `application/json; charset=utf-8`

이 문서는 외부 HTTP·CLI·Event 계약의 권위 문서다. 내부 클래스나 DB 컬럼은 정의하지 않는다.

## 1. 공통 규칙

- JSON field는 `snake_case`를 사용한다.
- ID는 UUID 문자열이다.
- timestamp는 UTC RFC 3339 형식이다: `2026-07-11T03:04:05.123456Z`.
- 기간은 `[start, end)`로 해석한다.
- decimal은 JSON number가 아니라 문자열로 전달한다.
- 알 수 없는 request field는 `400 AF-SCHEMA-001`로 거절한다.
- mutation request는 `Idempotency-Key` header를 지원한다.
- 모든 response는 `X-Correlation-ID`를 포함한다.

## 2. 공통 Envelope

### 2.1 성공

```json
{
  "data": {},
  "meta": {
    "correlation_id": "3f2927b9-f9dd-43fe-b3b9-d823f1704228",
    "api_version": "1.0"
  }
}
```

### 2.2 오류

```json
{
  "error": {
    "code": "AF-SCHEMA-001",
    "message": "request schema validation failed",
    "details": [
      {"path": "question.payload.signal", "reason": "field is required"}
    ],
    "retryable": false
  },
  "meta": {
    "correlation_id": "3f2927b9-f9dd-43fe-b3b9-d823f1704228",
    "api_version": "1.0"
  }
}
```

`message`는 운영 판단에 사용하지 않는다. Client는 `code`만 분기 기준으로 사용한다.

## 3. 공통 타입

### 3.1 Enum

```text
MandateMode = QUESTION_SPECIFIED | DOMAIN_DIRECTED | OPEN_DISCOVERY
Domain = FACTOR | STAT_ARB | MARKET_MAKING | STRUCTURAL_FLOW |
         CROSS_VENUE | DERIVATIVES | EVENT_FUNDAMENTAL | TIME_SERIES
JobStatus = QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
Decision = PASS | FAIL
RegistryState = CANDIDATE | VALIDATED | APPROVED | REJECTED | RETIRED
```

### 3.2 Provenance

모든 생성 resource는 다음 필드를 응답에 포함한다.

```json
{
  "created_at": "2026-07-11T03:04:05.123456Z",
  "created_by": "local:user",
  "schema_version": "1.0.0",
  "code_version": "git:abc1234",
  "parent_id": null,
  "content_hash": "sha256:..."
}
```

## 4. Mandate

### 4.1 CreateMandateRequest

```json
{
  "mode": "DOMAIN_DIRECTED",
  "objective": "한국 주식 fundamental factor 전략을 연구하라",
  "domain_hint": "FACTOR",
  "universe": {
    "dataset_id": "krx-pit-panel",
    "filters": {"market": ["KOSPI", "KOSDAQ"]}
  },
  "period": {
    "start": "2015-01-01T00:00:00Z",
    "end": "2025-01-01T00:00:00Z"
  },
  "constraints": {
    "forbidden_features": ["future_financials"],
    "max_turnover": "3.0"
  },
  "budget": {
    "question_limit": 3,
    "experiment_limit": 20,
    "llm_token_limit": 20000,
    "wall_time_seconds": 900
  },
  "question": null
}
```

`QUESTION_SPECIFIED`는 `question`이 필수다. `DOMAIN_DIRECTED`는 `domain_hint`가 필수다. `OPEN_DISCOVERY`는 MVP에서 resource 생성은 가능하지만 실행 시 `AF-RELEASE-001`을 반환한다.

### 4.2 ResearchQuestion

```json
{
  "question_id": "uuid",
  "domain": "FACTOR",
  "statement": "자산 성장률이 이후 횡단면 수익률과 음의 관계를 갖는가?",
  "assumptions": ["공시 가용시점을 반영한다"],
  "falsification": ["OOS rank IC가 0 이하"],
  "data_requirements": ["krx-pit-panel@2025-01"],
  "evidence_ids": ["claim-uuid"],
  "budget": {"experiment_limit": 10},
  "payload": {
    "type": "FACTOR",
    "signal": "asset_growth",
    "direction": "LOW_IS_LONG",
    "rebalance_frequency": "MONTHLY",
    "portfolio": {"method": "QUANTILE", "long_quantile": "0.2", "short_quantile": "0.2"}
  }
}
```

### 4.3 Domain payload 필수 필드

`payload.type`은 `domain`과 같아야 한다. 다음 표의 필드는 각 payload의 최소 계약이다. 세부 값 제약은 생성 OpenAPI schema가 동일하게 강제한다.

| type | 필수 필드 |
| --- | --- |
| `FACTOR` | `signal`, `direction`, `rebalance_frequency`, `portfolio` |
| `STAT_ARB` | `legs`, `relation_model`, `entry_rule`, `exit_rule`, `hedge_update` |
| `MARKET_MAKING` | `instrument`, `state_features`, `quote_policy`, `inventory_policy` |
| `STRUCTURAL_FLOW` | `event_source`, `flow_model`, `entry_window`, `exit_window` |
| `CROSS_VENUE` | `venues`, `instruments`, `route_rule`, `synchronization` |
| `DERIVATIVES` | `legs`, `cashflow_model`, `margin_policy`, `roll_policy` |
| `EVENT_FUNDAMENTAL` | `event_type`, `availability_rule`, `surprise_model`, `holding_period` |
| `TIME_SERIES` | `target`, `features`, `forecast_horizon`, `walk_forward`, `position_rule` |

각 domain의 알 수 없는 field는 허용하지 않는다. domain schema 변경은 minor 또는 major API version 규칙을 따른다.

## 5. Experiment

### 5.1 Policy union

각 정책은 `type` 판별자를 가진다.

```json
{
  "cost_model": {"type": "STATIC", "maker_bps": "1.5", "taker_bps": "3.5"},
  "latency_model": {"type": "CONSTANT", "milliseconds": 25},
  "fill_model": {"type": "CONSERVATIVE"},
  "slippage_model": {"type": "FIXED_BPS", "bps": "2.0"},
  "impact_model": {"type": "NONE"},
  "borrow_model": {"type": "STATIC", "annual_bps": "75"},
  "funding_model": {"type": "SERIES", "dataset_id": "funding-v1"}
}
```

지원 판별자:

| 정책 | type |
| --- | --- |
| cost | `STATIC`, `TIMESTAMPED_SCHEDULE`, `PLUGIN` |
| latency | `CONSTANT`, `EMPIRICAL`, `EVENT_SERIES` |
| fill | `CONSERVATIVE`, `PROBABILISTIC`, `ORDER_REPLAY` |
| slippage | `FIXED_BPS`, `VOLUME_FUNCTION`, `ORDER_BOOK` |
| impact | `NONE`, `LINEAR`, `NONLINEAR`, `SIMULATOR` |
| borrow/funding | `STATIC`, `SERIES`, `PLUGIN` |

MVP Factor와 StatArb는 `STATIC` cost, `CONSTANT` latency, `CONSERVATIVE` fill, `FIXED_BPS` slippage, `NONE` impact를 실제 실행한다. 다른 type은 등록 capability가 없으면 `AF-CAPABILITY-001`로 거절한다.

### 5.2 ExperimentRequest

```json
{
  "strategy_id": "uuid",
  "dataset_versions": [
    {"dataset_id": "krx-pit-panel", "version": "2025-01", "content_hash": "sha256:..."}
  ],
  "period": {"start": "2015-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
  "split_policy": {"type": "WALK_FORWARD", "train": 504, "test": 126, "step": 126},
  "execution_policies": {},
  "seed": 42,
  "run_sealed_holdout": false
}
```

### 5.3 ExperimentResult

```json
{
  "experiment_id": "uuid",
  "fingerprint": "sha256:...",
  "status": "SUCCEEDED",
  "engine": {"key": "panel-portfolio", "version": "1.0.0"},
  "metrics": {
    "net_return": "0.1832",
    "sharpe": "1.14",
    "max_drawdown": "-0.087",
    "turnover": "2.41"
  },
  "artifact_refs": [
    {"role": "RESULT", "uri": "artifact://sha256/...", "content_hash": "sha256:..."}
  ],
  "provenance": {}
}
```

### 5.4 ValidationReport

```json
{
  "validation_id": "uuid",
  "experiment_id": "uuid",
  "rule_set_version": "1.0.0",
  "overall_decision": "PASS",
  "gates": [
    {"rule_id": "COMMON-NO-LEAKAGE", "decision": "PASS", "reason_code": null, "metrics": {}}
  ],
  "registry_state": "VALIDATED"
}
```

## 6. Job

```json
{
  "job_id": "uuid",
  "kind": "RUN_MANDATE",
  "resource_id": "mandate-uuid",
  "status": "RUNNING",
  "stage": "EXPERIMENT",
  "progress": {"completed": 3, "total": 10},
  "error": null,
  "created_at": "2026-07-11T03:04:05Z",
  "started_at": "2026-07-11T03:04:06Z",
  "finished_at": null
}
```

Client는 progress를 정확한 백분율로 해석하지 않는다. terminal status는 `SUCCEEDED`, `FAILED`, `CANCELLED`다.

## 7. HTTP Endpoint

### 7.1 System과 Capability

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `GET /health` | process와 storage health | `200` |
| `GET /capabilities` | 현재 dataset·engine·policy·Lab 조회 | `200` |
| `GET /schemas/questions/{domain}` | domain 질문 JSON Schema 조회 | `200` |

### 7.2 Knowledge

| Method/Path | 기능 | Request | 성공 |
| --- | --- | --- | --- |
| `POST /knowledge/sources` | source와 claim 등록 | SourceCreate | `201` Source |
| `GET /knowledge/claims` | domain·relation 기반 claim 조회 | query parameters | `200` list |

SourceCreate 최소 필드:

```json
{
  "title": "string",
  "locator": "doi-or-url-or-artifact-ref",
  "available_at": "2025-01-01T00:00:00Z",
  "claims": [
    {"statement": "string", "domain": "FACTOR", "relation_to": null, "relation_type": null}
  ]
}
```

### 7.3 Mandate와 연구 흐름

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `POST /mandates` | Mandate 생성 | `201` Mandate |
| `GET /mandates/{mandate_id}` | Mandate와 현재 상태 | `200` Mandate |
| `POST /mandates/{mandate_id}/run` | 비동기 연구 job 생성 | `202` Job |
| `GET /mandates/{mandate_id}/questions` | 질문과 감사 결과 | `200` list |
| `GET /mandates/{mandate_id}/program` | Program DAG 조회 | `200` Program |

### 7.4 Experiment와 결과

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `POST /experiments` | 명시적 experiment job 생성 | `202` Job |
| `GET /experiments/{experiment_id}` | config와 result 조회 | `200` Experiment |
| `POST /experiments/{experiment_id}/sealed-evaluation` | 승인된 sealed 실행 | `202` Job |
| `GET /validations/{validation_id}` | validation report 조회 | `200` ValidationReport |
| `GET /registry/strategies` | 상태별 전략 조회 | `200` list |
| `GET /registry/rejections` | reason별 탈락 조회 | `200` list |

### 7.5 Job

| Method/Path | 기능 | 성공 |
| --- | --- | --- |
| `GET /jobs/{job_id}` | job 상태 조회 | `200` Job |
| `POST /jobs/{job_id}/cancel` | 취소 요청 | `202` Job |

`run`, `experiments`, `sealed-evaluation`, `cancel`은 `Idempotency-Key`가 필수다. 같은 key와 같은 body는 최초 결과를 반환하고, 다른 body는 `409 AF-IDEMPOTENCY-001`이다.

## 8. Event Schema

### 8.1 Envelope

```json
{
  "event_id": "uuid",
  "event_type": "experiment.completed",
  "event_version": 1,
  "occurred_at": "2026-07-11T03:04:05.123456Z",
  "correlation_id": "uuid",
  "resource_type": "experiment",
  "resource_id": "uuid",
  "payload": {}
}
```

### 8.2 Catalog

| event_type | payload 필드 |
| --- | --- |
| `mandate.accepted` | `mandate_id`, `mode` |
| `question.approved` | `question_id`, `domain`, `audit_id` |
| `question.rejected` | `question_id`, `reason_codes` |
| `experiment.completed` | `experiment_id`, `fingerprint`, `result_hash` |
| `experiment.failed` | `experiment_id`, `error_code`, `retryable` |
| `validation.completed` | `validation_id`, `experiment_id`, `decision` |

Consumer는 모르는 payload field를 무시해야 한다. 기존 field 의미 변경과 삭제는 `event_version` 증가가 필요하다.

## 9. Error Code

| HTTP | Code | 의미 | 재시도 |
| --- | --- | --- | --- |
| 400 | `AF-SCHEMA-001` | request/schema 위반 | 아니오 |
| 400 | `AF-DOMAIN-001` | domain·payload 불일치 | 아니오 |
| 400 | `AF-POLICY-001` | 실행 정책 값 위반 | 아니오 |
| 404 | `AF-RESOURCE-001` | resource 없음 | 아니오 |
| 409 | `AF-STATE-001` | 허용되지 않은 상태 전이 | 아니오 |
| 409 | `AF-IDEMPOTENCY-001` | key 재사용 body 충돌 | 아니오 |
| 409 | `AF-HOLDOUT-001` | sealed holdout 이미 소비 | 아니오 |
| 422 | `AF-CAPABILITY-001` | 데이터·engine·policy 미지원 | 조건 변경 후 가능 |
| 422 | `AF-AUDIT-001` | 질문 hard gate 실패 | 새 revision 필요 |
| 422 | `AF-VALIDATION-001` | 실험 검증 실패 | 새 revision 필요 |
| 429 | `AF-LLM-LIMIT-001` | token 또는 provider rate 제한 | 예 |
| 500 | `AF-STORAGE-001` | 저장소 실패 | 예 |
| 502 | `AF-LLM-001` | provider 응답 실패 | 예 |
| 500 | `AF-ENGINE-001` | engine 실행 실패 | 오류에 따라 다름 |
| 501 | `AF-RELEASE-001` | 현재 release에서 실행 비활성 | 아니오 |

## 10. CLI Mapping

```text
alpha-foundry capability list
alpha-foundry knowledge add SOURCE.json
alpha-foundry mandate create MANDATE.json
alpha-foundry mandate run MANDATE_ID --wait
alpha-foundry mandate show MANDATE_ID
alpha-foundry experiment run EXPERIMENT.json --wait
alpha-foundry experiment show EXPERIMENT_ID
alpha-foundry job show JOB_ID
alpha-foundry job cancel JOB_ID
alpha-foundry registry strategies --state VALIDATED
alpha-foundry registry rejections --reason COMMON-NO-LEAKAGE
```

CLI exit code는 성공 `0`, schema·사용 오류 `2`, 검증 탈락 `3`, 일시 오류 `4`, 내부 오류 `5`다. `--json` 출력은 HTTP `data`와 동일한 schema를 사용한다.

## 11. Versioning

- URL major version은 breaking change에서만 증가한다.
- optional response field 추가와 새 enum이 아닌 새 resource 추가는 minor change다.
- request 필수 field 추가, field 의미 변경, enum 값 제거는 major change다.
- schema resource는 별도 semantic version을 갖고 resource provenance에 기록한다.
- 한 major version은 Production에서 다음 major 공개 후 최소 90일 유지한다.
- OpenAPI는 CI에서 route와 model로 생성하고 이 문서의 endpoint table과 diff 검사한다.
