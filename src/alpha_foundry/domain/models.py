"""Strict, immutable value contracts for the Alpha Foundry domain core."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from types import UnionType
from typing import Annotated, ClassVar, Literal, Union, get_args, get_origin
from unicodedata import category, normalize

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .errors import ErrorDetail


def _canonical_identifier(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = normalize("NFC", value).strip()
    if not normalized:
        raise ValueError("identifiers must not be blank")
    if any(category(character).startswith("C") for character in normalized):
        raise ValueError("identifiers must not contain control characters")
    return normalized


Digest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71),
]
Identifier = Annotated[
    str,
    BeforeValidator(_canonical_identifier),
    StringConstraints(min_length=1),
]
Version = Annotated[
    str,
    BeforeValidator(_canonical_identifier),
    StringConstraints(min_length=1),
]


class Domain(StrEnum):
    FACTOR = "FACTOR"
    STAT_ARB = "STAT_ARB"
    MARKET_MAKING = "MARKET_MAKING"
    STRUCTURAL_FLOW = "STRUCTURAL_FLOW"
    CROSS_VENUE = "CROSS_VENUE"
    DERIVATIVES = "DERIVATIVES"
    EVENT_FUNDAMENTAL = "EVENT_FUNDAMENTAL"
    TIME_SERIES = "TIME_SERIES"


class FrozenModel(BaseModel):
    """Base configuration for immutable public contracts with strict JSON normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    __raw_text_fields__: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _normalize_public_json(cls, value: object) -> object:
        return _normalize_public_json_model(value, cls)


class DatasetRef(FrozenModel):
    """Pinned immutable dataset manifest reference."""

    dataset_id: Identifier
    domain: Domain
    version: Version
    content_hash: Digest


class ExecutionPolicy(FrozenModel):
    """Pinned immutable domain execution-policy reference.

    Policy internals remain domain-owned; this common contract pins their reviewed
    version and semantic content hash without inventing a cross-domain policy schema.
    """

    policy_id: Identifier
    domain: Domain
    version: Version
    content_hash: Digest


class ProviderModel(FrozenModel):
    """One explicitly ordered provider/model entry in a capability snapshot."""

    ordinal: int = Field(ge=0)
    provider: Identifier
    model: Identifier
    config_hash: Digest


class CapabilitySnapshot(FrozenModel):
    """Immutable resources and ordered provider chain available to a request."""

    capability_snapshot_id: Identifier
    content_hash: Digest
    datasets: tuple[DatasetRef, ...]
    execution_policies: tuple[ExecutionPolicy, ...]
    schema_hashes: tuple[Digest, ...]
    code_version: Version
    provider_chain: tuple[ProviderModel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_provider_chain(self) -> CapabilitySnapshot:
        expected_ordinals = tuple(range(len(self.provider_chain)))
        actual_ordinals = tuple(entry.ordinal for entry in self.provider_chain)
        if actual_ordinals != expected_ordinals:
            raise ValueError("provider_chain ordinals must be contiguous and ordered from zero")
        return self


class AstLiteral(FrozenModel):
    """A scalar literal node in a declarative, non-executable strategy AST."""

    kind: Literal["literal"]
    value: str | int | Decimal | bool | None

    @field_validator("value")
    @classmethod
    def _finite_decimal(cls, value: object) -> object:
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("AST Decimal literals must be finite")
        if isinstance(value, float):
            raise ValueError("AST literals must not use binary floating point")
        return value


class AstParameter(FrozenModel):
    """A named scalar parameter for a declared operator invocation."""

    name: Identifier
    value: str | int | Decimal | bool | None

    @field_validator("value")
    @classmethod
    def _finite_decimal(cls, value: object) -> object:
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError("AST Decimal parameters must be finite")
        if isinstance(value, float):
            raise ValueError("AST parameters must not use binary floating point")
        return value


class AstOperator(FrozenModel):
    """A declarative operator node; allowlist checks belong to domain compilers."""

    kind: Literal["operator"]
    operator_id: Identifier
    arguments: tuple[AstLiteral | AstOperator, ...] = ()
    parameters: tuple[AstParameter, ...] = ()

    @model_validator(mode="after")
    def _validate_parameter_names(self) -> AstOperator:
        names = tuple(parameter.name for parameter in self.parameters)
        if len(names) != len(set(names)):
            raise ValueError("operator parameter names must be unique")
        return self


AstOperator.model_rebuild()
AstNode = AstLiteral | AstOperator


class FactorStrategyAst(FrozenModel):
    domain: Literal[Domain.FACTOR] = Domain.FACTOR
    root: AstNode


class StatArbStrategyAst(FrozenModel):
    domain: Literal[Domain.STAT_ARB] = Domain.STAT_ARB
    root: AstNode


class MarketMakingStrategyAst(FrozenModel):
    domain: Literal[Domain.MARKET_MAKING] = Domain.MARKET_MAKING
    root: AstNode


class StructuralFlowStrategyAst(FrozenModel):
    domain: Literal[Domain.STRUCTURAL_FLOW] = Domain.STRUCTURAL_FLOW
    root: AstNode


class CrossVenueStrategyAst(FrozenModel):
    domain: Literal[Domain.CROSS_VENUE] = Domain.CROSS_VENUE
    root: AstNode


class DerivativesStrategyAst(FrozenModel):
    domain: Literal[Domain.DERIVATIVES] = Domain.DERIVATIVES
    root: AstNode


class EventFundamentalStrategyAst(FrozenModel):
    domain: Literal[Domain.EVENT_FUNDAMENTAL] = Domain.EVENT_FUNDAMENTAL
    root: AstNode


class TimeSeriesStrategyAst(FrozenModel):
    domain: Literal[Domain.TIME_SERIES] = Domain.TIME_SERIES
    root: AstNode


TypedStrategyAst = Annotated[
    FactorStrategyAst
    | StatArbStrategyAst
    | MarketMakingStrategyAst
    | StructuralFlowStrategyAst
    | CrossVenueStrategyAst
    | DerivativesStrategyAst
    | EventFundamentalStrategyAst
    | TimeSeriesStrategyAst,
    Field(discriminator="domain"),
]


class StrategySpec(FrozenModel):
    """A single-domain strategy and its typed declarative AST envelope."""

    strategy_id: Identifier
    version: Version
    domain: Domain
    operator_set_version: Version
    operator_set_hash: Digest
    schema_hash: Digest
    ast: TypedStrategyAst

    @model_validator(mode="after")
    def _validate_domain_ast(self) -> StrategySpec:
        if self.ast.domain is not self.domain:
            raise ValueError("strategy domain must match its typed AST envelope")
        return self


class ExperimentConfig(FrozenModel):
    """Frozen non-sealed engine input and all resource pins required to run it."""

    experiment_id: Identifier
    strategy: StrategySpec
    datasets: tuple[DatasetRef, ...] = Field(min_length=1)
    execution_policies: tuple[ExecutionPolicy, ...] = Field(min_length=1)
    schema_hashes: tuple[Digest, ...] = Field(min_length=1)
    code_hash: Digest
    seed: int

    @model_validator(mode="after")
    def _validate_primary_domain(self) -> ExperimentConfig:
        domain = self.strategy.domain
        if any(dataset.domain is not domain for dataset in self.datasets):
            raise ValueError("experiment datasets must match the strategy primary domain")
        if any(policy.domain is not domain for policy in self.execution_policies):
            raise ValueError("experiment policies must match the strategy primary domain")
        return self


class Metric(FrozenModel):
    """A finite Decimal observation identified by a stable metric name."""

    name: Identifier
    value: Decimal

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("metrics must be finite Decimals")
        return value


class ExperimentStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExperimentResult(FrozenModel):
    """Immutable result/provenance returned by a domain-owned engine."""

    experiment_id: Identifier
    strategy_id: Identifier
    domain: Domain
    config_hash: Digest
    status: ExperimentStatus
    metrics: tuple[Metric, ...] = ()
    artifact_hashes: tuple[Digest, ...] = ()
    reason: ErrorDetail | None = None
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def _validate_terminal_result(self) -> ExperimentResult:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.metrics:
            _require_unique(
                tuple(metric.name for metric in self.metrics), "experiment metric names"
            )
        if self.status is ExperimentStatus.SUCCEEDED:
            if self.reason is not None:
                raise ValueError("a successful experiment must not carry an error reason")
        elif self.reason is None:
            raise ValueError("a failed experiment requires a reason-coded error")
        return self


class GateComparison(StrEnum):
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"


class RankingDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class RankingRounding(StrEnum):
    HALF_EVEN = "ROUND_HALF_EVEN"
    HALF_UP = "ROUND_HALF_UP"


class ValidationGate(FrozenModel):
    gate_id: Identifier
    metric_name: Identifier
    comparison: GateComparison
    threshold: Decimal

    @field_validator("threshold")
    @classmethod
    def _finite_threshold(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("validation thresholds must be finite Decimals")
        return value


class RankingRule(FrozenModel):
    metric_name: Identifier
    direction: RankingDirection
    quantization: Decimal
    rounding: RankingRounding = RankingRounding.HALF_EVEN

    @field_validator("quantization")
    @classmethod
    def _positive_quantization(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("ranking quantization must be a positive finite Decimal")
        return value


class ValidationProfile(FrozenModel):
    """Frozen validation semantics pinned before an experiment or search begins."""

    validation_profile_id: Identifier
    domain: Domain
    version: Version
    profile_hash: Digest
    hard_gates: tuple[ValidationGate, ...] = Field(min_length=1)
    ranking: tuple[RankingRule, ...] = Field(min_length=1)
    fold_ids: tuple[Identifier, ...] = Field(min_length=1)
    pbo_minimum_eligible: int = Field(ge=1)
    patience: int = Field(ge=1)
    holdout_policy_hash: Digest
    disclosure_policy_hash: Digest
    allowed_disclosure_fields: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _validate_unique_profile_fields(self) -> ValidationProfile:
        _require_unique(self.fold_ids, "fold_ids")
        _require_unique(tuple(gate.gate_id for gate in self.hard_gates), "hard gate ids")
        _require_unique(tuple(rule.metric_name for rule in self.ranking), "ranking metric names")
        _require_unique(self.allowed_disclosure_fields, "allowed disclosure fields")
        return self


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


def _comparison_passes(
    comparison: GateComparison, observed_value: Decimal, threshold: Decimal
) -> bool:
    if comparison is GateComparison.LT:
        return observed_value < threshold
    if comparison is GateComparison.LTE:
        return observed_value <= threshold
    if comparison is GateComparison.GT:
        return observed_value > threshold
    if comparison is GateComparison.GTE:
        return observed_value >= threshold
    return observed_value == threshold


class GateResult(FrozenModel):
    gate_id: Identifier
    decision: Decision
    metric_name: Identifier
    threshold: Decimal
    comparison: GateComparison
    observed_value: Decimal
    reason: ErrorDetail | None = None

    @field_validator("threshold", "observed_value")
    @classmethod
    def _finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("gate values must be finite Decimals")
        return value

    @model_validator(mode="after")
    def _validate_decision(self) -> GateResult:
        expected = (
            Decision.PASS
            if _comparison_passes(self.comparison, self.observed_value, self.threshold)
            else Decision.FAIL
        )
        if self.decision is not expected:
            raise ValueError("gate decision must match the comparison and observed value")
        if self.decision is Decision.FAIL and self.reason is None:
            raise ValueError("a failed validation gate requires a reason-coded error")
        if self.decision is Decision.PASS and self.reason is not None:
            raise ValueError("a passing validation gate must not carry an error reason")
        return self


class ValidationReport(FrozenModel):
    """Immutable validation gate evidence and resulting pass/fail decision."""

    validation_id: Identifier
    experiment_id: Identifier
    domain: Domain
    profile_hash: Digest
    decision: Decision
    gate_results: tuple[GateResult, ...] = Field(min_length=1)
    metrics: tuple[Metric, ...] = ()
    reason: ErrorDetail | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def _validate_decision_reason(self) -> ValidationReport:
        _require_unique(tuple(result.gate_id for result in self.gate_results), "gate result ids")
        _require_unique(tuple(metric.name for metric in self.metrics), "validation metric names")
        if self.decision is Decision.PASS:
            if any(result.decision is Decision.FAIL for result in self.gate_results):
                raise ValueError("a passing validation report cannot contain a failed gate")
            if self.reason is not None:
                raise ValueError("a passing validation report must not carry an error reason")
        elif self.reason is None:
            raise ValueError("a failed validation report requires a reason-coded error")
        return self


class DisclosedMetric(FrozenModel):
    """Named aggregate metric that a frozen disclosure policy permits in a report."""

    name: Identifier
    value: Decimal
    threshold: Decimal | None = None
    decision: Decision

    @field_validator("value", "threshold")
    @classmethod
    def _finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("disclosed metrics must be finite Decimals")
        return value


class ResearchReport(FrozenModel):
    """The public, immutable, non-sealed publication report contract."""

    publication_id: Identifier
    strategy_id: Identifier
    domain: Domain
    evidence_ids: tuple[Identifier, ...]
    provider_attempt_ids: tuple[Identifier, ...]
    dataset_hashes: tuple[Digest, ...]
    config_hash: Digest
    code_hash: Digest
    schema_hashes: tuple[Digest, ...]
    lineage_id: Identifier
    validation_decision: Decision
    disclosed_metrics: tuple[DisclosedMetric, ...] = Field(min_length=1)
    artifact_hashes: tuple[Digest, ...]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @model_validator(mode="after")
    def _validate_disclosed_metrics(self) -> ResearchReport:
        _require_unique(
            tuple(metric.name for metric in self.disclosed_metrics),
            "disclosed metric names",
        )
        return self


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobKind(StrEnum):
    GENERATE = "GENERATE"
    RUN_SEARCH = "RUN_SEARCH"
    RUN_EXPERIMENT = "RUN_EXPERIMENT"
    PUBLISH = "PUBLISH"


class JobProgress(FrozenModel):
    completed: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_progress(self) -> JobProgress:
        if self.completed > self.total:
            raise ValueError("completed job progress must not exceed total")
        return self


class Job(FrozenModel):
    """Durable job lifecycle state, with a typed terminal error when it failed."""

    job_id: Identifier
    kind: JobKind
    resource_id: Identifier
    status: JobStatus
    stage: Identifier
    progress: JobProgress | None = None
    error: ErrorDetail | None = None
    result_ref: Identifier | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_job_lifecycle(self) -> Job:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.finished_at is not None:
            if self.started_at is None:
                raise ValueError("finished jobs must have started_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at must not precede started_at")

        if self.status is JobStatus.QUEUED:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("queued jobs must not have lifecycle timestamps")
            if self.error is not None or self.result_ref is not None:
                raise ValueError("queued jobs must not carry an error or result reference")
        elif self.status is JobStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("running jobs require started_at and no finished_at")
            if self.error is not None or self.result_ref is not None:
                raise ValueError("running jobs must not carry an error or result reference")
        elif self.status is JobStatus.SUCCEEDED:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("successful jobs require lifecycle timestamps")
            if self.error is not None:
                raise ValueError("successful jobs must not carry an error")
            if self.result_ref is None:
                raise ValueError("successful jobs require a result reference")
        elif self.status is JobStatus.FAILED:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("failed jobs require lifecycle timestamps")
            if self.error is None:
                raise ValueError("failed jobs require a reason-coded error")
            if self.result_ref is not None:
                raise ValueError("failed jobs must not carry a result reference")
        else:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("cancelled jobs require lifecycle timestamps")
            if self.error is not None or self.result_ref is not None:
                raise ValueError("cancelled jobs must not carry an error or result reference")
        return self


def _normalize_public_json_model(value: object, model_type: type[BaseModel]) -> object:
    if not isinstance(value, Mapping):
        return _normalize_untyped_json(value)

    normalized: dict[object, object] = {}
    raw_text_fields: frozenset[str] = getattr(model_type, "__raw_text_fields__", frozenset())
    for name, item in value.items():
        normalized_name = normalize("NFC", name) if isinstance(name, str) else name
        if normalized_name in normalized:
            raise ValueError("public JSON object keys must be unique after normalization")
        field = (
            model_type.model_fields.get(normalized_name)
            if isinstance(normalized_name, str)
            else None
        )
        annotation = (
            field.annotation if field is not None and field.annotation is not None else object
        )
        normalized[normalized_name] = (
            item
            if isinstance(normalized_name, str) and normalized_name in raw_text_fields
            else _normalize_public_json_value(item, annotation)
        )
    return normalized


def _normalize_public_json_value(value: object, annotation: object) -> object:
    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is Annotated:
        return _normalize_public_json_value(value, arguments[0] if arguments else object)

    if origin is Literal:
        for expected in arguments:
            if isinstance(expected, Enum) and value == expected.value:
                return expected
        return _normalize_untyped_json(value)

    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel) and isinstance(value, Mapping):
            return _normalize_public_json_model(value, annotation)
        if issubclass(annotation, Enum) and isinstance(value, str):
            try:
                return annotation(value)
            except ValueError as error:
                raise ValueError(f"invalid {annotation.__name__} value") from error
        if annotation is datetime and isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("timestamps must use ISO 8601 format") from error

    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            return _normalize_untyped_json(value)
        values = tuple(value)
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_normalize_public_json_value(item, arguments[0]) for item in values)
        return tuple(
            _normalize_public_json_value(item, arguments[index])
            if index < len(arguments)
            else _normalize_untyped_json(item)
            for index, item in enumerate(values)
        )

    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            return _normalize_untyped_json(value)
        item_annotation = arguments[1] if len(arguments) == 2 else object
        normalized: dict[object, object] = {}
        for key, item in value.items():
            normalized_key = normalize("NFC", key) if isinstance(key, str) else key
            if normalized_key in normalized:
                raise ValueError("public JSON object keys must be unique after normalization")
            normalized[normalized_key] = _normalize_public_json_value(item, item_annotation)
        return normalized

    if origin in (Union, UnionType):
        for option in arguments:
            option_origin = get_origin(option)
            if option is str and isinstance(value, str):
                return _normalize_untyped_json(value)
            if (
                isinstance(option, type)
                and issubclass(option, BaseModel)
                and isinstance(value, Mapping)
            ):
                return _normalize_public_json_model(value, option)
            if isinstance(option, type) and issubclass(option, Enum) and isinstance(value, str):
                try:
                    return option(value)
                except ValueError:
                    continue
            if option is datetime and isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    continue
            if option_origin is tuple and isinstance(value, (list, tuple)):
                return _normalize_public_json_value(value, option)
            if option_origin in (dict, Mapping) and isinstance(value, Mapping):
                return _normalize_public_json_value(value, option)
        return _normalize_untyped_json(value)

    return _normalize_untyped_json(value)


def _normalize_untyped_json(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("public JSON values cannot use binary floating point")
    if isinstance(value, str):
        return normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_untyped_json(item) for item in value)
    if isinstance(value, Mapping):
        normalized: dict[object, object] = {}
        for key, item in value.items():
            normalized_key = normalize("NFC", key) if isinstance(key, str) else key
            if normalized_key in normalized:
                raise ValueError("public JSON object keys must be unique after normalization")
            normalized[normalized_key] = _normalize_untyped_json(item)
        return normalized
    return value


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _require_unique(values: tuple[object, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
