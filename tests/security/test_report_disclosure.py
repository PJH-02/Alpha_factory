from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.models import (
    Decision,
    DisclosedMetric,
    Domain,
    GateComparison,
    GateResult,
    RankingDirection,
    RankingRule,
    ResearchReport,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.reporting import canonical_research_report_document
from alpha_foundry.validation.disclosure import (
    DisclosurePolicyError,
    HoldoutDisclosure,
    HoldoutDisclosurePolicy,
)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _profile(
    *,
    disclosure_policy_hash: str = _digest(3),
    allowed_disclosure_fields: tuple[str, ...] = ("sharpe", "turnover"),
) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile-1",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(1),
        hard_gates=(
            ValidationGate(
                gate_id="sharpe-gate",
                metric_name="sharpe",
                comparison=GateComparison.GTE,
                threshold=Decimal("1"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="sharpe",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=("fold-1",),
        pbo_minimum_eligible=1,
        patience=1,
        holdout_policy_hash=_digest(2),
        disclosure_policy_hash=disclosure_policy_hash,
        allowed_disclosure_fields=allowed_disclosure_fields,
    )


def _policy() -> HoldoutDisclosurePolicy:
    return HoldoutDisclosurePolicy(
        policy_id="disclosure-policy-1",
        version="v1",
        policy_hash=_digest(3),
        allowed_disclosure_fields=("sharpe", "turnover"),
    )


def _gate_result(
    name: str, value: Decimal, comparison: GateComparison = GateComparison.GTE
) -> GateResult:
    return GateResult(
        gate_id=f"{name}-gate",
        decision=Decision.PASS,
        metric_name=name,
        threshold=Decimal("1"),
        comparison=comparison,
        observed_value=value,
    )


def _report(disclosed_metrics: tuple[DisclosedMetric, ...]) -> ResearchReport:
    return ResearchReport(
        publication_id="publication-1",
        strategy_id="strategy-1",
        domain=Domain.FACTOR,
        evidence_ids=("validation-1",),
        provider_attempt_ids=("attempt-1",),
        dataset_hashes=(_digest(4),),
        config_hash=_digest(5),
        code_hash=_digest(6),
        schema_hashes=(_digest(7),),
        lineage_id="lineage-1",
        validation_decision=Decision.PASS,
        disclosed_metrics=disclosed_metrics,
        artifact_hashes=(_digest(8),),
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value))
    return set()


def test_sec_report_001_disclosure_is_allowlisted_and_policy_ordered() -> None:
    policy = _policy()
    policy.validate_profile(_profile())
    disclosure = policy.project_disclosed_metrics(
        decision=Decision.PASS,
        aggregate_metrics=(
            DisclosedMetric(
                name="unapproved_metric",
                value=Decimal("999"),
                decision=Decision.PASS,
            ),
            DisclosedMetric(
                name="turnover",
                value=Decimal("0.20"),
                threshold=Decimal("0.50"),
                decision=Decision.PASS,
            ),
            DisclosedMetric(
                name="sharpe",
                value=Decimal("1.40"),
                threshold=Decimal("1"),
                decision=Decision.PASS,
            ),
        ),
    )

    assert tuple(metric.name for metric in disclosure.disclosed_metrics) == (
        "sharpe",
        "turnover",
    )
    assert tuple(metric.value for metric in disclosure.disclosed_metrics) == (
        Decimal("1.40"),
        Decimal("0.20"),
    )
    policy.validate_disclosure(disclosure)

    with pytest.raises(DisclosurePolicyError, match="outside the frozen allowlist"):
        policy.validate_disclosure(
            disclosure.model_copy(
                update={
                    "disclosed_metrics": (
                        DisclosedMetric(
                            name="raw_returns",
                            value=Decimal("0.01"),
                            decision=Decision.PASS,
                        ),
                    )
                }
            )
        )


def test_sec_report_003_policy_profile_mismatch_and_sealed_allowlist_names_fail_closed() -> None:
    policy = _policy()
    assert policy.allowed_disclosure_fields == ("sharpe", "turnover")

    with pytest.raises(DisclosurePolicyError, match="pins a different"):
        policy.validate_profile(_profile(disclosure_policy_hash=_digest(9)))
    with pytest.raises(DisclosurePolicyError, match="fields do not match"):
        policy.validate_profile(_profile(allowed_disclosure_fields=("sharpe",)))
    with pytest.raises(ValidationError, match="must be unique"):
        HoldoutDisclosurePolicy(
            policy_id="duplicate-policy",
            version="v1",
            policy_hash=_digest(3),
            allowed_fields=("sharpe", "sharpe"),
        )
    with pytest.raises(ValidationError, match="sealed holdout field"):
        HoldoutDisclosurePolicy(
            policy_id="sealed-policy",
            version="v1",
            policy_hash=_digest(3),
            allowed_fields=("fold_sharpe",),
        )


def test_sec_report_004_projection_discards_unallowlisted_results_and_rejects_duplicates() -> None:
    policy = _policy()
    disclosure = policy.project(
        decision=Decision.PASS,
        aggregate_gate_results=(
            _gate_result("unapproved_metric", Decimal("999")),
            _gate_result("turnover", Decimal("0.20"), GateComparison.LTE),
            _gate_result("sharpe", Decimal("1.40")),
        ),
    )

    assert tuple(metric.name for metric in disclosure.disclosed_metrics) == (
        "sharpe",
        "turnover",
    )
    assert tuple(metric.value for metric in disclosure.disclosed_metrics) == (
        Decimal("1.40"),
        Decimal("0.20"),
    )
    assert "unapproved_metric" not in tuple(metric.name for metric in disclosure.disclosed_metrics)

    with pytest.raises(DisclosurePolicyError, match="multiple aggregate results"):
        policy.project(
            decision=Decision.PASS,
            aggregate_gate_results=(
                _gate_result("sharpe", Decimal("1.40")),
                _gate_result("sharpe", Decimal("1.50")),
            ),
        )
    with pytest.raises(DisclosurePolicyError, match="multiple aggregate metrics"):
        policy.project_disclosed_metrics(
            decision=Decision.PASS,
            aggregate_metrics=(
                DisclosedMetric(
                    name="sharpe",
                    value=Decimal("1.40"),
                    threshold=Decimal("1"),
                    decision=Decision.PASS,
                ),
                DisclosedMetric(
                    name="sharpe",
                    value=Decimal("1.50"),
                    threshold=Decimal("1"),
                    decision=Decision.PASS,
                ),
            ),
        )


def test_sec_report_005_duplicate_disclosure_names_are_rejected_at_model_and_policy_boundaries() -> (
    None
):
    sharpe = DisclosedMetric(
        name="sharpe",
        value=Decimal("1.40"),
        threshold=Decimal("1"),
        decision=Decision.PASS,
    )

    with pytest.raises(ValidationError, match="must be unique"):
        HoldoutDisclosure(
            decision=Decision.PASS,
            disclosed_metrics=(sharpe, sharpe),
        )

    tampered = HoldoutDisclosure(
        decision=Decision.PASS,
        disclosed_metrics=(sharpe,),
    ).model_copy(update={"disclosed_metrics": (sharpe, sharpe)})
    with pytest.raises(DisclosurePolicyError, match="must be unique"):
        _policy().validate_disclosure(tampered)


def test_sec_report_002_public_report_rejects_reconstructive_holdout_fields() -> None:
    report = _report(
        (
            DisclosedMetric(
                name="sharpe",
                value=Decimal("1.40"),
                threshold=Decimal("1"),
                decision=Decision.PASS,
            ),
        )
    )
    document = canonical_research_report_document(report)
    model_document = report.model_dump(mode="python")
    forbidden_fields = {
        "fold_ids": ["fold-1"],
        "holdout_selector": "selector-hash",
        "observations": [{"timestamp": "2026-07-12T00:00:00Z", "return": "0.01"}],
        "per_fold_metrics": {"fold-1": {"sharpe": "1.4"}},
        "trade_log": [{"instrument": "ABC", "quantity": 1}],
        "raw_returns": ["0.01"],
        "trace": {"candidate": "reconstructive-detail"},
    }

    assert set(document["disclosed_metrics"][0]) == {"name", "value", "threshold", "decision"}
    assert not set(forbidden_fields).intersection(_keys(document))
    for field, value in forbidden_fields.items():
        with pytest.raises(ValidationError):
            ResearchReport.model_validate({**model_document, field: value})
