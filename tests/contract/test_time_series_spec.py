from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    Domain,
    StrategySpec,
    TimeSeriesStrategyAst,
)
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY
from alpha_foundry.labs.time_series import (
    TimeSeriesExecutionPolicy,
    TimeSeriesQuestionPayload,
    TimeSeriesRunPayload,
)


def compile_time_series_strategy(strategy: StrategySpec) -> object:
    return DEFAULT_LAB_REGISTRY.get(Domain.TIME_SERIES).compile(strategy)


_START = datetime(2024, 1, 1, tzinfo=UTC)
_TIMESTAMPS = tuple(_START + timedelta(days=index) for index in range(4))


def _policy() -> TimeSeriesExecutionPolicy:
    return TimeSeriesExecutionPolicy(
        lookback_periods=2,
        position_lag_periods=1,
        rebalance_periods=2,
        signal_threshold=Decimal("0.05"),
        position_size=Decimal("0.5"),
        maximum_gross_exposure=Decimal("1"),
        allow_short=True,
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.002"),
        impact_rate=Decimal("0.003"),
    )


def _payload() -> dict[str, object]:
    return {
        "timestamps": _TIMESTAMPS,
        "prices": (Decimal("100"), Decimal("110"), Decimal("121"), Decimal("121")),
        "price_available_at": _TIMESTAMPS,
        "policy": _policy(),
    }


def _strategy(operator_id: str = "rolling_momentum") -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.TIME_SERIES)

    return StrategySpec(
        strategy_id="time-series-strategy",
        version="v1",
        domain=Domain.TIME_SERIES,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=TimeSeriesStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="lookback_periods", value=2),
                    AstParameter(name="position_lag_periods", value=1),
                    AstParameter(name="rebalance_periods", value=2),
                    AstParameter(name="signal_threshold", value=Decimal("0.05")),
                    AstParameter(name="position_size", value=Decimal("0.5")),
                    AstParameter(name="maximum_gross_exposure", value=Decimal("1")),
                    AstParameter(name="allow_short", value=True),
                ),
            )
        ),
    )


def test_ct_time_series_001_payloads_are_strict_and_preserve_price_availability() -> None:
    payload = TimeSeriesRunPayload(domain=Domain.TIME_SERIES, **_payload())

    assert payload.domain is Domain.TIME_SERIES
    assert payload.price_available_at == _TIMESTAMPS
    assert payload.policy.position_lag_periods == 1

    with pytest.raises(ValidationError) as generated_code:
        TimeSeriesRunPayload(
            domain=Domain.TIME_SERIES,
            **_payload(),
            python_code="raise RuntimeError('must not run')",
        )
    assert generated_code.value.errors()[0]["loc"] == ("python_code",)
    assert generated_code.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as wrong_domain:
        TimeSeriesQuestionPayload(
            domain=Domain.EVENT_FUNDAMENTAL,
            research_id="research-1",
            instrument_id="instrument-1",
            sampling_interval="1d",
        )
    assert wrong_domain.value.errors()[0]["loc"] == ("domain",)
    assert wrong_domain.value.errors()[0]["type"] == "literal_error"


def test_ct_time_series_002_compiler_accepts_only_explicit_allowlisted_data_plan() -> None:
    plan = compile_time_series_strategy(_strategy())

    assert plan.model_dump() == {
        "lookback_periods": 2,
        "position_lag_periods": 1,
        "rebalance_periods": 2,
        "signal_threshold": Decimal("0.05"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": Decimal("1"),
        "allow_short": True,
    }

    with pytest.raises(DomainError) as generated_operator:
        compile_time_series_strategy(_strategy(operator_id="python_exec"))
    assert generated_operator.value.detail.code is ErrorCode.SCHEMA
    assert generated_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"


@pytest.mark.parametrize(
    ("field", "path"),
    (
        ("operator_set_version", "strategy.operator_set_version"),
        ("operator_set_hash", "strategy.operator_set_hash"),
        ("schema_hash", "strategy.schema_hash"),
    ),
)
def test_ct_time_series_003_compiler_rejects_mismatched_lab_identity(field: str, path: str) -> None:
    strategy = _strategy()
    current_value = getattr(strategy, field)
    replacement = (
        f"{current_value}-unreviewed"
        if field == "operator_set_version"
        else current_value[:-1] + ("0" if current_value[-1] != "0" else "1")
    )

    with pytest.raises(DomainError) as mismatched_identity:
        compile_time_series_strategy(strategy.model_copy(update={field: replacement}))

    assert mismatched_identity.value.detail.code is ErrorCode.SCHEMA
    assert mismatched_identity.value.detail.details[0].path == path


def test_ct_time_series_004_compiler_rejects_duplicate_parameter_names() -> None:
    strategy = _strategy()
    root = strategy.ast.root
    assert isinstance(root, AstOperator)
    duplicate_strategy = strategy.model_copy(
        update={
            "ast": strategy.ast.model_copy(
                update={
                    "root": root.model_copy(
                        update={"parameters": (*root.parameters, root.parameters[0])}
                    )
                }
            )
        }
    )

    with pytest.raises(DomainError) as duplicate_parameter:
        compile_time_series_strategy(duplicate_strategy)

    assert duplicate_parameter.value.detail.code is ErrorCode.SCHEMA
    assert (
        duplicate_parameter.value.detail.details[0].path == "strategy.ast.root.parameters[7].name"
    )
