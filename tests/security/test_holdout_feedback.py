from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from alpha_foundry.domain.models import Decision, DisclosedMetric, GateComparison, GateResult
from alpha_foundry.validation.disclosure import (
    DisclosurePolicyError,
    HoldoutDisclosure,
    HoldoutDisclosurePolicy,
)

_HASH = "sha256:" + "a" * 64
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _PROJECT_ROOT / "src"
_FORBIDDEN_FEEDBACK_PATHS = (
    _SOURCE_ROOT / "alpha_foundry" / "knowledge.py",
    _SOURCE_ROOT / "alpha_foundry" / "generation.py",
    _SOURCE_ROOT / "alpha_foundry" / "application" / "generation.py",
    _SOURCE_ROOT / "alpha_foundry" / "validation" / "pbo.py",
)
_FORBIDDEN_FEEDBACK_DIRECTORIES = (_SOURCE_ROOT / "alpha_foundry" / "search",)
_FORBIDDEN_FEEDBACK_MODULE_PREFIXES = (
    "alpha_foundry.application",
    "alpha_foundry.generation",
    "alpha_foundry.knowledge",
    "alpha_foundry.search",
    "alpha_foundry.validation.pbo",
)
_FORBIDDEN_DISCLOSURE_TOKENS = frozenset(
    {"fold", "observation", "range", "return", "row", "selector", "trace"}
)


def _policy(*, allowed_fields: tuple[str, ...] = ("sharpe",)) -> HoldoutDisclosurePolicy:
    return HoldoutDisclosurePolicy(
        policy_id="holdout-disclosure",
        version="1.0.0",
        policy_hash=_HASH,
        allowed_fields=allowed_fields,
    )


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = path.relative_to(_SOURCE_ROOT).with_suffix("").parts[:-1]
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                module = node.module
            else:
                parent_parts = package_parts[: len(package_parts) - (node.level - 1)]
                target_parts = parent_parts + (
                    () if node.module is None else tuple(node.module.split("."))
                )
                module = ".".join(target_parts)
            if module:
                modules.append(module)
                modules.extend(f"{module}.{alias.name}" for alias in node.names)
    return tuple(modules)


def test_sec_hold_001_disclosure_projects_only_frozen_allowlisted_aggregate_metrics() -> None:
    policy = _policy()

    disclosure = policy.project(
        decision=Decision.PASS,
        aggregate_gate_results=(
            GateResult(
                gate_id="sharpe-gate",
                decision=Decision.PASS,
                metric_name="sharpe",
                threshold=Decimal("1"),
                comparison=GateComparison.GTE,
                observed_value=Decimal("1.25"),
            ),
            GateResult(
                gate_id="sealed-selector-gate",
                decision=Decision.PASS,
                metric_name="selector_score",
                threshold=Decimal("0"),
                comparison=GateComparison.GTE,
                observed_value=Decimal("7"),
            ),
        ),
    )

    assert tuple(metric.name for metric in disclosure.disclosed_metrics) == ("sharpe",)
    assert disclosure.disclosed_metrics[0].value == Decimal("1.25")
    assert set(HoldoutDisclosure.model_fields) == {"decision", "disclosed_metrics"}
    assert set(DisclosedMetric.model_fields) == {"name", "value", "threshold", "decision"}


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "fold_score",
        "observation_count",
        "range_start",
        "return_path",
        "row_id",
        "selector_score",
        "trace_id",
    ),
)
def test_sec_hold_002_policy_rejects_forbidden_sealed_field_names(forbidden_field: str) -> None:
    with pytest.raises(ValueError, match="sealed holdout field"):
        _policy(allowed_fields=(forbidden_field,))


def test_sec_hold_003_policy_rejects_disclosure_outside_the_frozen_allowlist() -> None:
    policy = _policy()
    disclosure = HoldoutDisclosure(
        decision=Decision.PASS,
        disclosed_metrics=(
            DisclosedMetric(
                name="selector_score",
                value=Decimal("7"),
                threshold=Decimal("0"),
                decision=Decision.PASS,
            ),
        ),
    )

    with pytest.raises(DisclosurePolicyError, match="outside the frozen allowlist"):
        policy.validate_disclosure(disclosure)


def test_sec_hold_004_forbidden_feedback_consumers_do_not_import_disclosure() -> None:
    paths = list(_FORBIDDEN_FEEDBACK_PATHS)
    for directory in _FORBIDDEN_FEEDBACK_DIRECTORIES:
        paths.extend(directory.rglob("*.py"))

    violations = [
        path.relative_to(_PROJECT_ROOT).as_posix()
        for path in sorted(paths)
        if "alpha_foundry.validation.disclosure" in _imported_modules(path)
    ]

    assert not violations, "sealed disclosure must not feed research systems:\n" + "\n".join(
        violations
    )


def test_sec_hold_005_disclosure_does_not_import_feedback_or_lineage_services() -> None:
    disclosure_path = _SOURCE_ROOT / "alpha_foundry" / "validation" / "disclosure.py"
    violations = [
        module
        for module in _imported_modules(disclosure_path)
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in _FORBIDDEN_FEEDBACK_MODULE_PREFIXES
        )
    ]

    assert not violations, "disclosure must be a one-way public boundary:\n" + "\n".join(violations)


def test_sec_hold_006_public_disclosure_contract_has_no_sealed_detail_field_names() -> None:
    field_names = set(HoldoutDisclosure.model_fields) | set(DisclosedMetric.model_fields)

    assert not {
        field_name
        for field_name in field_names
        if any(token in field_name.casefold() for token in _FORBIDDEN_DISCLOSURE_TOKENS)
    }
