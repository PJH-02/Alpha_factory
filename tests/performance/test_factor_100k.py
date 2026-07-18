"""Release performance evidence for the deterministic Factor panel engine."""

from __future__ import annotations

from decimal import Decimal
from time import perf_counter

import pytest

from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentStatus,
    FactorStrategyAst,
    StrategySpec,
)
from alpha_foundry.engines.factor import FactorEngine

pytestmark = pytest.mark.release

_HASH = "sha256:" + "0" * 64
_ASSET_COUNT = 1_000
_PERIOD_COUNT = 100
_MAX_RUNTIME_SECONDS = 60.0


def _config() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="factor-100k-release",
        strategy=StrategySpec(
            strategy_id="factor-100k-strategy",
            version="v1",
            domain=Domain.FACTOR,
            operator_set_version="v1",
            operator_set_hash=_HASH,
            schema_hash=_HASH,
            ast=FactorStrategyAst(
                root=AstOperator(
                    kind="operator",
                    operator_id="ranked_long_short",
                    parameters=(
                        AstParameter(name="long_count", value=50),
                        AstParameter(name="short_count", value=50),
                    ),
                )
            ),
        ),
        datasets=(
            DatasetRef(
                dataset_id="factor-100k-panel",
                domain=Domain.FACTOR,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="factor-100k-policy",
                domain=Domain.FACTOR,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=7,
    )


def _panel() -> dict[str, object]:
    timestamps = tuple(range(_PERIOD_COUNT))
    asset_ids = tuple(f"asset-{index:04d}" for index in range(_ASSET_COUNT))
    zero_returns = (0,) * _ASSET_COUNT
    return {
        "timestamps": timestamps,
        "asset_ids": asset_ids,
        "signals": tuple(
            tuple(
                (asset_index + period_index) % _ASSET_COUNT for asset_index in range(_ASSET_COUNT)
            )
            for period_index in timestamps
        ),
        "returns": tuple(zero_returns for _ in timestamps),
        "signal_available_at": tuple((period_index,) * _ASSET_COUNT for period_index in timestamps),
        "long_count": 50,
        "short_count": 50,
        "policy": {
            "commission_rate": Decimal("0"),
            "slippage_rate": Decimal("0"),
            "impact_rate": Decimal("0"),
        },
    }


def test_release_factor_engine_completes_a_deterministic_100k_row_panel_within_budget() -> None:
    config = _config()
    panel = _panel()

    started = perf_counter()
    result = FactorEngine().run(config, panel)
    elapsed_seconds = perf_counter() - started
    repeated = FactorEngine().run(config, panel)

    assert result.status is ExperimentStatus.SUCCEEDED
    assert result == repeated
    metrics = {metric.name: metric.value for metric in result.metrics}
    assert metrics["period_count"] == Decimal(_PERIOD_COUNT)
    assert metrics["asset_count"] == Decimal(_ASSET_COUNT)
    assert elapsed_seconds <= _MAX_RUNTIME_SECONDS
