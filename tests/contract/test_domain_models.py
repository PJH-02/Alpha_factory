"""Contract coverage for immutable, discriminated domain models."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from alpha_foundry.domain import (
    AstLiteral,
    AstOperator,
    AstParameter,
    CapabilitySnapshot,
    CrossVenueStrategyAst,
    Decision,
    DerivativesStrategyAst,
    DisclosedMetric,
    Domain,
    ErrorCode,
    ErrorDetail,
    EventFundamentalStrategyAst,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    FactorStrategyAst,
    FrozenModel,
    GateComparison,
    GateResult,
    Job,
    JobKind,
    JobProgress,
    JobStatus,
    MarketMakingStrategyAst,
    Metric,
    ProviderModel,
    RankingDirection,
    RankingRule,
    StatArbStrategyAst,
    StrategySpec,
    StructuralFlowStrategyAst,
    TimeSeriesStrategyAst,
    ValidationGate,
    ValidationProfile,
    ValidationReport,
)

DIGEST = "sha256:" + ("0" * 64)
DOMAIN_AST_MODELS = (
    (Domain.FACTOR, FactorStrategyAst),
    (Domain.STAT_ARB, StatArbStrategyAst),
    (Domain.MARKET_MAKING, MarketMakingStrategyAst),
    (Domain.STRUCTURAL_FLOW, StructuralFlowStrategyAst),
    (Domain.CROSS_VENUE, CrossVenueStrategyAst),
    (Domain.DERIVATIVES, DerivativesStrategyAst),
    (Domain.EVENT_FUNDAMENTAL, EventFundamentalStrategyAst),
    (Domain.TIME_SERIES, TimeSeriesStrategyAst),
)


@pytest.mark.parametrize(
    ("domain", "ast_model"),
    DOMAIN_AST_MODELS,
    ids=[domain.value.lower() for domain, _ in DOMAIN_AST_MODELS],
)
def test_each_domain_has_a_distinct_immutable_typed_ast_model(
    domain: Domain,
    ast_model: type[
        FactorStrategyAst
        | StatArbStrategyAst
        | MarketMakingStrategyAst
        | StructuralFlowStrategyAst
        | CrossVenueStrategyAst
        | DerivativesStrategyAst
        | EventFundamentalStrategyAst
        | TimeSeriesStrategyAst
    ],
) -> None:
    ast = ast_model(root=AstLiteral(kind="literal", value=Decimal("1.25")))
    strategy = StrategySpec(
        strategy_id=f"{domain.value.lower()}-strategy",
        version="1",
        domain=domain,
        operator_set_version="1",
        operator_set_hash=DIGEST,
        schema_hash=DIGEST,
        ast=ast,
    )

    assert tuple(Domain) == tuple(item[0] for item in DOMAIN_AST_MODELS)
    assert ast.domain is domain
    assert type(strategy.ast) is ast_model

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ast_model(root=AstLiteral(kind="literal", value=1), unexpected=True)

    with pytest.raises(ValidationError, match="frozen_instance"):
        ast.root = AstLiteral(kind="literal", value=2)


def test_strategy_spec_rejects_an_ast_from_another_domain() -> None:
    with pytest.raises(ValidationError, match="strategy domain must match"):
        StrategySpec.model_validate(
            {
                "strategy_id": "not-a-factor",
                "version": "1",
                "domain": Domain.STAT_ARB.value,
                "operator_set_version": "1",
                "operator_set_hash": DIGEST,
                "schema_hash": DIGEST,
                "ast": {
                    "domain": Domain.FACTOR.value,
                    "root": {"kind": "literal", "value": "signal"},
                },
            }
        )


def test_frozen_models_forbid_unknown_fields_and_implicit_coercion() -> None:
    provider = ProviderModel(
        ordinal=0,
        provider="deterministic-provider",
        model="deterministic-model",
        config_hash=DIGEST,
    )

    with pytest.raises(ValidationError, match="frozen_instance"):
        provider.ordinal = 1

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProviderModel(
            ordinal=0,
            provider="deterministic-provider",
            model="deterministic-model",
            config_hash=DIGEST,
            display_name="not-a-contract-field",
        )

    with pytest.raises(ValidationError, match="int_type"):
        ProviderModel(
            ordinal="0",
            provider="deterministic-provider",
            model="deterministic-model",
            config_hash=DIGEST,
        )


TIMESTAMP = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)
REASON = ErrorDetail(
    code=ErrorCode.STATE,
    message="The requested state transition is invalid.",
    retryable=False,
)


class JsonCollectionEnvelope(FrozenModel):
    value: tuple[datetime, ...] | Mapping[str, datetime]


def _reason_payload() -> dict[str, object]:
    return REASON.model_dump(mode="json")


def _provider_payload(ordinal: int = 0) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "provider": f"provider-{ordinal}",
        "model": f"model-{ordinal}",
        "config_hash": DIGEST,
    }


def _capability_snapshot(**overrides: object) -> CapabilitySnapshot:
    payload: dict[str, object] = {
        "capability_snapshot_id": "capability-1",
        "content_hash": DIGEST,
        "datasets": [],
        "execution_policies": [],
        "schema_hashes": [],
        "code_version": "1",
        "provider_chain": [_provider_payload()],
    }
    payload.update(overrides)
    return CapabilitySnapshot.model_validate(payload)


def _factor_strategy() -> StrategySpec:
    return StrategySpec(
        strategy_id="factor-strategy",
        version="1",
        domain=Domain.FACTOR,
        operator_set_version="1",
        operator_set_hash=DIGEST,
        schema_hash=DIGEST,
        ast=FactorStrategyAst(root=AstLiteral(kind="literal", value="signal")),
    )


def _dataset_payload(domain: Domain) -> dict[str, object]:
    return {
        "dataset_id": "dataset-1",
        "domain": domain.value,
        "version": "1",
        "content_hash": DIGEST,
    }


def _execution_policy_payload(domain: Domain) -> dict[str, object]:
    return {
        "policy_id": "policy-1",
        "domain": domain.value,
        "version": "1",
        "content_hash": DIGEST,
    }


def _experiment_config(**overrides: object) -> ExperimentConfig:
    payload: dict[str, object] = {
        "experiment_id": "experiment-1",
        "strategy": _factor_strategy(),
        "datasets": [_dataset_payload(Domain.FACTOR)],
        "execution_policies": [_execution_policy_payload(Domain.FACTOR)],
        "schema_hashes": [DIGEST],
        "code_hash": DIGEST,
        "seed": 7,
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def _experiment_result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "experiment_id": "experiment-1",
        "strategy_id": "factor-strategy",
        "domain": Domain.FACTOR.value,
        "config_hash": DIGEST,
        "status": ExperimentStatus.SUCCEEDED.value,
        "metrics": [],
        "artifact_hashes": [],
        "reason": None,
        "started_at": TIMESTAMP.isoformat(),
        "finished_at": (TIMESTAMP + timedelta(minutes=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _job_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-1",
        "kind": JobKind.RUN_EXPERIMENT.value,
        "resource_id": "experiment-1",
        "status": JobStatus.SUCCEEDED.value,
        "stage": "completed",
        "progress": {"completed": 1, "total": 1},
        "error": None,
        "result_ref": "experiment-1",
        "created_at": TIMESTAMP.isoformat(),
        "started_at": (TIMESTAMP + timedelta(minutes=1)).isoformat(),
        "finished_at": (TIMESTAMP + timedelta(minutes=2)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _validation_gate_payload() -> dict[str, object]:
    return {
        "gate_id": "minimum-sharpe",
        "metric_name": "sharpe",
        "comparison": GateComparison.GTE.value,
        "threshold": Decimal("1"),
    }


def _ranking_rule_payload() -> dict[str, object]:
    return {
        "metric_name": "sharpe",
        "direction": RankingDirection.DESC.value,
        "quantization": Decimal("0.001"),
    }


def _validation_profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "validation_profile_id": "profile-1",
        "domain": Domain.FACTOR.value,
        "version": "1",
        "profile_hash": DIGEST,
        "hard_gates": [_validation_gate_payload()],
        "ranking": [_ranking_rule_payload()],
        "fold_ids": ["fold-1"],
        "pbo_minimum_eligible": 1,
        "patience": 1,
        "holdout_policy_hash": DIGEST,
        "disclosure_policy_hash": DIGEST,
        "allowed_disclosure_fields": ["sharpe"],
    }
    payload.update(overrides)
    return payload


def _gate_result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "gate_id": "minimum-sharpe",
        "decision": Decision.PASS.value,
        "metric_name": "sharpe",
        "threshold": Decimal("1"),
        "comparison": GateComparison.GTE.value,
        "observed_value": Decimal("1.2"),
        "reason": None,
    }
    payload.update(overrides)
    return payload


def _validation_report_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "validation_id": "validation-1",
        "experiment_id": "experiment-1",
        "domain": Domain.FACTOR.value,
        "profile_hash": DIGEST,
        "decision": Decision.PASS.value,
        "gate_results": [_gate_result_payload()],
        "metrics": [],
        "reason": None,
        "created_at": TIMESTAMP.isoformat(),
    }
    payload.update(overrides)
    return payload


def test_capability_snapshot_requires_contiguous_ordered_provider_ordinals() -> None:
    snapshot = _capability_snapshot(
        provider_chain=[_provider_payload(0), _provider_payload(1)],
    )

    assert tuple(provider.ordinal for provider in snapshot.provider_chain) == (0, 1)

    with pytest.raises(ValidationError, match="ordinals must be contiguous and ordered"):
        _capability_snapshot(provider_chain=[_provider_payload(1)])


@pytest.mark.parametrize(
    ("model_type", "payload", "message"),
    [
        (
            AstLiteral,
            {"kind": "literal", "value": Decimal("NaN")},
            "finite number",
        ),
        (
            AstLiteral,
            {"kind": "literal", "value": 0.5},
            "public JSON values cannot use binary floating point",
        ),
        (
            AstParameter,
            {"name": "window", "value": Decimal("Infinity")},
            "finite number",
        ),
        (
            AstParameter,
            {"name": "window", "value": 0.5},
            "public JSON values cannot use binary floating point",
        ),
    ],
)
def test_ast_scalars_reject_nonfinite_and_binary_float_values(
    model_type: type[BaseModel],
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        model_type.model_validate(payload)


def test_ast_operator_requires_unique_parameter_names() -> None:
    with pytest.raises(ValidationError, match="parameter names must be unique"):
        AstOperator(
            kind="operator",
            operator_id="rolling_mean",
            parameters=(
                AstParameter(name="window", value=10),
                AstParameter(name="window", value=20),
            ),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"datasets": [_dataset_payload(Domain.STAT_ARB)]},
            "experiment datasets must match",
        ),
        (
            {"execution_policies": [_execution_policy_payload(Domain.STAT_ARB)]},
            "experiment policies must match",
        ),
    ],
)
def test_experiment_config_requires_resources_from_the_strategy_domain(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _experiment_config(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"finished_at": TIMESTAMP - timedelta(seconds=1)},
            "finished_at must not precede started_at",
        ),
        (
            {"reason": _reason_payload()},
            "successful experiment must not carry an error reason",
        ),
        (
            {"status": ExperimentStatus.FAILED.value},
            "failed experiment requires a reason-coded error",
        ),
    ],
)
def test_experiment_results_require_consistent_terminal_state(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ExperimentResult.model_validate(_experiment_result_payload(**overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"started_at": TIMESTAMP - timedelta(seconds=1)},
            "started_at must not precede created_at",
        ),
        (
            {"started_at": None},
            "finished jobs must have started_at",
        ),
        (
            {"finished_at": TIMESTAMP},
            "finished_at must not precede started_at",
        ),
        (
            {"finished_at": None},
            "successful jobs require lifecycle timestamps",
        ),
        (
            {"status": JobStatus.FAILED.value},
            "failed jobs require a reason-coded error",
        ),
        (
            {
                "status": JobStatus.RUNNING.value,
                "finished_at": None,
                "error": _reason_payload(),
            },
            "running jobs must not carry an error or result reference",
        ),
        ({"result_ref": None}, "successful jobs require a result reference"),
    ],
)
def test_jobs_enforce_terminal_lifecycle_invariants(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Job.model_validate(_job_payload(**overrides))


def test_jobs_require_aware_timestamps_and_normalize_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    created_at = datetime(2026, 7, 12, 9, 30, tzinfo=offset)
    job = Job.model_validate(
        _job_payload(
            created_at=created_at.isoformat(),
            started_at=(created_at + timedelta(minutes=1)).isoformat(),
            finished_at=(created_at + timedelta(minutes=2)).isoformat(),
        )
    )

    assert job.created_at == datetime(2026, 7, 12, 4, tzinfo=UTC)
    assert job.created_at.tzinfo is UTC

    with pytest.raises(ValidationError, match="timestamps must be timezone-aware"):
        Job.model_validate(_job_payload(created_at=datetime(2026, 7, 12, 9, 30)))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"fold_ids": ["fold-1", "fold-1"]}, "fold_ids must be unique"),
        (
            {
                "hard_gates": [
                    _validation_gate_payload(),
                    {**_validation_gate_payload(), "metric_name": "sortino"},
                ]
            },
            "hard gate ids must be unique",
        ),
        (
            {
                "ranking": [
                    _ranking_rule_payload(),
                    {**_ranking_rule_payload(), "direction": RankingDirection.ASC.value},
                ]
            },
            "ranking metric names must be unique",
        ),
        (
            {"allowed_disclosure_fields": ["sharpe", "sharpe"]},
            "allowed disclosure fields must be unique",
        ),
    ],
)
def test_validation_profiles_require_unique_identifiers(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ValidationProfile.model_validate(_validation_profile_payload(**overrides))


@pytest.mark.parametrize(
    ("model_type", "payload", "message"),
    [
        (Metric, {"name": "sharpe", "value": Decimal("NaN")}, "finite number"),
        (
            ValidationGate,
            {**_validation_gate_payload(), "threshold": Decimal("Infinity")},
            "finite number",
        ),
        (
            RankingRule,
            {**_ranking_rule_payload(), "quantization": Decimal("Infinity")},
            "finite number",
        ),
        (
            RankingRule,
            {**_ranking_rule_payload(), "quantization": Decimal("0")},
            "ranking quantization must be a positive finite Decimal",
        ),
        (
            DisclosedMetric,
            {
                "name": "sharpe",
                "value": Decimal("1"),
                "threshold": Decimal("NaN"),
                "decision": Decision.PASS.value,
            },
            "finite number",
        ),
    ],
)
def test_numeric_domain_contracts_reject_nonfinite_or_invalid_values(
    model_type: type[BaseModel],
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "decision": Decision.FAIL.value,
                "observed_value": Decimal("0.8"),
            },
            "failed validation gate requires a reason-coded error",
        ),
        (
            {"reason": _reason_payload()},
            "passing validation gate must not carry an error reason",
        ),
        (
            {
                "decision": Decision.FAIL.value,
                "reason": _reason_payload(),
            },
            "gate decision must match the comparison and observed value",
        ),
        (
            {"observed_value": Decimal("NaN")},
            "finite number",
        ),
    ],
)
def test_gate_results_require_decision_consistent_reasons_and_finite_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        GateResult.model_validate(_gate_result_payload(**overrides))


def test_gate_results_require_comparison() -> None:
    payload = _gate_result_payload()
    del payload["comparison"]

    with pytest.raises(ValidationError, match="comparison"):
        GateResult.model_validate(payload)


def test_validation_reports_require_unique_and_decision_consistent_evidence() -> None:
    duplicate_gate_results = [_gate_result_payload(), _gate_result_payload()]
    with pytest.raises(ValidationError, match="gate result ids must be unique"):
        ValidationReport.model_validate(
            _validation_report_payload(gate_results=duplicate_gate_results)
        )

    failed_gate = _gate_result_payload(
        decision=Decision.FAIL.value,
        reason=_reason_payload(),
        observed_value=Decimal("0.8"),
    )
    with pytest.raises(ValidationError, match="cannot contain a failed gate"):
        ValidationReport.model_validate(_validation_report_payload(gate_results=[failed_gate]))

    with pytest.raises(ValidationError, match="passing validation report must not carry"):
        ValidationReport.model_validate(_validation_report_payload(reason=_reason_payload()))

    with pytest.raises(ValidationError, match="failed validation report requires"):
        ValidationReport.model_validate(
            _validation_report_payload(decision=Decision.FAIL.value),
        )

    failed_report = ValidationReport.model_validate(
        _validation_report_payload(
            decision=Decision.FAIL.value,
            reason=_reason_payload(),
        )
    )
    assert failed_report.decision is Decision.FAIL


def test_job_progress_rejects_completed_work_above_total() -> None:
    with pytest.raises(ValidationError, match="completed job progress must not exceed total"):
        JobProgress(completed=2, total=1)


def test_frozen_models_normalize_structured_json_without_float_coercion() -> None:
    timestamp = "2026-07-12T09:30:00Z"
    listed = JsonCollectionEnvelope.model_validate({"value": [timestamp]})
    mapped = JsonCollectionEnvelope.model_validate({"value": {"opened_at": timestamp}})

    assert listed.value == (datetime(2026, 7, 12, 9, 30, tzinfo=UTC),)
    assert mapped.value == {"opened_at": datetime(2026, 7, 12, 9, 30, tzinfo=UTC)}

    with pytest.raises(
        ValidationError, match="public JSON values cannot use binary floating point"
    ):
        JsonCollectionEnvelope.model_validate({"value": [0.5]})
    with pytest.raises(
        ValidationError, match="public JSON values cannot use binary floating point"
    ):
        AstLiteral.model_validate({"kind": "literal", "value": {"nested": [0.5]}})
    with pytest.raises(ValidationError):
        JsonCollectionEnvelope.model_validate(["not-an-object"])
