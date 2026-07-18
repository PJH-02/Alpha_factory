"""Shared deterministic primitives for domain-owned numerical engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.errors import ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    Domain,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    Metric,
)

_ZERO = Decimal("0")
_DECIMAL_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    capitals=1,
    clamp=1,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class LogicalClock(int):
    """A normalized integer clock that cannot be mixed with wall-clock timestamps."""


type Timestamp = datetime | LogicalClock


class EngineInputError(ValueError):
    """A reason-coded, caller-correctable numerical-engine input error."""

    def __init__(
        self,
        code: ErrorCode,
        path: str,
        reason: str,
        details: Sequence[ErrorField] | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.reason = reason
        self.details = (
            tuple(details) if details is not None else (ErrorField(path=path, reason=reason),)
        )
        super().__init__(f"{code.value} at {path}: {reason}")


@runtime_checkable
class EngineProtocol(Protocol):
    """The only interface shared by domain-owned numerical engines."""

    domain: Domain

    def validate_config(self, config: ExperimentConfig) -> None:
        """Reject a configuration not pinned to this engine's primary domain."""

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        """Execute deterministically without mutating either argument."""


class BaseEngine(ABC):
    """Base result construction and shared strict input conversion helpers."""

    domain: Domain
    engine_id: str

    def validate_config(self, config: ExperimentConfig) -> None:
        if config.strategy.domain is not self.domain:
            raise EngineInputError(
                ErrorCode.DOMAIN,
                "config.strategy.domain",
                f"expected {self.domain.value}, got {config.strategy.domain.value}",
            )
        if not config.execution_policies:
            raise EngineInputError(
                ErrorCode.POLICY,
                "config.execution_policies",
                "at least one pinned execution policy is required",
            )
        for index, policy in enumerate(config.execution_policies):
            if policy.domain is not self.domain:
                raise EngineInputError(
                    ErrorCode.DOMAIN,
                    f"config.execution_policies[{index}].domain",
                    f"expected {self.domain.value}, got {policy.domain.value}",
                )

    def bind_runtime_policy(
        self,
        config: ExperimentConfig,
        *,
        policy_id: str,
        version: str,
        content_hash: str,
        path: str = "execution_policy",
    ) -> None:
        """Require the complete runtime policy identity to be pinned by the config."""

        policies = tuple(
            policy for policy in config.execution_policies if policy.policy_id == policy_id
        )
        if not policies:
            raise EngineInputError(
                ErrorCode.POLICY,
                f"{path}.policy_id",
                "is not pinned by config.execution_policies",
            )
        versions = tuple(policy for policy in policies if policy.version == version)
        if not versions:
            raise EngineInputError(
                ErrorCode.POLICY,
                f"{path}.version",
                "does not match the pinned execution policy",
            )
        matches = tuple(policy for policy in versions if policy.content_hash == content_hash)
        if len(matches) != 1:
            raise EngineInputError(
                ErrorCode.POLICY,
                f"{path}.content_hash",
                "does not match exactly one pinned execution policy",
            )

    @abstractmethod
    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        """Run the domain-specific numerical simulation."""

    def succeeded(
        self,
        config: ExperimentConfig,
        metrics: Sequence[tuple[str, Decimal]],
        artifact: Mapping[str, object],
        *,
        input_snapshot: object | None = None,
        policy_identity: Mapping[str, object] | None = None,
        started_at: datetime = _EPOCH,
        finished_at: datetime = _EPOCH,
    ) -> ExperimentResult:
        """Create a result whose artifact hash covers every replay-relevant identity."""
        metric_models = tuple(Metric(name=name, value=value) for name, value in metrics)
        if len({metric.name for metric in metric_models}) != len(metric_models):
            raise ValueError("engine result metrics must have unique names")
        normalized_started_at = _normalized_boundary(started_at, "started_at")
        normalized_finished_at = _normalized_boundary(finished_at, "finished_at")
        if normalized_finished_at < normalized_started_at:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "run_boundaries",
                "finished_at must not precede started_at",
            )
        pinned_policies = tuple(
            {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "content_hash": policy.content_hash,
            }
            for policy in config.execution_policies
        )
        artifact_hash = digest(
            "alpha-foundry.engine-artifact",
            {
                "artifact": artifact,
                "config_hash": config_digest(config),
                "domain": self.domain.value,
                "engine_id": self.engine_id,
                "input_snapshot": artifact if input_snapshot is None else input_snapshot,
                "metrics": tuple(
                    {"name": metric.name, "value": metric.value} for metric in metric_models
                ),
                "policy_identity": pinned_policies if policy_identity is None else policy_identity,
                "run_boundaries": {
                    "finished_at": normalized_finished_at,
                    "started_at": normalized_started_at,
                },
            },
        )
        return self._result(
            config,
            status=ExperimentStatus.SUCCEEDED,
            metrics=metric_models,
            artifact_hashes=(artifact_hash,),
            started_at=normalized_started_at,
            finished_at=normalized_finished_at,
        )

    def failed(self, config: ExperimentConfig, error: EngineInputError) -> ExperimentResult:
        return self._result(
            config,
            status=ExperimentStatus.FAILED,
            reason=ErrorDetail.for_code(
                error.code,
                "engine input rejected",
                error.details,
            ),
            started_at=_EPOCH,
            finished_at=_EPOCH,
        )

    def _result(
        self,
        config: ExperimentConfig,
        *,
        status: ExperimentStatus,
        started_at: datetime,
        finished_at: datetime,
        metrics: tuple[Metric, ...] = (),
        artifact_hashes: tuple[str, ...] = (),
        reason: ErrorDetail | None = None,
    ) -> ExperimentResult:
        """Create every engine result through one immutable provenance boundary."""

        return ExperimentResult(
            experiment_id=config.experiment_id,
            strategy_id=config.strategy.strategy_id,
            domain=config.strategy.domain,
            config_hash=config_digest(config),
            status=status,
            metrics=metrics,
            artifact_hashes=artifact_hashes,
            reason=reason,
            started_at=started_at,
            finished_at=finished_at,
        )


def _normalized_boundary(value: datetime, path: str) -> datetime:
    """Require and normalize a real UTC run boundary before it reaches lineage."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise EngineInputError(
            ErrorCode.SCHEMA,
            f"run_boundaries.{path}",
            "must be timezone-aware",
        )
    return value.astimezone(UTC)


def config_digest(config: ExperimentConfig) -> str:
    """Return the stable digest recorded on every engine result."""

    return digest("alpha-foundry.experiment-config", config.model_dump(mode="json"))


@contextmanager
def deterministic_decimal_context() -> Iterator[None]:
    """Use fixed Decimal128-style arithmetic independently of caller context."""

    with localcontext(_DECIMAL_CONTEXT):
        yield


def require_mapping(
    value: object, path: str, code: ErrorCode = ErrorCode.SCHEMA
) -> Mapping[str, Any]:
    """Require a string-keyed mapping without copying or mutating it."""

    if not isinstance(value, Mapping):
        raise EngineInputError(code, path, "must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise EngineInputError(code, path, "keys must be strings")
    return value


def require_field(data: Mapping[str, Any], path: str, code: ErrorCode = ErrorCode.SCHEMA) -> object:
    """Return one required field or raise a reason-coded error."""

    if path not in data:
        raise EngineInputError(code, path, "is required")
    return data[path]


def payload_validation_error(
    error: ValidationError,
    *,
    policy_root: str = "execution_policy",
) -> EngineInputError:
    """Preserve Pydantic's ordered diagnostics under one stable engine error code."""

    issues = error.errors(include_url=False)
    fields = tuple(
        ErrorField(
            path=".".join(str(part) for part in issue["loc"]) or "data",
            reason=str(issue["msg"]),
        )
        for issue in issues
    )
    if not fields:
        return EngineInputError(ErrorCode.SCHEMA, "data", "payload validation failed")
    code = ErrorCode.POLICY if fields[0].path.split(".", 1)[0] == policy_root else ErrorCode.SCHEMA
    return EngineInputError(code, fields[0].path, fields[0].reason, fields)


def array_items(value: object, path: str) -> tuple[object, ...]:
    """Normalize a declared array to an immutable tuple in declared order."""

    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be an array")
    if isinstance(value, Sequence):
        return tuple(value)
    raise EngineInputError(ErrorCode.SCHEMA, path, "must be an array")


def decimal_value(value: object, path: str) -> Decimal:
    """Convert array observations through text, never Decimal's binary-float constructor."""

    if isinstance(value, bool):
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be a finite numeric value")
    if isinstance(value, Decimal):
        converted = value
    elif isinstance(value, int):
        converted = Decimal(value)
    elif isinstance(value, (str, float)):
        try:
            converted = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise EngineInputError(
                ErrorCode.SCHEMA, path, "must be a finite numeric value"
            ) from error
    else:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be a finite numeric value")
    if not converted.is_finite():
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be finite")
    return converted


def decimal_vector(value: object, path: str, width: int) -> tuple[Decimal, ...]:
    """Convert an exact-width numeric array to immutable Decimal observations."""

    items = array_items(value, path)
    if len(items) != width:
        raise EngineInputError(ErrorCode.SCHEMA, path, f"must contain exactly {width} values")
    return tuple(decimal_value(item, f"{path}[{index}]") for index, item in enumerate(items))


def decimal_matrix(
    value: object, path: str, rows: int, columns: int
) -> tuple[tuple[Decimal, ...], ...]:
    """Convert an exact-shape numeric matrix to declared-order Decimal rows."""

    matrix = array_items(value, path)
    if len(matrix) != rows:
        raise EngineInputError(ErrorCode.SCHEMA, path, f"must contain exactly {rows} rows")
    return tuple(
        decimal_vector(row, f"{path}[{row_index}]", columns) for row_index, row in enumerate(matrix)
    )


def normalize_timestamp(value: object, path: str) -> Timestamp:
    """Parse aware datetimes to UTC while retaining integer logical clocks."""

    if isinstance(value, bool):
        raise EngineInputError(
            ErrorCode.SCHEMA,
            path,
            "must be an ISO 8601 aware datetime or integer logical clock",
        )
    if isinstance(value, int):
        return LogicalClock(value)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                path,
                "must be an ISO 8601 aware datetime or integer logical clock",
            ) from error
    if not isinstance(value, datetime):
        raise EngineInputError(
            ErrorCode.SCHEMA,
            path,
            "must be an ISO 8601 aware datetime or integer logical clock",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be timezone-aware")
    return value.astimezone(UTC)


def _timestamps_in_order(previous: Timestamp, current: Timestamp) -> bool:
    """Return whether two normalized timestamps are strictly increasing."""

    if isinstance(previous, datetime):
        return isinstance(current, datetime) and previous < current
    return isinstance(current, LogicalClock) and int(previous) < int(current)


def ordered_timestamps(value: object, path: str) -> tuple[Timestamp, ...]:
    """Normalize and validate a strictly increasing, comparable timestamp array."""

    timestamps = array_items(value, path)
    if not timestamps:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must not be empty")
    normalized = tuple(
        normalize_timestamp(timestamp, f"{path}[{index}]")
        for index, timestamp in enumerate(timestamps)
    )
    first_type = type(normalized[0])
    if any(type(timestamp) is not first_type for timestamp in normalized):
        raise EngineInputError(ErrorCode.SCHEMA, path, "must use one timestamp type")
    for index in range(1, len(normalized)):
        if not _timestamps_in_order(normalized[index - 1], normalized[index]):
            raise EngineInputError(ErrorCode.SCHEMA, path, "must be strictly increasing")
    return normalized


def availability_matrix(
    value: object,
    path: str,
    timestamps: Sequence[Timestamp],
    columns: int,
) -> tuple[tuple[Timestamp, ...], ...]:
    """Validate point-in-time availability for every panel observation."""

    matrix = array_items(value, path)
    if len(matrix) != len(timestamps):
        raise EngineInputError(
            ErrorCode.SCHEMA, path, f"must contain exactly {len(timestamps)} rows"
        )
    rows: list[tuple[Timestamp, ...]] = []
    expected_type = type(timestamps[0])
    for row_index, row in enumerate(matrix):
        items = array_items(row, f"{path}[{row_index}]")
        if len(items) != columns:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                f"{path}[{row_index}]",
                f"must contain exactly {columns} values",
            )
        normalized_row: list[Timestamp] = []
        for column_index, timestamp in enumerate(items):
            item_path = f"{path}[{row_index}][{column_index}]"
            normalized_timestamp = normalize_timestamp(timestamp, item_path)
            if type(normalized_timestamp) is not expected_type:
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    item_path,
                    "must use the timestamps array type",
                )
            if _timestamps_in_order(timestamps[row_index], normalized_timestamp):
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    item_path,
                    "is not available at its observation timestamp",
                )
            normalized_row.append(normalized_timestamp)
        rows.append(tuple(normalized_row))
    return tuple(rows)


def require_positive_int(value: object, path: str) -> int:
    """Require one positive built-in integer, excluding bool coercions."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be a positive integer")
    return value


def require_policy_rates(data: Mapping[str, Any], names: Sequence[str]) -> dict[str, Decimal]:
    """Read explicit Decimal execution-cost rates without defaults or coercion."""

    policy = require_mapping(
        require_field(data, "policy", ErrorCode.POLICY), "policy", ErrorCode.POLICY
    )
    rates: dict[str, Decimal] = {}
    for name in names:
        if name not in policy:
            raise EngineInputError(ErrorCode.POLICY, f"policy.{name}", "is required")
        value = policy[name]
        if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
            raise EngineInputError(
                ErrorCode.POLICY,
                f"policy.{name}",
                "must be a finite, non-negative Decimal",
            )
        rates[name] = value
    return rates


def cost_components(
    turnover: Decimal, rates: Mapping[str, Decimal]
) -> tuple[Decimal, Decimal, Decimal]:
    """Return commission, linear slippage, and quadratic market-impact costs."""

    commission = turnover * rates["commission_rate"]
    slippage = turnover * rates["slippage_rate"]
    impact = turnover * turnover * rates["impact_rate"]
    return commission, slippage, impact


def metric_path(prefix: str, values: Sequence[Decimal]) -> tuple[tuple[str, Decimal], ...]:
    """Expose a time-ordered Decimal path through stable metric names."""

    return tuple((f"{prefix}_{index:06d}", value) for index, value in enumerate(values))


def max_drawdown(equity: Sequence[Decimal]) -> Decimal:
    """Return peak-to-trough drawdown for a non-empty positive equity path."""

    if not equity:
        raise EngineInputError(ErrorCode.VALIDATION, "accounting.equity", "must not be empty")
    peak = equity[0]
    drawdown = _ZERO
    for value in equity:
        if value <= _ZERO:
            raise EngineInputError(
                ErrorCode.VALIDATION,
                "accounting.equity",
                "must remain positive after every period",
            )
        if value > peak:
            peak = value
        candidate = (peak - value) / peak
        if candidate > drawdown:
            drawdown = candidate
    return drawdown
