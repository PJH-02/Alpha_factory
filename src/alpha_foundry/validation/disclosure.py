"""Frozen holdout disclosure projection with a one-way public boundary.

This module intentionally depends only on immutable domain value objects.  It must not
be imported by, or provide inputs to, knowledge, generation, search, ranking, PBO, or
lineage-mutating services.
"""

from __future__ import annotations

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from alpha_foundry.domain.models import (
    Decision,
    DisclosedMetric,
    FrozenModel,
    GateResult,
    Identifier,
    ValidationProfile,
)


class DisclosurePolicyError(ValueError):
    """Raised when a disclosure would cross the sealed holdout boundary."""


_SEALED_FIELD_TOKENS = frozenset(
    {"fold", "observation", "range", "return", "row", "selector", "trace"}
)


class HoldoutDisclosure(FrozenModel):
    """The only non-sealed representation emitted from a holdout evaluation."""

    decision: Decision
    disclosed_metrics: tuple[DisclosedMetric, ...] = ()

    @model_validator(mode="after")
    def _validate_unique_metric_names(self) -> HoldoutDisclosure:
        names = tuple(metric.name for metric in self.disclosed_metrics)
        if len(names) != len(set(names)):
            raise ValueError("disclosed metric names must be unique")
        return self


class HoldoutDisclosurePolicy(FrozenModel):
    """Profile-pinned allowlist of named aggregate fields safe for final reporting.

    Each allowed name represents one aggregate ``decision/value/threshold`` tuple.
    The policy has no selector, row, fold, trace, or per-observation representation,
    so its projection cannot serialize sealed evaluation detail.
    """

    model_config = ConfigDict(populate_by_name=True)

    policy_id: Identifier
    version: Identifier
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_fields: tuple[Identifier, ...] = Field(
        validation_alias=AliasChoices("allowed_fields", "allowed_disclosure_fields"),
        serialization_alias="allowed_fields",
    )

    @property
    def allowed_disclosure_fields(self) -> tuple[Identifier, ...]:
        """Return the profile field name retained for profile compatibility."""
        return self.allowed_fields

    @model_validator(mode="after")
    def _validate_allowed_fields(self) -> HoldoutDisclosurePolicy:
        if len(self.allowed_fields) != len(set(self.allowed_fields)):
            raise ValueError("allowed disclosure fields must be unique")
        if any(
            token in field.casefold()
            for field in self.allowed_fields
            for token in _SEALED_FIELD_TOKENS
        ):
            raise ValueError("allowed disclosure fields cannot name a sealed holdout field")
        return self

    def validate_profile(self, profile: ValidationProfile) -> None:
        """Require the frozen profile to pin this exact disclosure policy."""
        if profile.disclosure_policy_hash != self.policy_hash:
            raise DisclosurePolicyError("validation profile pins a different disclosure policy")
        if profile.allowed_disclosure_fields != self.allowed_fields:
            raise DisclosurePolicyError(
                "validation profile disclosure fields do not match the policy"
            )

    def project(
        self,
        *,
        decision: Decision,
        aggregate_gate_results: tuple[GateResult, ...],
    ) -> HoldoutDisclosure:
        """Project aggregate gate evidence into the safe, policy-ordered public DTO.

        Results for fields outside the allowlist are deliberately discarded.  A field
        requested by the policy may appear at most once; otherwise selecting one would
        invent an aggregation rule and risk disclosing unreproducible detail.
        """
        by_name: dict[str, GateResult] = {}
        for result in aggregate_gate_results:
            if result.metric_name not in self.allowed_fields:
                continue
            if result.metric_name in by_name:
                raise DisclosurePolicyError(
                    f"multiple aggregate results exist for disclosed field {result.metric_name!r}"
                )
            by_name[result.metric_name] = result

        disclosed = tuple(
            DisclosedMetric(
                name=name,
                value=by_name[name].observed_value,
                threshold=by_name[name].threshold,
                decision=by_name[name].decision,
            )
            for name in self.allowed_fields
            if name in by_name
        )
        return HoldoutDisclosure(decision=decision, disclosed_metrics=disclosed)

    def project_disclosed_metrics(
        self,
        *,
        decision: Decision,
        aggregate_metrics: tuple[DisclosedMetric, ...],
    ) -> HoldoutDisclosure:
        """Filter an aggregate-only holdout DTO into the policy-ordered public DTO.

        This accepts only already aggregate ``DisclosedMetric`` values; it has no
        parameter for sealed observations, selectors, rows, folds, or traces.
        """
        by_name: dict[str, DisclosedMetric] = {}
        for metric in aggregate_metrics:
            if metric.name not in self.allowed_fields:
                continue
            if metric.name in by_name:
                raise DisclosurePolicyError(
                    f"multiple aggregate metrics exist for disclosed field {metric.name!r}"
                )
            by_name[metric.name] = metric
        return HoldoutDisclosure(
            decision=decision,
            disclosed_metrics=tuple(
                by_name[name] for name in self.allowed_fields if name in by_name
            ),
        )

    def validate_disclosure(self, disclosure: HoldoutDisclosure) -> None:
        """Verify that an already-projected disclosure remains policy-safe."""
        names = tuple(metric.name for metric in disclosure.disclosed_metrics)
        if len(names) != len(set(names)):
            raise DisclosurePolicyError("disclosed metric names must be unique")
        unknown = tuple(name for name in names if name not in self.allowed_fields)
        if unknown:
            raise DisclosurePolicyError("disclosure contains a field outside the frozen allowlist")


__all__ = [
    "DisclosurePolicyError",
    "HoldoutDisclosure",
    "HoldoutDisclosurePolicy",
]
