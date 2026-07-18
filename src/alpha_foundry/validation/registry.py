"""Frozen ordered common/domain hard-gate registry and publication blockers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from alpha_foundry.domain.errors import ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    Decision,
    Domain,
    GateComparison,
    Metric,
    ValidationGate,
    ValidationProfile,
)

from .pbo import PboCompletenessCertificate


class GateScope(StrEnum):
    """The only two ordered ownership scopes for validation hard gates."""

    COMMON = "COMMON"
    DOMAIN = "DOMAIN"


@dataclass(frozen=True, slots=True)
class RegisteredGate:
    """One profile gate and its fixed registry scope."""

    gate: ValidationGate
    scope: GateScope


@dataclass(frozen=True, slots=True)
class GateCheck:
    """One data-only hard-gate result; missing values intentionally remain absent."""

    gate_id: str
    scope: GateScope
    decision: Decision
    metric_name: str
    threshold: Decimal
    comparison: GateComparison
    observed_value: Decimal | None
    reason: ErrorDetail | None

    def __post_init__(self) -> None:
        if not self.gate_id:
            raise ValueError("gate_id must be non-empty")
        if not self.metric_name:
            raise ValueError("metric_name must be non-empty")
        if not isinstance(self.comparison, GateComparison):
            raise ValueError("gate comparison must be a GateComparison")
        if not isinstance(self.decision, Decision):
            raise ValueError("gate decision must be a Decision")
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite():
            raise ValueError("gate thresholds must be finite")
        if self.observed_value is not None and (
            not isinstance(self.observed_value, Decimal) or not self.observed_value.is_finite()
        ):
            raise ValueError("observed gate values must be finite")
        if self.decision is Decision.PASS:
            if self.observed_value is None or self.reason is not None:
                raise ValueError("a passing gate check needs an observed value and no reason")
        elif self.reason is None:
            raise ValueError("a failed gate check requires a typed reason")
        if self.observed_value is not None:
            expected_decision = (
                Decision.PASS
                if _comparison_passes(self.comparison, self.observed_value, self.threshold)
                else Decision.FAIL
            )
            if self.decision is not expected_decision:
                raise ValueError("gate decision must match the comparison and observed value")


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    """Ordered hard-gate evaluation that stops at the first hard failure."""

    profile_hash: str
    domain: Domain
    decision: Decision
    checks: tuple[GateCheck, ...]
    reason: ErrorDetail | None

    def __post_init__(self) -> None:
        if not self.profile_hash:
            raise ValueError("profile_hash must be non-empty")
        if self.decision is Decision.PASS:
            if self.reason is not None or any(
                check.decision is Decision.FAIL for check in self.checks
            ):
                raise ValueError("a passing gate evaluation cannot contain a failure")
        elif self.reason is None:
            raise ValueError("a failed gate evaluation requires a typed reason")

    @property
    def blocks_publication(self) -> bool:
        """Return whether this evaluation must block holdout and publication."""
        return self.decision is Decision.FAIL


@dataclass(frozen=True, slots=True)
class PublicationBlock:
    """A typed non-retryable reason for withholding public visibility."""

    reason: ErrorDetail


@dataclass(frozen=True, slots=True)
class GateRegistry:
    """An immutable common-then-domain hard-gate registry bound to one profile hash.

    Construction validates that the registry's exact gate sequence is the frozen
    profile sequence.  A later profile, threshold, ordering, or domain change
    therefore cannot be evaluated through this instance.
    """

    profile_hash: str
    domain: Domain
    common_gates: tuple[ValidationGate, ...]
    domain_gates: tuple[ValidationGate, ...]

    def __post_init__(self) -> None:
        if not self.profile_hash:
            raise ValueError("profile_hash must be non-empty")
        gates = self.common_gates + self.domain_gates
        if not gates:
            raise ValueError("a gate registry requires at least one gate")
        gate_ids = tuple(gate.gate_id for gate in gates)
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("a gate registry cannot contain duplicate gate IDs")

    @classmethod
    def freeze(
        cls,
        *,
        profile: ValidationProfile,
        common_gates: Iterable[ValidationGate],
        domain_gates: Iterable[ValidationGate],
    ) -> GateRegistry:
        """Bind explicit common/domain gates to one already-frozen profile."""
        registry = cls(
            profile_hash=profile.profile_hash,
            domain=profile.domain,
            common_gates=tuple(common_gates),
            domain_gates=tuple(domain_gates),
        )
        if registry.gates != profile.hard_gates:
            raise ValueError(
                "common and domain registry order must exactly match the frozen profile"
            )
        return registry

    @property
    def gates(self) -> tuple[ValidationGate, ...]:
        """Return the frozen common-first and domain-second gate sequence."""
        return self.common_gates + self.domain_gates

    @property
    def registered_gates(self) -> tuple[RegisteredGate, ...]:
        """Return data-only gate ownership records in their evaluation order."""
        return tuple(
            RegisteredGate(gate=gate, scope=GateScope.COMMON) for gate in self.common_gates
        ) + tuple(RegisteredGate(gate=gate, scope=GateScope.DOMAIN) for gate in self.domain_gates)

    def evaluate(self, *, profile: ValidationProfile, metrics: Iterable[Metric]) -> GateEvaluation:
        """Evaluate in registry order, returning immediately after the first hard fail."""
        profile_error = self._profile_error(profile)
        if profile_error is not None:
            return GateEvaluation(
                profile_hash=self.profile_hash,
                domain=self.domain,
                decision=Decision.FAIL,
                checks=(),
                reason=profile_error,
            )

        metric_values, duplicate_names = _metric_values(metrics)
        checks: list[GateCheck] = []
        for registered in self.registered_gates:
            gate = registered.gate
            if gate.metric_name in duplicate_names:
                reason = _validation_reason(
                    f"metric {gate.metric_name!r} was supplied more than once",
                    path=f"metrics.{gate.metric_name}",
                )
                check = GateCheck(
                    gate_id=gate.gate_id,
                    scope=registered.scope,
                    decision=Decision.FAIL,
                    metric_name=gate.metric_name,
                    threshold=gate.threshold,
                    comparison=gate.comparison,
                    observed_value=None,
                    reason=reason,
                )
                checks.append(check)
                return _failed_evaluation(self, checks, reason)
            observed = metric_values.get(gate.metric_name)
            if observed is None:
                reason = _validation_reason(
                    f"required metric {gate.metric_name!r} is missing",
                    path=f"metrics.{gate.metric_name}",
                )
                check = GateCheck(
                    gate_id=gate.gate_id,
                    scope=registered.scope,
                    decision=Decision.FAIL,
                    metric_name=gate.metric_name,
                    threshold=gate.threshold,
                    comparison=gate.comparison,
                    observed_value=None,
                    reason=reason,
                )
                checks.append(check)
                return _failed_evaluation(self, checks, reason)
            if not observed.is_finite():
                reason = _validation_reason(
                    f"metric {gate.metric_name!r} is non-finite",
                    path=f"metrics.{gate.metric_name}",
                )
                check = GateCheck(
                    gate_id=gate.gate_id,
                    scope=registered.scope,
                    decision=Decision.FAIL,
                    metric_name=gate.metric_name,
                    threshold=gate.threshold,
                    comparison=gate.comparison,
                    observed_value=None,
                    reason=reason,
                )
                checks.append(check)
                return _failed_evaluation(self, checks, reason)
            if _comparison_passes(gate.comparison, observed, gate.threshold):
                checks.append(
                    GateCheck(
                        gate_id=gate.gate_id,
                        scope=registered.scope,
                        decision=Decision.PASS,
                        metric_name=gate.metric_name,
                        threshold=gate.threshold,
                        comparison=gate.comparison,
                        observed_value=observed,
                        reason=None,
                    )
                )
                continue
            reason = _validation_reason(
                f"metric {gate.metric_name!r} failed hard gate {gate.gate_id!r}",
                path=f"metrics.{gate.metric_name}",
            )
            check = GateCheck(
                gate_id=gate.gate_id,
                scope=registered.scope,
                decision=Decision.FAIL,
                metric_name=gate.metric_name,
                threshold=gate.threshold,
                comparison=gate.comparison,
                observed_value=observed,
                reason=reason,
            )
            checks.append(check)
            return _failed_evaluation(self, checks, reason)

        return GateEvaluation(
            profile_hash=self.profile_hash,
            domain=self.domain,
            decision=Decision.PASS,
            checks=tuple(checks),
            reason=None,
        )

    def _profile_error(self, profile: ValidationProfile) -> ErrorDetail | None:
        if profile.domain is not self.domain:
            return _validation_reason("validation profile domain does not match the gate registry")
        if profile.profile_hash != self.profile_hash:
            return _validation_reason(
                "validation profile hash does not match the frozen gate registry"
            )
        if profile.hard_gates != self.gates:
            return _validation_reason(
                "validation profile gates do not match the frozen gate registry"
            )
        return None


def publication_block_reason(
    *, gate_evaluation: GateEvaluation, pbo_certificate: PboCompletenessCertificate
) -> PublicationBlock | None:
    """Return the reason that must block holdout/publication, or ``None`` on pass."""
    if gate_evaluation.decision is Decision.FAIL:
        return PublicationBlock(
            reason=gate_evaluation.reason
            or _validation_reason("hard-gate evaluation failed without a typed reason")
        )
    if not pbo_certificate.is_complete:
        return PublicationBlock(
            reason=pbo_certificate.reason
            or ErrorDetail.for_code(ErrorCode.PBO_INCOMPLETE, "PBO certificate is incomplete")
        )
    if gate_evaluation.profile_hash != pbo_certificate.profile_hash:
        return PublicationBlock(
            reason=_validation_reason("gate evaluation and PBO certificate profile hashes differ")
        )
    return None


def _failed_evaluation(
    registry: GateRegistry, checks: list[GateCheck], reason: ErrorDetail
) -> GateEvaluation:
    return GateEvaluation(
        profile_hash=registry.profile_hash,
        domain=registry.domain,
        decision=Decision.FAIL,
        checks=tuple(checks),
        reason=reason,
    )


def _metric_values(metrics: Iterable[Metric]) -> tuple[dict[str, Decimal], set[str]]:
    values: dict[str, Decimal] = {}
    duplicate_names: set[str] = set()
    for metric in metrics:
        if metric.name in values:
            duplicate_names.add(metric.name)
            continue
        values[metric.name] = metric.value
    return values, duplicate_names


def _comparison_passes(comparison: GateComparison, value: Decimal, threshold: Decimal) -> bool:
    if comparison is GateComparison.LT:
        return value < threshold
    if comparison is GateComparison.LTE:
        return value <= threshold
    if comparison is GateComparison.GT:
        return value > threshold
    if comparison is GateComparison.GTE:
        return value >= threshold
    if comparison is GateComparison.EQ:
        return value == threshold
    raise ValueError(f"unsupported gate comparison: {comparison}")


def _validation_reason(message: str, *, path: str = "validation") -> ErrorDetail:
    return ErrorDetail.for_code(
        ErrorCode.VALIDATION,
        message,
        details=(ErrorField(path=path, reason=message),),
    )
