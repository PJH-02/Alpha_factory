from decimal import Decimal

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentStatus,
    FactorStrategyAst,
    StatArbStrategyAst,
    StrategySpec,
)
from alpha_foundry.engines.factor import FactorEngine
from alpha_foundry.engines.statarb import StatArbEngine

_HASH = "sha256:" + "2" * 64


def _config(domain: Domain) -> ExperimentConfig:
    if domain is Domain.FACTOR:
        ast = FactorStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="ranked_long_short",
                parameters=(
                    AstParameter(name="long_count", value=1),
                    AstParameter(name="short_count", value=1),
                ),
            )
        )
    else:
        ast = StatArbStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="lagged_zscore_spread",
                parameters=(
                    AstParameter(name="lookback", value=2),
                    AstParameter(name="entry_z", value=Decimal("2")),
                    AstParameter(name="exit_z", value=Decimal("0.5")),
                ),
            )
        )
    strategy = StrategySpec(
        strategy_id=f"{domain.value.lower()}-strategy",
        version="v1",
        domain=domain,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=ast,
    )
    return ExperimentConfig(
        experiment_id=f"{domain.value.lower()}-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id=f"{domain.value.lower()}-data",
                domain=domain,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id=f"{domain.value.lower()}-policy",
                domain=domain,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=13,
    )


def _factor_data() -> dict[str, object]:
    return {
        "timestamps": (0, 1),
        "asset_ids": ("A", "B"),
        "signals": ((Decimal("1"), Decimal("0")), (Decimal("0"), Decimal("1"))),
        "returns": ((Decimal("0"), Decimal("0")), (Decimal("0"), Decimal("0"))),
        "signal_available_at": ((0, 0), (1, 1)),
        "long_count": 1,
        "short_count": 1,
        "policy": {
            "commission_rate": Decimal("0"),
            "slippage_rate": Decimal("0"),
            "impact_rate": Decimal("0"),
        },
    }


def _statarb_data() -> dict[str, object]:
    return {
        "timestamps": (0, 1, 2, 3),
        "leg_ids": ("A", "B"),
        "prices": (
            (Decimal("100"), Decimal("100")),
            (Decimal("101"), Decimal("100")),
            (Decimal("103"), Decimal("100")),
            (Decimal("104"), Decimal("100")),
        ),
        "price_available_at": ((0, 0), (1, 1), (2, 2), (3, 3)),
        "hedge_weights": (Decimal("1"), Decimal("-1")),
        "lookback": 2,
        "entry_z": Decimal("2"),
        "exit_z": Decimal("0.5"),
        "policy": {
            "commission_rate": Decimal("0"),
            "slippage_rate": Decimal("0"),
            "impact_rate": Decimal("0"),
        },
    }


def test_sec_eng_002_factor_rejects_an_absent_policy_mapping() -> None:
    data = _factor_data()
    del data["policy"]

    result = FactorEngine().run(_config(Domain.FACTOR), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "policy"


def test_sec_eng_002_statarb_rejects_a_missing_required_cost_rate() -> None:
    data = _statarb_data()
    policy = data["policy"]
    assert isinstance(policy, dict)
    del policy["impact_rate"]

    result = StatArbEngine().run(_config(Domain.STAT_ARB), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "policy.impact_rate"


def test_sec_eng_002_statarb_rejects_coerced_string_cost_rates() -> None:
    data = _statarb_data()
    policy = data["policy"]
    assert isinstance(policy, dict)
    policy["commission_rate"] = "0.01"

    result = StatArbEngine().run(_config(Domain.STAT_ARB), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "policy.commission_rate"
