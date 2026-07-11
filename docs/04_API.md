# Alpha Foundry API 및 Event 계약

| 항목 | 값 |
|---|---|
| 문서 버전 | 1.0.0 |
| HTTP base path | `/v1` |
| 표현 | UTF-8 JSON, `application/json` |
| OpenAPI | 3.1 |
| 시간 | RFC 3339 UTC offset 필수 |

이 문서는 HTTP, CLI가 공유하는 application DTO, plugin interface payload, integration event의 권위 계약이다. 물리 저장 구조는 [Database](05_Database.md)가 소유한다.

## 1. 계약 원칙

1. 모든 object schema는 `additionalProperties: false`다.
2. 모든 union은 명시적 discriminator를 사용한다.
3. 입력에 server-owned 필드가 있거나 알 수 없는 enum·필드가 있으면 `422 AF-SCHEMA-001`이다.
4. ID는 lowercase canonical UUID 문자열이다.
5. timestamp는 `2026-07-11T05:30:00.123456Z`와 같은 offset-aware RFC 3339다. naive timestamp는 거부한다.
6. 금융 decimal은 scientific notation이 없는 문자열 `DecimalString`으로 전달한다. regex는 `^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`다.
7. JSON의 `NaN`, `Infinity`, `-Infinity`는 금지한다.
8. 배열 순서가 의미 없는 필드는 server가 정렬 후 canonicalize한다. 의미가 있는 leg·DAG·event 순서는 입력 순서를 보존한다.
9. `null`은 schema가 명시한 nullable 필드에서만 허용한다. 비적용 metric은 `null`이 아니라 `MetricValue.status=NOT_APPLICABLE`이다.
10. request body 최대 크기는 일반 JSON 10 MiB다. 시장 데이터와 artifact byte는 별도 upload session 또는 등록된 storage ref를 사용한다.

## 2. 공통 Header와 Envelope

### 2.1 Request header

| Header | 적용 | 규칙 |
|---|---|---|
| `Authorization: Bearer <JWT>` | health 외 전부 | OIDC access token |
| `X-Correlation-ID` | 선택 | UUID; 없으면 server 생성 |
| `Idempotency-Key` | POST mutation | 16~128 ASCII, 생성·실행·전이에는 필수 |
| `If-Match` | mutable resource command | quoted integer ETag, 예: `"4"` |
| `Accept` | 선택 | `application/json` 또는 report에서 `text/markdown` |

### 2.2 Success envelope

```json
{
  "data": {},
  "meta": {
    "request_id": "4ef3172e-50b3-4ca9-8136-706b5db9d622",
    "correlation_id": "c02cb6b8-6890-463f-afbb-b21b4f2403f5",
    "api_version": "1",
    "served_at": "2026-07-11T05:30:00.123456Z"
  }
}
```

List response의 `meta`에는 `next_cursor: string|null`과 `has_more: boolean`이 추가된다.

### 2.3 Error envelope

```json
{
  "error": {
    "code": "AF-CAPABILITY-001",
    "message": "Required capabilities are unavailable.",
    "details": {
      "missing": ["dataset.field:available_at", "engine:panel_portfolio>=1.0"]
    },
    "retryable": false
  },
  "meta": {
    "request_id": "4ef3172e-50b3-4ca9-8136-706b5db9d622",
    "correlation_id": "c02cb6b8-6890-463f-afbb-b21b4f2403f5",
    "api_version": "1",
    "served_at": "2026-07-11T05:30:00.123456Z"
  }
}
```

`message`는 안전한 사용자 메시지다. stack trace, SQL, token, provider raw response는 포함하지 않는다.

## 3. 인증과 권한

### 3.1 Token claim

필수 claim은 `iss`, `sub`, `aud=alpha-foundry`, `iat`, `exp`, `actor_type`, `roles`, `tenant_id`다.

`actor_type`:

```text
HUMAN | AI_AGENT | SERVICE
```

`roles`:

```text
RESEARCHER | RESEARCH_REVIEWER | RISK_APPROVER |
PLATFORM_ADMIN | AUDITOR | WORKER_SERVICE | EVENT_DISPATCHER
```

### 3.2 권한 표

| 동작 | 허용 role | actor 제약 |
|---|---|---|
| mandate/question/program 생성 | RESEARCHER | HUMAN, AI_AGENT |
| experiment run | RESEARCHER | HUMAN, AI_AGENT |
| G0~G8 validation | RESEARCHER, RESEARCH_REVIEWER | HUMAN, AI_AGENT, SERVICE |
| sealed evaluation 요청 | RESEARCH_REVIEWER | HUMAN only |
| strategy register | RESEARCH_REVIEWER | HUMAN only |
| PAPER/SHADOW 전이 | RESEARCH_REVIEWER, RISK_APPROVER | HUMAN only |
| HUMAN_APPROVED 전이 | RISK_APPROVER | HUMAN only |
| LIVE_RECORDED 전이 | RISK_APPROVER + PLATFORM_ADMIN 두 승인 | 서로 다른 HUMAN 두 명 |
| capability/config/data 등록 | PLATFORM_ADMIN | HUMAN only |
| job claim/heartbeat/complete | WORKER_SERVICE | SERVICE only |
| audit read | AUDITOR, PLATFORM_ADMIN | HUMAN |

## 4. 공통 타입

### 4.1 Enum

```text
Domain =
  FACTOR_PORTFOLIO | STAT_ARB | MARKET_STRUCTURE_MM |
  MARKET_STRUCTURE_FLOW | CROSS_VENUE | DERIVATIVES_RV |
  EVENT_FUNDAMENTAL | TIME_SERIES

AutonomyMode = QUESTION_SPECIFIED | DOMAIN_MANDATE | OPEN_DISCOVERY

HorizonUnit = TICK | SECOND | MINUTE | HOUR | TRADING_DAY | CALENDAR_DAY | MONTH

MetricStatus = VALUE | NOT_APPLICABLE | NOT_COMPUTED | INVALID

GateDecision = PASS | FAIL | WARN | NOT_APPLICABLE
```

### 4.2 Identifier와 참조

`RefString`은 `^[A-Za-z][A-Za-z0-9._:/-]{0,254}$`를 만족한다. UUID resource ID가 아닌 field, plugin, model, column, venue 참조에 사용한다.

```json
{
  "ref_id": "dataset:kr_equity_pit",
  "version": "2026.07.01",
  "content_hash": "blake3:96f..."
}
```

`VersionedRef`의 세 필드는 모두 필수다.

### 4.3 Horizon, Window, TimeRange

```json
{
  "unit": "TRADING_DAY",
  "value": 20
}
```

`Horizon.value`는 1~1,000,000 정수다.

```json
{
  "kind": "OBSERVATIONS",
  "value": 252,
  "unit": null
}
```

`WindowSpec.kind`는 `OBSERVATIONS|DURATION`이다. `OBSERVATIONS`이면 `unit=null`, `DURATION`이면 `unit`이 `SECOND|MINUTE|HOUR|TRADING_DAY|CALENDAR_DAY|MONTH` 중 하나다.

```json
{
  "start": "2020-01-01T00:00:00Z",
  "end_exclusive": "2025-01-01T00:00:00Z"
}
```

### 4.4 MetricValue

```json
{"status": "VALUE", "value": "1.284", "unit": "RATIO"}
```

```json
{"status": "NOT_APPLICABLE", "value": null, "unit": "RATIO"}
```

`status=VALUE`이면 `value`가 DecimalString이고, 나머지는 `value=null`이다. `unit`은 `CURRENCY|RETURN|RATIO|BPS|QUANTITY|SECONDS|COUNT|PERCENT`다.

### 4.5 PredicateSpec

```json
{
  "field_ref": "security.market_cap",
  "operator": "GREATER_THAN_OR_EQUAL",
  "value_type": "DECIMAL",
  "value": "100000000000"
}
```

`operator`는 `EQUAL|NOT_EQUAL|LESS_THAN|LESS_THAN_OR_EQUAL|GREATER_THAN|GREATER_THAN_OR_EQUAL|IN|NOT_IN|IS_NULL|IS_NOT_NULL`이다. `value_type`은 `STRING|INTEGER|DECIMAL|BOOLEAN|DATE|STRING_LIST|DECIMAL_LIST`이며 null 연산자의 `value`만 null이다.

## 5. 핵심 리소스 스키마

### 5.1 Provenance

| 필드 | 타입 | 규칙 |
|---|---|---|
| `created_by_actor_id` | UUID | 필수 |
| `actor_type` | ActorType | 필수 |
| `created_at` | timestamp | server-owned |
| `model_id` | RefString/null | LLM 산출물이면 필수 |
| `prompt_template_id` | RefString/null | LLM 산출물이면 필수 |
| `prompt_hash` | `blake3:*`/null | LLM 산출물이면 필수 |
| `parent_resource_id` | UUID/null | revision이면 필수 |
| `generation_method` | enum | `USER|LLM|DETERMINISTIC|IMPORT` |

### 5.2 ResearchMandate

#### Create request

| 필드 | 타입 | 규칙 |
|---|---|---|
| `schema_version` | string | 정확히 `1.0` |
| `natural_language_goal` | string | 10~20,000자 |
| `autonomy_mode` | AutonomyMode | 필수 |
| `preferred_domains` | Domain[] | 최대 8, 중복 금지 |
| `excluded_domains` | Domain[] | 최대 8, preferred와 교집합 금지 |
| `market_scope.asset_classes` | RefString[] | 1~16 |
| `market_scope.venues` | RefString[] | 0~64 |
| `market_scope.regions` | RefString[] | 0~32 |
| `market_scope.instruments` | RefString[] | 0~10,000 |
| `market_scope.holding_horizon.minimum` | Horizon | 필수 |
| `market_scope.holding_horizon.maximum` | Horizon | minimum 이상 |
| `resource_refs.datasets` | VersionedRef[] | 1~128 |
| `resource_refs.knowledge_namespaces` | VersionedRef[] | 0~64 |
| `resource_refs.config_profiles` | VersionedRef[] | 1~32 |
| `resource_refs.engines` | VersionedRef[] | 0~32 |
| `constraints.maximum_candidates` | integer | 1~1,000 |
| `constraints.maximum_trials` | integer | 1~100,000 |
| `constraints.maximum_revisions_per_lineage` | integer | 0~10 |
| `constraints.sealed_holdout_accesses` | integer | 정확히 0 또는 1 |
| `constraints.live_trading_allowed` | boolean | 반드시 false |
| `risk_requirements.maximum_drawdown` | DecimalString/null | 0~1 |
| `risk_requirements.minimum_capacity` | DecimalString/null | 0 이상, base currency |
| `risk_requirements.maximum_turnover` | DecimalString/null | 0 이상 |
| `risk_requirements.maximum_gross_exposure` | DecimalString/null | 0 이상 |
| `knowledge_policy.research_mode` | enum | `MODERN_KNOWLEDGE_RETROSPECTIVE|HISTORICAL_AS_OF` |
| `knowledge_policy.latest_knowledge_at` | timestamp/null | historical이면 필수 |

Response resource는 위 필드에 `mandate_id`, `status`, `row_version`, `provenance`, `supersedes_mandate_id`를 추가한다.

### 5.3 CapabilitySnapshot

| 필드 | 타입 | 규칙 |
|---|---|---|
| `snapshot_id` | UUID | 필수 |
| `snapshot_hash` | `blake3:*` | canonical content hash |
| `datasets` | DatasetCapability[] | field, time field, frequency, PIT, depth, quality |
| `engines` | EngineCapability[] | id, version, domains, profiles |
| `configs` | ConfigCapability[] | policy 종류·version |
| `compute.maximum_memory_bytes` | integer | 256 MiB 이상 |
| `compute.maximum_workers` | integer | 1~256 |
| `compute.maximum_trial_budget` | integer | 1 이상 |
| `created_at` | timestamp | server-owned |

Snapshot은 생성 후 변경할 수 없다.

### 5.4 ResearchQuestion 공통 외피

| 필드 | 타입 | 규칙 |
|---|---|---|
| `question_id` | UUID | server-owned |
| `mandate_id` | UUID | 필수 |
| `schema_version` | string | `1.0` |
| `domain` | Domain | payload discriminator |
| `domain_subtype` | RefString/null | lab schema가 허용한 값 |
| `title` | string | 5~300자 |
| `question` | string | 20~4,000자 |
| `evidence.supporting_claim_refs` | UUID[] | 1~100 |
| `evidence.contradicting_claim_refs` | UUID[] | 1~100 |
| `evidence.empirical_observation_refs` | UUID[] | 0~100 |
| `assumptions` | string[] | 1~30, 각 5~1,000자 |
| `falsifiers` | string[] | 1~20 |
| `negative_controls` | string[] | 1~20 |
| `data_requirements` | DataRequirement[] | 1~100 |
| `engine_requirements` | EngineRequirement[] | 1~20 |
| `decision_if_supported` | string | 10~2,000자 |
| `decision_if_rejected` | string | 10~2,000자 |
| `research_budget.maximum_trials` | integer | 1~10,000 |
| `research_budget.maximum_parameter_sets` | integer | 1~1,000 |
| `research_budget.maximum_revisions` | integer | 0~10 |
| `payload` | DomainQuestionPayload | `domain`과 일치 |
| `provenance` | Provenance | 필수 |

`DataRequirement`는 dataset ref, required field refs, time field ref, frequency, point-in-time boolean, minimum history를 가진다. `EngineRequirement`는 engine profile ref와 required capability string 배열을 가진다.

## 6. DomainQuestionPayload

아래 path는 모두 해당 payload 안에 있으며 명시되지 않은 path는 금지한다.

### 6.1 `FACTOR_PORTFOLIO`

| Path | 타입·제약 |
|---|---|
| `research_axis` | `SIGNAL_DISCOVERY|MARGINAL_INFORMATION|INTERACTION|DECAY|PORTFOLIO_MAPPING|CAPACITY` |
| `universe.asset_class` | RefString |
| `universe.markets` | RefString[1..16] |
| `universe.eligibility_rules` | PredicateSpec[1..100] |
| `universe.point_in_time_required` | literal `true` |
| `target.return_definition` | `CLOSE_TO_CLOSE|OPEN_TO_CLOSE|CLOSE_TO_OPEN|TOTAL_RETURN|EXCESS_RETURN` |
| `target.horizon` | Horizon |
| `target.benchmark_adjustment` | `NONE|MARKET|INDUSTRY|FACTOR_MODEL`, model이면 VersionedRef 필수 |
| `economic_mechanism.category` | `RISK_PREMIUM|MISPRICING|FRICTION|BEHAVIORAL|INSTITUTIONAL|ACCOUNTING|CUSTOM` |
| `economic_mechanism.causal_chain` | string[2..10] |
| `economic_mechanism.expected_sign` | `NEGATIVE|ZERO|POSITIVE|NON_MONOTONIC` |
| `feature_scope.families` | RefString[1..32] |
| `feature_scope.allowed_operators` | FactorOperator[1..32] |
| `feature_scope.forbidden_operators` | FactorOperator[0..32] |
| `feature_scope.maximum_expression_depth` | integer 1..12 |
| `availability.source_time_field` | RefString |
| `availability.minimum_lag` | Horizon |
| `neutralization.candidates` | `NONE|INDUSTRY|SIZE|BETA|COUNTRY|CUSTOM` 배열 |
| `neutralization.mandatory_exposures` | RefString[0..32] |
| `portfolio_mapping.candidates` | `QUANTILE|RANK_LINEAR|ZSCORE_LINEAR|OPTIMIZED` 배열 |
| `portfolio_mapping.rebalance_frequency` | Horizon |
| `portfolio_mapping.turnover_limit` | DecimalString, 0 이상 |
| `portfolio_mapping.leverage_limit` | DecimalString, 0 이상 |
| `baselines.known_factors` | VersionedRef[0..64] |
| `baselines.simple_models` | `MEAN|RANK|LINEAR|SECTOR_NEUTRAL` 배열 |
| `ablation_plan.components` | RefString[1..32] |
| `ablation_plan.interaction_terms` | 2-element RefString tuple[0..32] |
| `capacity_assumptions.participation_rate` | DecimalString, 0~1 |
| `capacity_assumptions.impact_model_ref` | VersionedRef |

`FactorOperator`는 `RANK|WINSORIZE|LAG|DELTA|ROLLING_MEAN|ROLLING_STD|ROBUST_ZSCORE|DECAY|RESIDUALIZE|NEUTRALIZE`다.

### 6.2 `STAT_ARB`

| Path | 타입·제약 |
|---|---|
| `relation_scope.type` | `PAIR|BASKET|MULTI_PAIR_PORTFOLIO` |
| `candidate_space.universe` | VersionedRef |
| `candidate_space.economic_constraints` | string[1..30] |
| `candidate_space.graph_constraints` | string[0..30] |
| `candidate_space.liquidity_constraints` | PredicateSpec[1..30] |
| `relationship_hypothesis.economic_link` | string 10..2,000자 |
| `relationship_hypothesis.statistical_relation` | `COINTEGRATION|COMMON_FACTOR|REPLICATION|MEAN_REVERTING_RESIDUAL|CUSTOM` |
| `relationship_hypothesis.expected_break_conditions` | string[1..20] |
| `selection_models.candidates` | `DISTANCE|COINTEGRATION|PCA_RESIDUAL|GRAPH_CLUSTER|REPLICATION|CUSTOM_PLUGIN` 배열 |
| `hedge_models.candidates` | `STATIC_OLS|ROLLING_OLS|TOTAL_LEAST_SQUARES|KALMAN|JOHANSEN|CUSTOM_PLUGIN` 배열 |
| `spread_models.candidates` | `ZSCORE|OU|ROBUST_QUANTILE|CHANGE_POINT|STATE_SPACE` 배열 |
| `formation_policy.window` | WindowSpec |
| `formation_policy.reselection_frequency` | Horizon |
| `formation_policy.information_boundary` | `FORMATION_END_EXCLUSIVE` literal |
| `trading_policy.entry_family` | `ZSCORE_THRESHOLD|QUANTILE_THRESHOLD|STATE_PROBABILITY|CUSTOM_PLUGIN` |
| `trading_policy.exit_family` | `MEAN_CROSS|INNER_THRESHOLD|TIME|STATE_PROBABILITY|CUSTOM_PLUGIN` |
| `trading_policy.stop_family` | `OUTER_THRESHOLD|BREAK_DETECTION|LOSS_LIMIT|TIME|NONE` |
| `trading_policy.maximum_holding_period` | Horizon |
| `execution.leg_count` | integer 2..64 |
| `execution.simultaneous_required` | boolean |
| `execution.execution_model_ref` | VersionedRef |
| `execution.maximum_leg_imbalance` | DecimalString, 0~1 |
| `portfolio_layer.pair_conflict_resolution` | `NET_ASSET_EXPOSURE|PRIORITY|OPTIMIZE|REJECT_CONFLICT` |
| `portfolio_layer.gross_exposure_limit` | DecimalString, 0 이상 |
| `portfolio_layer.concentration_limit` | DecimalString, 0~1 |

### 6.3 `MARKET_STRUCTURE_MM`

| Path | 타입·제약 |
|---|---|
| `market.venue` / `market.instrument` | RefString |
| `market.matching_rule` | `PRICE_TIME|PRO_RATA|SIZE_TIME|CUSTOM_PLUGIN` |
| `market.tick_size` / `market.lot_size` | 양의 DecimalString |
| `state_space.book_levels` | integer 1..100 |
| `state_space.order_flow_features` | RefString[1..64] |
| `state_space.volatility_features` | RefString[1..32] |
| `state_space.inventory_features` | RefString[1..32] |
| `state_space.venue_health_features` | RefString[1..32] |
| `action_space.quote_sides` | `BID|ASK`를 모두 포함 |
| `action_space.quote_distance_parameterization` | `TICKS|BPS|VOLATILITY_SCALED` |
| `action_space.size_parameterization` | `FIXED|INVENTORY_SCALED|DEPTH_SCALED` |
| `action_space.cancel_actions` | `TIMEOUT|STATE_CHANGE|QUEUE_LOSS|RISK` 중 1개 이상 |
| `action_space.flatten_actions` | `MARKET|IOC|PASSIVE_THEN_MARKET` 중 1개 이상 |
| `policy_family.candidates` | `RULE_BASED|STOCHASTIC_CONTROL|CONTEXTUAL_BANDIT|REINFORCEMENT_LEARNING|CUSTOM_PLUGIN` 배열 |
| `inventory.state_definition` | `BASE_QUANTITY|QUOTE_NOTIONAL|DELTA_EQUIVALENT` |
| `inventory.soft_limit` / `hard_limit` | 양의 DecimalString, soft < hard |
| `inventory.skew_candidates` | RefString[1..20] |
| `fill_model.model_ref` | VersionedRef |
| `fill_model.queue_assumption` | `BACK_OF_QUEUE|PRO_RATA|CALIBRATED|L3_EXACT` |
| `fill_model.partial_fill_enabled` | boolean |
| `latency_model.market_data_ref/order_entry_ref/cancel_ref` | VersionedRef |
| `objective.*_weight` | 0 이상 DecimalString; 네 weight 합 > 0 |
| `evaluation_horizons.markout_windows` | Horizon[1..20] |
| `evaluation_horizons.maximum_inventory_duration` | Horizon |

### 6.4 `MARKET_STRUCTURE_FLOW`

| Path | 타입·제약 |
|---|---|
| `actor.actor_type` | RefString |
| `actor.economic_role` | string 5..1,000자 |
| `mechanical_rule.source_ref` | VersionedRef |
| `mechanical_rule.trigger` | string |
| `mechanical_rule.calculation_rule` | RefString DSL id |
| `mechanical_rule.execution_requirement` | string |
| `flow_model.direction` | `BUY|SELL|SIGNED|CONDITIONAL` |
| `flow_model.magnitude_equation` | RefString typed equation id |
| `flow_model.observable_inputs` | RefString[1..64] |
| `flow_model.hidden_inputs` | RefString[0..64] |
| `flow_model.uncertainty_model` | VersionedRef |
| `affected_market.primary_instruments` | RefString[1..10,000] |
| `affected_market.hedge_instruments/substitute_instruments` | RefString[0..10,000] |
| `timing.information_available_at` | RefString timestamp field |
| `timing.expected_order_window` | TimeWindowRelative |
| `timing.auction_or_continuous` | `AUCTION|CONTINUOUS|BOTH` |
| `timing.anticipation_window/impact_window/reversal_window` | RelativeWindow |
| `market_response.expected_pre_move/impact/reversal` | `NEGATIVE|ZERO|POSITIVE|NON_MONOTONIC` |
| `crowding.observable_proxies` | RefString[1..32] |
| `crowding.decay_hypothesis` | string |
| `counterfactuals.placebo_events` | RefString[1..32] |
| `counterfactuals.unaffected_instruments` | RefString[1..10,000] |
| `counterfactuals.sign_reversal_group` | RefString[1..32] |
| `execution.engine_profile` | VersionedRef |
| `execution.capacity_constraints` | PredicateSpec[1..32] |

`RelativeWindow`은 `{start: Horizon, end: Horizon, direction: BEFORE|AFTER}`이고 start ≤ end다. `TimeWindowRelative`은 시작·끝 offset과 market session ref를 가진다.

### 6.5 `CROSS_VENUE`

| Path | 타입·제약 |
|---|---|
| `venue_graph.venues/assets/instruments` | RefString 배열, 각각 2개 이상 |
| `venue_graph.edges` | `{from_node,to_node,edge_type,instrument_ref}`[1..100,000] |
| `instrument_equivalence.canonical_underlying` | RefString |
| `instrument_equivalence.contract_mapping/quote_currency_mapping/multiplier_mapping/collateral_mapping` | VersionedRef 각각 필수 |
| `opportunity_class.type` | `DIRECT|TRIANGULAR|CYCLIC|CROSS_VENUE_RELATIVE_VALUE|CEX_DEX` |
| `route_candidates.paths` | ordered node id arrays[1..10,000] |
| `route_candidates.maximum_legs` | integer 2..16 |
| `route_candidates.execution_order` | `SIMULTANEOUS|SEQUENTIAL|ATOMIC_PLUGIN` |
| `route_candidates.atomicity` | `NONE|BEST_EFFORT|GUARANTEED` |
| `inventory.prepositioned_assets` | venue-asset-DecimalString records |
| `inventory.capital_per_venue` | venue-currency-DecimalString records |
| `inventory.transfer_allowed` | boolean |
| `inventory.rebalance_policy` | VersionedRef |
| `cost_model_ref/latency_model_ref/fill_model_ref/slippage_model_ref` | VersionedRef |
| `settlement.settlement_model_ref/transfer_delay_ref/counterparty_risk_ref` | VersionedRef |
| `failure_policy.partial_route/failed_leg/stale_quote/venue_outage` | `ABORT|HEDGE|ROLLBACK|HOLD_AND_ALERT` |
| `risk_constraints.maximum_unhedged_notional` | 양의 DecimalString |
| `risk_constraints.maximum_route_duration` | Horizon |
| `risk_constraints.maximum_venue_exposure` | 양의 DecimalString |

### 6.6 `DERIVATIVES_RV`

| Path | 타입·제약 |
|---|---|
| `relation_type.type` | `SPOT_FUTURE|SPOT_PERPETUAL|PERPETUAL_PERPETUAL|CALENDAR_SPREAD|VOLATILITY_SURFACE|DELTA_HEDGED_OPTION|CUSTOM` |
| `legs.instruments` | ordered InstrumentLeg[2..64] |
| `legs.side_constraints` | `LONG|SHORT|EITHER` 배열, leg 수와 같음 |
| `legs.quantity_mapping` | VersionedRef |
| `pricing_relation.theoretical_model` | VersionedRef |
| `pricing_relation.fair_value_components` | RefString[1..32] |
| `pricing_relation.convergence_event` | RefString |
| `cashflows.funding_model_ref/borrow_model_ref/dividend_model_ref/roll_model_ref/fee_model_ref` | VersionedRef 또는 명시적 `model:none:1.0` |
| `hedging.hedge_target` | `DELTA|BETA|VEGA|GAMMA|MULTI_GREEK|NONE` |
| `hedging.hedge_frequency` | Horizon |
| `hedging.greek_constraints` | GreekBound[0..16] |
| `hedging.rebalance_model` | VersionedRef |
| `margin.initial_margin_ref/maintenance_margin_ref/liquidation_model_ref/collateral_haircut_ref` | VersionedRef |
| `exercise_settlement.settlement_type` | `CASH|PHYSICAL|NONE` |
| `exercise_settlement.exercise_policy` | VersionedRef |
| `exercise_settlement.expiry_handling` | `CLOSE|ROLL|EXERCISE|SETTLE` |
| `risk.gap_scenarios/volatility_scenarios/correlation_scenarios/funding_scenarios` | VersionedRef[1..32] |

### 6.7 `EVENT_FUNDAMENTAL`

| Path | 타입·제약 |
|---|---|
| `event_source.source_type` | `FILING|EARNINGS|CORPORATE_ACTION|ANALYST|NEWS|CUSTOM` |
| `event_source.source_refs` | VersionedRef[1..32] |
| `event_source.timestamp_fields` | `{declared_at, filed_at, available_at}` RefString, available 필수 |
| `event_taxonomy.category/subtype` | RefString |
| `event_taxonomy.extraction_schema` | VersionedRef |
| `information_definition.raw_value` | RefString |
| `information_definition.expected_value` | RefString 또는 `NONE` |
| `information_definition.surprise_definition` | VersionedRef |
| `information_definition.direction_mapping` | VersionedRef |
| `revision_policy.initial_filing/restatement/latest_available` | `INCLUDE|EXCLUDE|SEPARATE_COHORT` |
| `revision_policy.lineage_required` | literal true |
| `universe.eligibility` | PredicateSpec[1..100] |
| `universe.point_in_time_classification` | literal true |
| `event_windows.pre_event/immediate/post_event/reversal` | RelativeWindow |
| `benchmark.market_model/factor_model/matched_control` | VersionedRef 또는 `model:none:1.0` |
| `heterogeneity.grouping_variables` | RefString[0..32] |
| `heterogeneity.interaction_candidates` | RefString pair[0..32] |
| `portfolio_mapping.entry_time` | `NEXT_TRADEABLE_EVENT|NEXT_OPEN|SAME_SESSION_AFTER_DELAY` |
| `portfolio_mapping.holding_period` | Horizon |
| `portfolio_mapping.overlap_policy` | `ALLOW|NET|SKIP|SEPARATE_COHORT` |

### 6.8 `TIME_SERIES`

| Path | 타입·제약 |
|---|---|
| `instrument_scope.instruments` | RefString[1..10,000] |
| `instrument_scope.asset_class` | RefString |
| `target.target_type` | `RETURN|DIRECTION|VOLATILITY|QUANTILE|TAIL_EVENT` |
| `target.horizon` | Horizon |
| `target.overlap_policy` | `PURGE|NON_OVERLAPPING|ALLOW_WITH_CORRECTION` |
| `predictor_scope.feature_families` | RefString[1..64] |
| `predictor_scope.availability_lag` | Horizon |
| `predictor_scope.exogenous_data` | VersionedRef[0..32] |
| `model_families.candidates` | `RULE_BASED|LINEAR|TREE|STATE_SPACE|HMM|NEURAL|BAYESIAN` 배열 |
| `regime.definition_candidates` | VersionedRef[1..32] |
| `regime.transition_model` | VersionedRef |
| `regime.minimum_regime_sample` | integer 30 이상 |
| `position_policy.mapping` | `SIGN|LINEAR|PROBABILITY|QUANTILE|OPTIMIZED` |
| `position_policy.volatility_target` | DecimalString, 0~1 |
| `position_policy.leverage_limit` | DecimalString, 0 이상 |
| `position_policy.stop_policy` | VersionedRef |
| `execution.rebalance_frequency` | Horizon |
| `execution.cost_model_ref/slippage_model_ref` | VersionedRef |

## 7. Hypothesis, Strategy, Program Schema

### 7.1 HypothesisSpec

| 필드 | 타입 | 규칙 |
|---|---|---|
| `hypothesis_id` | UUID | server-owned |
| `question_id` | UUID | 필수 |
| `domain` | Domain | question과 동일 |
| `schema_version` | string | `1.0` |
| `statement` | string | 20~4,000자 |
| `mechanism` | string | 20~4,000자 |
| `expected_direction` | `NEGATIVE|ZERO|POSITIVE|NON_MONOTONIC|STATE_DEPENDENT` | 필수 |
| `testable_implications` | string[1..20] | 필수 |
| `falsification_rules` | DecisionRule[1..20] | 수치·통계 rule |
| `alternative_explanations` | string[1..20] | 필수 |
| `negative_controls` | string[1..20] | 필수 |
| `payload_schema_id` | RefString | `af://hypothesis/{domain}/1.0` |
| `payload` | DomainHypothesisPayload | schema registry 검증 |
| `provenance` | Provenance | 필수 |

`DecisionRule`은 `metric_ref`, `operator`, `threshold` DecimalString, `minimum_sample`, `severity=FATAL|WARNING`을 가진다.

Built-in `DomainHypothesisPayload` 필수 필드:

| Domain | 필수 payload field |
|---|---|
| FACTOR_PORTFOLIO | `feature_family`, `target_horizon`, `expected_ic_sign`, `mandatory_neutralizations`, `failure_regimes` |
| STAT_ARB | `relation_type`, `convergence_condition`, `expected_half_life_range`, `break_conditions`, `leg_risk_hypothesis` |
| MARKET_STRUCTURE_MM | `fill_condition`, `post_fill_markout_sign`, `inventory_effect`, `latency_failure_point` |
| MARKET_STRUCTURE_FLOW | `actor`, `mechanical_trigger`, `expected_flow_sign`, `impact_pattern`, `reversal_pattern` |
| CROSS_VENUE | `equivalence_basis`, `net_edge_condition`, `minimum_persistence`, `route_failure_conditions` |
| DERIVATIVES_RV | `pricing_relation`, `cashflow_source`, `convergence_event`, `hedge_stability`, `margin_failure_conditions` |
| EVENT_FUNDAMENTAL | `information_event`, `surprise_sign`, `reaction_pattern`, `revision_effect`, `placebo_expectation` |
| TIME_SERIES | `target_horizon`, `conditional_state`, `forecast_direction`, `calibration_expectation`, `tail_failure_regime` |

각 field는 문자열이 아니라 해당 question payload의 enum/Ref/Horizon/Range 타입을 재사용한다.

### 7.2 StrategySpec

| 필드 | 타입 | 규칙 |
|---|---|---|
| `strategy_id` | UUID | server-owned |
| `hypothesis_id` | UUID | 필수 |
| `domain` | Domain | hypothesis와 동일 |
| `schema_version` | string | `1.0` |
| `name` | string | 3~200자 |
| `dataset_version_refs` | UUID[1..128] | immutable versions |
| `config_profile_refs` | UUID[1..32] | immutable versions |
| `engine_profile` | VersionedRef | domain-compatible |
| `signal_or_state_schema_id` | RefString | built-in domain schema |
| `risk_policy_ref` | VersionedRef | 필수 |
| `execution_policy_ref` | VersionedRef | 필수 |
| `composition` | CompositionSpec[] | 0..8 |
| `payload_schema_id` | RefString | `af://strategy/{domain}/1.0` |
| `payload` | DomainStrategyPayload | strict union |
| `frozen_at` | timestamp/null | freeze 이후 revision 금지 |
| `provenance` | Provenance | 필수 |

Built-in `DomainStrategyPayload`:

| Domain | 필수 구조 |
|---|---|
| FACTOR_PORTFOLIO | typed `signal_ast`, universe rule, lag, neutralization, score-to-weight, rebalance, turnover/leverage/capacity limits |
| STAT_ARB | ordered legs, formation selector, hedge model, spread model, entry/exit/stop, reselection, execution, portfolio conflict rule |
| MARKET_STRUCTURE_MM | market, state features, quote/cancel/flatten actions, policy parameters, inventory limits, fill/latency models, objective |
| MARKET_STRUCTURE_FLOW | actor rule, flow equation, affected basket, availability/execution windows, entry/exit, capacity, counterfactuals |
| CROSS_VENUE | canonical instruments, graph, ordered routes, inventory, cost/latency/fill/slippage, failure policy, unhedged limits |
| DERIVATIVES_RV | ordered contract legs, fair-value/cashflow models, hedge, margin/liquidation, expiry/exercise, scenario limits |
| EVENT_FUNDAMENTAL | event extraction/version policy, surprise, tradeable timestamp, cohorts, benchmark/control, entry/hold/overlap |
| TIME_SERIES | feature pipeline, target, split, model, calibration, regime, position mapping, stop, rebalance/cost |

전략 payload 안의 plugin parameter는 `plugin_id`, `plugin_version`, `parameter_schema_id`, strict `parameters`를 함께 가져야 한다.

### 7.3 CompositionSpec

```json
{
  "source_strategy_id": "9cb88c13-89df-40a5-9c0c-ff0c97e91ab9",
  "target_strategy_id": "8d9d9aaf-2e40-4228-bdb4-7f912fa56738",
  "interface": "UNIVERSE_FILTER",
  "mechanism": "Use validated value strategy eligibility only for pair formation.",
  "ablation_experiment_id": "5efb5e24-4b5a-4c6a-ac06-d134dcff1a20",
  "parameters_frozen_at": "2026-07-11T05:30:00Z"
}
```

`interface`는 `UNIVERSE_FILTER|REGIME_GATE|RISK_NEUTRALIZER|EXECUTION_ADAPTER|CAPITAL_ALLOCATOR|HEDGE_ADAPTER`다.

### 7.4 ResearchProgram

| 필드 | 타입 | 규칙 |
|---|---|---|
| `program_id` | UUID | 필수 |
| `mandate_id` | UUID | 필수 |
| `schema_version` | string | `1.0` |
| `status` | `DRAFT|READY|RUNNING|SUCCEEDED|PARTIAL|FAILED|REJECTED` | 필수 |
| `nodes` | ProgramNode[1..10,000] | unique node id |
| `edges` | ProgramEdge[0..50,000] | cycle 금지 |
| `budget_allocation` | node별 trial/token/compute | 합이 mandate 한도 이하 |
| `capability_snapshot_id` | UUID | 필수 |
| `program_hash` | `blake3:*` | canonical DAG hash |
| `provenance` | Provenance | 필수 |

`ProgramNode.kind`는 `QUESTION|DATA_PREP|HYPOTHESIS|STRATEGY|EXPERIMENT|VALIDATION|REPORT`다. `ProgramEdge.kind`는 `DEPENDS_ON|CONSUMES|GATES`다.

## 8. Experiment Config와 Result

### 8.1 Policy discriminated unions

| Policy | 판별자와 variant |
|---|---|
| Cost | `STATIC(maker_bps,taker_bps,fixed_cost,currency)`, `TIMESTAMPED(dataset_version_id,maker_field,taker_field,fixed_field)`, `PLUGIN(plugin ref, strict parameters)` |
| Latency | `CONSTANT(market_data_ms,order_entry_ms,cancel_ms)`, `EMPIRICAL_DISTRIBUTION(dataset_version_id,field mapping,seed)`, `EVENT_RECORDED(dataset_version_id,event fields)` |
| Fill | `RULE_BASED(rule plugin)`, `PROBABILISTIC(model ref,seed)`, `ORDER_REPLAY(dataset version,matching rule)` |
| Slippage | `FIXED_BPS(bps)`, `VOLUME_FUNCTION(model ref,participation cap)`, `ORDER_BOOK(book dataset,depth cap)` |
| Impact | `NONE`, `LINEAR(coefficient,volume field)`, `NONLINEAR(model ref)`, `SIMULATOR(plugin ref)` |
| Borrow/Funding | `STATIC(rate,accrual basis)`, `TIME_SERIES(dataset version,rate field)`, `PLUGIN(plugin ref)` |

모든 decimal과 millisecond는 0 이상이다. static cost의 maker/taker 값은 server가 변경하지 않는다. plugin variant의 parameter object는 `parameter_schema_id`로 strict validation한다.

### 8.2 ExperimentConfig

| 필드 | 타입 | 규칙 |
|---|---|---|
| `schema_version` | string | `1.0` |
| `period.train` | TimeRange | 필수 |
| `period.validation` | TimeRange | train 이후, 겹침 금지 |
| `period.robustness` | TimeRange[] | 0..32 |
| `period.sealed_partition_ref` | UUID/null | 승인 전 조회 불가 |
| `cost_model` | CostModel | 필수 |
| `latency_model` | LatencyModel | 필수 |
| `fill_model` | FillModel | 필수 |
| `slippage_model` | SlippageModel | 필수 |
| `impact_model` | ImpactModel | 필수 |
| `borrow_model` | CarryModel | 필수; 비적용은 `STATIC rate=0`이 아니라 `NotApplicablePolicy` |
| `funding_model` | CarryModel | 동일 |
| `risk_profile_ref` | VersionedRef | 필수 |
| `random_seed` | integer | 0..2^63-1 |
| `resource_budget.memory_bytes` | integer | 256 MiB 이상 |
| `resource_budget.cpu_seconds` | integer | 1 이상 |
| `resource_budget.wall_seconds` | integer | 1 이상 |
| `feedback_policy` | train/validation/sealed visibility | `FULL/COARSE/HIDDEN` 고정 규칙 |

`NotApplicablePolicy`는 `{type:"NOT_APPLICABLE", reason_code}`이며 해당 domain에서만 허용된다.

### 8.3 ExperimentResult

| 영역 | 필드 |
|---|---|
| Identity | `experiment_id`, `fingerprint`, `strategy_id`, `domain`, `engine_id/version`, `dataset_versions`, `config_hash`, `code_revision`, `random_seed` |
| Performance | `gross_pnl`, `net_pnl`, `annualized_return`, `volatility`, `sharpe`, `sortino`, `max_drawdown`, `var`, `expected_shortfall` MetricValue |
| Costs | `fees`, `slippage`, `market_impact`, `borrow`, `funding`, `other` MetricValue |
| Exposure | `gross`, `net`, `beta`, `concentration`, `by_asset`, `by_venue`, `by_domain` |
| Execution | `turnover`, `fill_rate`, `partial_fill_rate`, `rejected_orders`, `maximum_unhedged_exposure` |
| Attribution | domain component MetricValue와 `reconciliation_error` |
| Artifacts | `pnl_series`, `positions`, `orders`, `fills`, `diagnostics`, `model`, `report` ArtifactRef |
| Runtime | `started_at`, `finished_at`, `duration_ms`, `peak_memory_bytes`, `worker_release` |

`ArtifactRef`는 `artifact_id`, `content_hash`, `media_type`, `size_bytes`, `schema_id`, `retention_class`를 가진다. storage path는 API에 노출하지 않는다.

### 8.4 ValidationReport

| 필드 | 타입 |
|---|---|
| `validation_report_id` | UUID |
| `experiment_id` | UUID |
| `rule_set_version` | string |
| `overall_decision` | `PASS|FAIL` |
| `gate_results` | G0~G10 GateResult[11] |
| `coarse_generator_feedback` | FeedbackItem[] |
| `reviewer_findings` | Finding[] |
| `created_at` | timestamp |

`GateResult`는 `gate`, `decision`, `fatal`, `rule_results`, `artifact_refs`, `started_at`, `finished_at`을 가진다. `RuleResult`는 rule id/version, metric, threshold, decision, evidence ref를 가진다.

### 8.5 JobResource

| 필드 | 타입·규칙 |
|---|---|
| `job_id` | UUID |
| `job_type` | `COMPILE_MANDATE|ROUTE_DOMAIN|GENERATE_QUESTIONS|AUDIT_QUESTION|BUILD_PROGRAM|RUN_PROGRAM|COMPILE_HYPOTHESIS|COMPILE_STRATEGY|RUN_EXPERIMENT|VALIDATE_EXPERIMENT|RUN_SEALED|BUILD_REPORT|MAINTENANCE` |
| `status` | `QUEUED|CLAIMED|RUNNING|CANCELLING|SUCCEEDED|FAILED|CANCELLED` |
| `progress` | integer 0..100 |
| `attempt` / `maximum_attempts` | integer |
| `created_at`, `available_at`, `started_at`, `finished_at` | timestamp/null |
| `heartbeat_at`, `lease_expires_at` | timestamp/null |
| `result` | `{resource_type,resource_id}`/null |
| `error` | safe ErrorObject/null |
| `links.self`, `links.result` | relative URI/null |

202 response의 `data`는 JobResource이고 `Location` header는 `/v1/jobs/{job_id}`다.

## 9. HTTP Endpoint

### 9.1 System과 schema discovery

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `GET /health/live` | 없음 | 없음 | `200 {status:"live"}` |
| `GET /health/ready` | 내부 probe | 없음 | `200 ready` 또는 `503`와 dependency 상태 |
| `GET /v1/meta` | 인증 사용자 | 없음 | API, release, schema catalog version |
| `GET /v1/schemas/{schema_id}/{version}` | 인증 사용자 | path | `200` JSON Schema |

`/health/ready`는 secret, hostname, provider raw 오류를 반환하지 않는다.

### 9.2 Dataset, Config, Capability, Plugin

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/datasets` | PLATFORM_ADMIN | `DatasetCreateRequest` | `201 DatasetResource` |
| `POST /v1/datasets/{dataset_id}/versions` | PLATFORM_ADMIN | `DatasetVersionCreateRequest` | `201 DatasetVersionResource` |
| `GET /v1/datasets/{dataset_id}` | RESEARCHER+ | 없음 | `200 DatasetResource` |
| `GET /v1/dataset-versions/{version_id}` | RESEARCHER+ | 없음 | `200 DatasetVersionResource` |
| `POST /v1/config-profiles` | PLATFORM_ADMIN | `ConfigProfileCreateRequest` | `201 ConfigProfileResource` |
| `GET /v1/config-profiles/{config_id}` | RESEARCHER+ | 없음 | `200 ConfigProfileResource` |
| `POST /v1/plugins` | PLATFORM_ADMIN | `PluginRegistrationRequest` | `201 PluginResource` |
| `GET /v1/plugins` | 인증 사용자 | filters/cursor | `200 PluginResource[]` |
| `POST /v1/capability-snapshots` | PLATFORM_ADMIN, RESEARCHER | version refs와 compute 한도 | `201 CapabilitySnapshot` |
| `GET /v1/capability-snapshots/{snapshot_id}` | 인증 사용자 | 없음 | `200 CapabilitySnapshot` |

이 표의 `RESEARCHER+`는 `RESEARCHER`, `RESEARCH_REVIEWER`, `RISK_APPROVER`, `PLATFORM_ADMIN`, `AUDITOR` 중 하나를 뜻한다. `/v1/meta`의 `enabled_autonomy_modes`가 현재 release에서 실행 가능한 mode를 열거한다. MVP의 `OPEN_DISCOVERY` mandate는 schema validation 후 실행 command에서 `AF-RELEASE-001`을 반환한다.

`DatasetVersionCreateRequest` 필수 필드:

- `dataset_id`, `version_label`, `content_hash`, `schema_id`
- `storage_ref` 또는 이미 완료된 upload session id 중 하나
- `time_semantics`의 `event_time_field`, `available_at_field`, `timezone`, `revision_policy`
- `coverage`의 start/end, instrument count, row count
- `quality`의 duplicate count, null rate by required field, sequence-gap summary, survivorship flag
- `license`의 owner, permitted uses, `retention_until`, redistribution boolean

Dataset version은 생성 후 byte·schema·time semantics를 바꿀 수 없다. 오류 정정은 새 version이다.

`ConfigProfileCreateRequest`는 `name`, `schema_version`, 7개 policy union, risk profile, description을 가진다. response에는 `config_hash`와 immutable `config_profile_id`가 추가된다.

`PluginRegistrationRequest`:

```json
{
  "plugin_id": "engine.panel_portfolio",
  "plugin_type": "ENGINE",
  "version": "1.0.0",
  "supported_domains": ["FACTOR_PORTFOLIO"],
  "input_schema_id": "af://strategy/factor_portfolio/1.0",
  "output_schema_id": "af://experiment/result/1.0",
  "parameter_schema_id": "af://engine/panel_portfolio/parameters/1.0",
  "code_revision": "git:7ab4c1e",
  "artifact_hash": "sha256:...",
  "capabilities": ["PIT_PANEL", "NEUTRALIZATION", "CAPACITY"]
}
```

`plugin_type`은 `LAB|ENGINE|VALIDATOR|COST_MODEL|LATENCY_MODEL|FILL_MODEL|SLIPPAGE_MODEL|IMPACT_MODEL|DATA_ADAPTER`다.

### 9.3 Knowledge와 Evidence

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/sources` | RESEARCHER, PLATFORM_ADMIN | `SourceCreateRequest` | `201 SourceResource` |
| `GET /v1/sources/{source_id}` | 인증 사용자 | 없음 | `200 SourceResource` |
| `POST /v1/claims` | RESEARCHER, AI_AGENT role 포함 token | `ClaimCreateRequest` | `201 ClaimResource` |
| `POST /v1/claims/{claim_id}/review` | RESEARCH_REVIEWER | `ClaimReviewRequest` | `200 ClaimResource` |
| `POST /v1/claim-relations` | RESEARCHER | source/target/type | `201 ClaimRelationResource` |
| `POST /v1/evidence-bundles` | RESEARCHER | claim·observation·failure refs | `201 EvidenceBundleResource` |
| `GET /v1/evidence-bundles/{bundle_id}` | 인증 사용자 | 없음 | `200 EvidenceBundleResource` |

`ClaimCreateRequest`는 `domain`, `statement`, `source_id`, `locator.kind`, `locator.value`, scope, assumptions, limitations, publication time, knowledge available time, evidence quality, reproduction status를 요구한다. `locator.kind`는 `PAGE|SECTION|PARAGRAPH|TIMESTAMP|ROW_RANGE|URL_FRAGMENT`다. AI가 만든 claim은 `UNREVIEWED` 상태이고 질문의 유일한 지지 근거가 될 수 없다.

### 9.4 Mandate, Question, Program

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/mandates` | RESEARCHER | `ResearchMandateCreateRequest` | `201 ResearchMandate` |
| `GET /v1/mandates/{mandate_id}` | 소유자, reviewer, auditor | 없음 | `200 ResearchMandate` |
| `POST /v1/mandates/{mandate_id}/compile` | RESEARCHER | `{capability_snapshot_id}` | `202 JobResource` |
| `POST /v1/mandates/{mandate_id}/route` | RESEARCHER | `{capability_snapshot_id}` | `202 JobResource` |
| `POST /v1/mandates/{mandate_id}/questions/generate` | RESEARCHER | `QuestionGenerationRequest` | `202 JobResource` |
| `GET /v1/mandates/{mandate_id}/questions` | 인증 사용자 | status/domain/cursor | `200 ResearchQuestion[]` |
| `GET /v1/questions/{question_id}` | 인증 사용자 | 없음 | `200 ResearchQuestion` |
| `POST /v1/questions/{question_id}/audit` | RESEARCHER, RESEARCH_REVIEWER | `{capability_snapshot_id, auditor_set_version}` | `202 JobResource` |
| `POST /v1/mandates/{mandate_id}/program/build` | RESEARCHER | `ProgramBuildRequest` | `202 JobResource` |
| `GET /v1/programs/{program_id}` | 인증 사용자 | 없음 | `200 ResearchProgram` |
| `POST /v1/programs/{program_id}/run` | RESEARCHER | `{execution_mode:"UNTIL_GATE"|"FULL_NON_SEALED"}` | `202 JobResource` |

`QuestionGenerationRequest`:

```json
{
  "evidence_bundle_id": "b7849309-d998-4d72-937e-b4e0414a5623",
  "capability_snapshot_id": "202e3be8-80fc-4223-84d4-97830d4ef4fe",
  "candidate_count": 20,
  "generation_policy_version": "1.0.0",
  "model_profile_ref": {
    "ref_id": "llm:research-generator",
    "version": "2026.07",
    "content_hash": "blake3:..."
  }
}
```

`candidate_count`는 남은 mandate budget 이하여야 한다. 배분 슬롯은 FR-018의 largest-remainder 방식으로 server가 계산하며 client가 임의 비율을 전달하지 않는다.

`ProgramBuildRequest`는 `question_ids` 1~100개, `capability_snapshot_id`, `maximum_parallel_nodes` 1~64를 가진다. audit pass가 아닌 질문, 서로 다른 mandate의 질문, cycle을 만드는 명시 dependency는 거부한다.

### 9.5 Hypothesis와 Strategy

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/questions/{question_id}/hypothesis/compile` | RESEARCHER | `{compiler_version, model_profile_ref}` | `202 JobResource` |
| `GET /v1/hypotheses/{hypothesis_id}` | 인증 사용자 | 없음 | `200 HypothesisSpec` |
| `POST /v1/hypotheses/{hypothesis_id}/strategy/compile` | RESEARCHER | `{compiler_version, config_profile_ids}` | `202 JobResource` |
| `GET /v1/strategies/{strategy_id}` | 인증 사용자 | 없음 | `200 StrategySpec + registry status` |
| `POST /v1/strategies/{strategy_id}/freeze` | RESEARCHER | `{reason}` + `If-Match` | `200 StrategySpec` |

freeze는 strategy revision을 immutable하게 만든다. 변경은 `parent_resource_id`가 있는 새 strategy compile이다. sealed evaluation에 사용된 strategy는 새 revision의 parent가 될 수 있지만 새 계보는 이전 sealed partition을 사용할 수 없다.

### 9.6 Experiment와 Validation

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/strategies/{strategy_id}/experiments` | RESEARCHER | `ExperimentCreateRequest` | cache hit `200`, 새 실험 `201` |
| `POST /v1/experiments/{experiment_id}/run` | RESEARCHER | `{mode:"TRAIN_VALIDATION_ROBUSTNESS"}` | `202 JobResource` |
| `GET /v1/experiments/{experiment_id}` | 인증 사용자 | `view=RESEARCHER|REVIEWER` | `200 ExperimentResource` |
| `POST /v1/experiments/{experiment_id}/validate` | RESEARCHER, RESEARCH_REVIEWER | `{maximum_gate:"G8", rule_set_version}` | `202 JobResource` |
| `POST /v1/experiments/{experiment_id}/sealed-evaluations` | RESEARCH_REVIEWER, HUMAN | `SealedEvaluationRequest` | `202 JobResource` |
| `GET /v1/validation-reports/{report_id}` | 인증 사용자 | view에 따른 redaction | `200 ValidationReport` |
| `GET /v1/experiments/{experiment_id}/attribution` | 인증 사용자 | 없음 | `200 AttributionReport` |

`ExperimentCreateRequest`:

```json
{
  "dataset_version_ids": ["ac4a5970-68b3-4a1b-a2fc-4cf1e29121a3"],
  "config_profile_id": "7f7a2ec8-079d-4ac8-ae51-1bd94caecf46",
  "engine": {
    "ref_id": "engine.panel_portfolio",
    "version": "1.0.0",
    "content_hash": "blake3:..."
  },
  "code_revision": "git:7ab4c1e",
  "random_seed": 42,
  "period": {
    "train": {"start": "2010-01-01T00:00:00Z", "end_exclusive": "2019-01-01T00:00:00Z"},
    "validation": {"start": "2019-01-01T00:00:00Z", "end_exclusive": "2023-01-01T00:00:00Z"},
    "robustness": [],
    "sealed_partition_ref": null
  }
}
```

Server는 referenced config 전체를 canonical fingerprint에 넣는다. client가 fingerprint를 제공하지 않는다.

`SealedEvaluationRequest`:

```json
{
  "lineage_id": "e067a8aa-9bb6-4a50-b65e-f8f4e4285062",
  "sealed_partition_id": "d45540aa-1ab9-42e3-940a-63ac9cb31ee4",
  "approval_reason": "G0-G8 passed and all parameters are frozen.",
  "expected_validation_report_id": "b28e6f9b-344f-4250-bae1-cb6ff0eca077"
}
```

요청 transaction은 lineage, strategy freeze, G0~G8 pass, access count, 사람 actor를 잠근다. 성공한 202는 access를 소비한다. job 실패는 같은 access-bound job을 재개하며 새 POST를 허용하지 않는다.

### 9.7 Registry, Transition, Report, Artifact

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `POST /v1/strategies/{strategy_id}/register` | RESEARCH_REVIEWER, HUMAN | validation report와 reason | `200 StrategyRegistryResource` |
| `POST /v1/strategies/{strategy_id}/transitions` | 상태별 승인 role | `StrategyTransitionRequest` | `200 StrategyRegistryResource` |
| `GET /v1/strategies/{strategy_id}/registry` | 인증 사용자 | 없음 | `200 StrategyRegistryResource` |
| `POST /v1/strategies/{strategy_id}/reports` | RESEARCHER, REVIEWER | `{format:["JSON","MARKDOWN"]}` | `202 JobResource` |
| `GET /v1/strategies/{strategy_id}/report` | 인증 사용자 | `Accept` | `200 report` 또는 `303` artifact URL |
| `GET /v1/artifacts/{artifact_id}` | 권한 있는 사용자 | 없음 | metadata, short-lived download link |

`StrategyTransitionRequest`:

```json
{
  "from_state": "SHADOW",
  "to_state": "HUMAN_APPROVED",
  "reason": "Shadow metrics remained within approved risk limits for 30 sessions.",
  "evidence_artifact_ids": ["ce0eb494-f2f9-4857-a1f6-749be254b3a7"]
}
```

`LIVE_RECORDED` 전이는 첫 요청을 `PENDING_SECOND_APPROVAL`로 기록한다. 서로 다른 사람의 필요한 두 번째 role 승인이 같은 transition request id에 추가된 뒤에만 aggregate state가 바뀐다.

### 9.8 Job과 Event Query

| Method / Path | 권한 | Request | 성공 |
|---|---|---|---|
| `GET /v1/jobs/{job_id}` | 소유자, reviewer, admin | 없음 | `200 JobResource` |
| `POST /v1/jobs/{job_id}/cancel` | 소유자, admin | `{reason}` | `202 JobResource` |
| `GET /v1/events` | AUDITOR, PLATFORM_ADMIN | `after`, `aggregate_id`, `type`, `limit` | `200 CloudEvent[]` |

완료·실패 job cancel은 멱등하게 현재 resource를 반환한다. `RUNNING` cancel은 `CANCELLING`으로 전환하고 worker의 다음 cancellation checkpoint에서 종료한다. sealed evaluation은 계산 취소가 가능하지만 consumed access는 복원하지 않는다.

## 10. Event Schema

### 10.1 Envelope

```json
{
  "specversion": "1.0",
  "id": "bc19351d-cbd2-430e-b759-b6d64189b82b",
  "source": "/alpha-foundry/experiment-service",
  "type": "af.experiment.completed.v1",
  "subject": "experiments/97989caa-7293-419c-9751-401ea9954b08",
  "time": "2026-07-11T05:30:00.123456Z",
  "datacontenttype": "application/json",
  "dataschema": "af://events/experiment-completed/1.0",
  "correlationid": "c02cb6b8-6890-463f-afbb-b21b4f2403f5",
  "causationid": "5ea2dc50-7c73-45c4-a312-2ef28c46f7df",
  "aggregateversion": 7,
  "data": {
    "experiment_id": "97989caa-7293-419c-9751-401ea9954b08",
    "fingerprint": "blake3:...",
    "domain": "FACTOR_PORTFOLIO",
    "status": "VALIDATION_EVALUATED",
    "result_artifact_ids": ["ce0eb494-f2f9-4857-a1f6-749be254b3a7"]
  }
}
```

`correlationid`, `causationid`, `aggregateversion`은 필수 extension이다. event consumer는 `id`로 deduplicate하고 aggregateversion gap을 발견하면 resource API로 재동기화한다.

### 10.2 Event catalog

| Type | 필수 data |
|---|---|
| `af.mandate.created.v1` | `mandate_id`, `mode`, `actor_id`, `status` |
| `af.mandate.routed.v1` | `mandate_id`, `primary_domain`, `auxiliary_interfaces`, `capability_snapshot_id` |
| `af.question.generated.v1` | `question_id`, `mandate_id`, `domain`, `generation_method`, `search_event_id` |
| `af.question.audited.v1` | `question_id`, `decision`, `fatal_reasons`, `audit_report_id` |
| `af.program.ready.v1` | `program_id`, `mandate_id`, `program_hash`, `node_count` |
| `af.job.status-changed.v1` | `job_id`, `job_type`, `from_status`, `to_status`, `attempt`, `error_code` |
| `af.experiment.created.v1` | `experiment_id`, `strategy_id`, `fingerprint`, `domain` |
| `af.experiment.completed.v1` | 예시와 동일 |
| `af.experiment.rejected.v1` | `experiment_id`, `failed_gate`, `rule_ids`, `rejection_report_id` |
| `af.validation.completed.v1` | `validation_report_id`, `experiment_id`, `overall_decision`, `last_gate` |
| `af.holdout.accessed.v1` | `access_id`, `lineage_id`, `experiment_id`, `actor_id`, `outcome` |
| `af.strategy.registered.v1` | `strategy_id`, `validation_report_id`, `registry_state` |
| `af.strategy.state-changed.v1` | `strategy_id`, `from_state`, `to_state`, `human_approval_ids` |
| `af.artifact.committed.v1` | `artifact_id`, `owner_type`, `owner_id`, `content_hash`, `media_type` |
| `af.capability.snapshot-created.v1` | `snapshot_id`, `snapshot_hash`, component refs |

Event data는 full resource를 복제하지 않는다. ID, version, routing과 불변성 검증에 필요한 hash만 포함한다.

## 11. Error Code

| HTTP | Code | 조건 | Retryable |
|---:|---|---|:---:|
| 400 | `AF-REQUEST-001` | malformed JSON, header 형식 오류 | 아니오 |
| 400 | `AF-REQUEST-002` | cursor와 filter·sort가 일치하지 않음 | 새 cursor로 가능 |
| 401 | `AF-AUTH-001` | token 없음·만료·서명·audience 오류 | 아니오 |
| 403 | `AF-AUTH-002` | role 부족 | 아니오 |
| 403 | `AF-AUTH-003` | AI/service가 human-only 동작 요청 | 아니오 |
| 404 | `AF-RESOURCE-001` | resource 없음 또는 tenant 밖 | 아니오 |
| 409 | `AF-IDEMPOTENCY-001` | 같은 key에 다른 request hash | 아니오 |
| 409 | `AF-STATE-001` | 불법 상태 전이 | 아니오 |
| 409 | `AF-DOMAIN-001` | domain/payload/engine/validator 불일치 | 아니오 |
| 409 | `AF-DOMAIN-002` | 허용되지 않은 cross-domain composition | 아니오 |
| 409 | `AF-FINGERPRINT-001` | 같은 fingerprint의 비호환 완료 상태 | 아니오 |
| 409 | `AF-HOLDOUT-001` | lineage access 이미 소비 | 아니오 |
| 409 | `AF-HOLDOUT-002` | G0~G8 또는 freeze 선행 조건 미충족 | 아니오 |
| 409 | `AF-HOLDOUT-003` | sealed 결과 이후 lineage 수정 시도 | 아니오 |
| 409 | `AF-DAG-001` | cycle·없는 dependency·예산 불일치 | 아니오 |
| 409 | `AF-RELEASE-001` | 유효한 기능이 현재 release에서 비활성 | 활성 release에서 가능 |
| 412 | `AF-CONCURRENCY-001` | `If-Match` version 불일치 | client reread 후 가능 |
| 422 | `AF-SCHEMA-001` | schema, enum, extra field, discriminator 오류 | 아니오 |
| 422 | `AF-CAPABILITY-001` | 필요한 capability 없음 | capability 변경 후 가능 |
| 422 | `AF-CAPABILITY-002` | plugin/schema version 비호환 | 아니오 |
| 422 | `AF-BUDGET-001` | candidate/trial/revision/token/compute 예산 초과 | 새 mandate에서 가능 |
| 422 | `AF-DATA-001` | 미래 정보·PIT·timezone 위반 | 데이터 정정 후 가능 |
| 422 | `AF-DATA-002` | schema/quality/coverage 부족 | 데이터 정정 후 가능 |
| 422 | `AF-VALIDATION-001` | fatal gate 실패 | 아니오 |
| 422 | `AF-CONFIG-001` | policy 누락·단위·범위 오류 | 아니오 |
| 429 | `AF-RATE-001` | actor/API rate limit | 예, `Retry-After` |
| 429 | `AF-LLM-001` | provider rate limit budget 소진 | 예 |
| 500 | `AF-INTERNAL-001` | 분류되지 않은 내부 오류 | 조건부 |
| 502 | `AF-LLM-002` | LLM 응답 schema repair 실패 | 요청 수정 후 가능 |
| 503 | `AF-DEPENDENCY-001` | DB/blob/identity dependency unavailable | 예 |
| 503 | `AF-JOB-001` | worker capacity unavailable | 예 |
| 507 | `AF-RESOURCE-002` | job memory/output resource limit 초과 | 더 큰 profile에서 가능 |

오류 `details`는 code별 strict schema를 가진다. 예를 들어 `AF-SCHEMA-001`은 `{violations:[{path,rule,received_type}]}`, `AF-CAPABILITY-001`은 `{missing:[string]}`, `AF-VALIDATION-001`은 `{gate,rule_ids,report_id}`다.

## 12. 멱등성, ETag, Pagination, Rate Limit

### 12.1 멱등성

- 모든 POST는 `Idempotency-Key`가 필수다. health와 read-only GET에는 사용하지 않는다.
- request hash는 method, normalized path, canonical body, actor id를 포함한다.
- 처리 중 같은 key 요청은 `202`와 기존 JobResource를 반환한다. 별도의 중복 오류를 만들지 않는다.
- 최초 response status, body, selected headers를 저장해 성공·정상 rejection을 재현한다.

### 12.2 ETag

Mutable resource GET은 `ETag: "{row_version}"`을 반환한다. 상태 전이·cancel·freeze는 `If-Match`가 필수다. append-only revision 생성 endpoint는 ETag 대신 parent resource id를 사용한다.

### 12.3 Cursor pagination

- `limit` 기본 50, 최소 1, 최대 200.
- cursor는 server-signed opaque string이며 sort key, filter hash, direction을 포함한다.
- 기본 정렬은 `(created_at DESC, id DESC)`다.
- cursor와 다른 filter를 보내면 `400 AF-REQUEST-002`다.

### 12.4 Rate limit

| 주체/동작 | 한도 |
|---|---:|
| 일반 metadata GET | actor당 300/min |
| 일반 mutation | actor당 60/min |
| LLM generation enqueue | actor당 20/min, mandate당 동시 1개 |
| experiment enqueue | actor당 30/min, strategy당 동시 1개 |
| sealed evaluation | lineage당 평생 1회 |

## 13. Feedback View와 Redaction

| View | Train | Validation | Sealed | 권한 |
|---|---|---|---|---|
| `GENERATOR` | 상세 metric | 범주형 진단만 | 존재 여부도 숨김 | AI Agent 내부 |
| `RESEARCHER` | 상세 metric | 상세 metric과 gate | sealed overall decision; 세부 metric은 policy에 따라 숨김 | Researcher |
| `REVIEWER` | 상세 metric | 상세 metric | 상세 metric | Human Reviewer |
| `AUDIT` | 모든 versioned input/output | 모든 metric | 모든 접근·metric | Auditor |

범주형 validation feedback은 `STRONGLY_NEGATIVE|NEGATIVE|INCONCLUSIVE|POSITIVE|STRONGLY_POSITIVE`와 rule category만 제공한다. threshold, exact metric, 최적 parameter 방향은 포함하지 않는다.

## 14. API와 Schema Versioning

1. HTTP breaking change는 `/v2`와 같은 새 path major를 사용한다.
2. 같은 major 안에서 optional response field 추가, 새 endpoint, 새 event type은 additive다.
3. required request field 추가, enum 제거·의미 변경, field type 변경, 상태 전이 제거는 breaking이다.
4. 각 domain schema는 semantic version을 가진다. patch는 설명·constraint error text만, minor는 backward-compatible optional field, major는 discriminator 또는 required field 변경이다.
5. persisted resource는 생성 당시 schema version을 보존한다. reader는 해당 version validator로 읽고 explicit migration command 없이 rewrite하지 않는다.
6. plugin은 지원하는 input schema version range를 선언한다. Registry는 실행 전에 exact compatibility를 계산한다.
7. event `type` 끝의 `v1`은 data schema major다. breaking event는 새 type으로 동시 발행 기간 30일을 둔다.
8. deprecated endpoint/header/field는 response `Deprecation`과 `Sunset` header를 최소 90일 제공한다.

## 15. CLI Mapping

| CLI | Application command / HTTP 대응 |
|---|---|
| `alpha-foundry mandate create` | `POST /v1/mandates` |
| `alpha-foundry mandate compile` | mandate compile |
| `alpha-foundry program generate` | questions generate → audit → program build orchestration |
| `alpha-foundry program run` | `POST /v1/programs/{id}/run` |
| `alpha-foundry experiment run` | create/cache lookup → run |
| `alpha-foundry experiment validate` | validation endpoint |
| `alpha-foundry holdout run` | human reviewer 전용 sealed endpoint |
| `alpha-foundry report show` | report GET |
| `alpha-foundry job watch` | job GET polling, 1~30초 interval |

CLI는 API schema와 error code를 그대로 표시하고 별도 validation 기본값을 추가하지 않는다.

## 16. OpenAPI와 실행 계약 일치

- Pydantic model에서 `schemas/api/*.json`과 `openapi.json`을 결정론적으로 생성한다.
- CI는 생성 결과와 repository snapshot을 byte 비교한다.
- endpoint마다 정상 response, 모든 선언 error response, auth, idempotency, ETag를 OpenAPI에 선언한다.
- built-in 8개 question/hypothesis/strategy union은 discriminator mapping을 명시한다.
- example은 test fixture에서 생성하며 문서 전용 수동 example을 두지 않는다.
- consumer contract test는 직전 minor release의 OpenAPI와 backward compatibility를 검사한다.
