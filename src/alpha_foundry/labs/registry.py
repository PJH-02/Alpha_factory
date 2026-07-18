"""Lazy registry for the eight isolated Alpha Foundry labs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from importlib import import_module
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from alpha_foundry.domain import models as domain_models
from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstLiteral,
    AstOperator,
    AstParameter,
    CapabilitySnapshot,
    Domain,
    FrozenModel,
    StrategySpec,
)

_OPERATOR_SET_VERSION = "1.0.0"
_SCHEMA_VERSION = "1.0.0"
_FORBIDDEN_OPERATOR_IDS = frozenset({"eval", "exec", "import", "include", "python", "shell", "sql"})


@runtime_checkable
class Lab(Protocol):
    """The minimal domain-owned compiler surface exposed to application code."""

    @property
    def domain(self) -> Domain: ...

    @property
    def operator_set_version(self) -> str: ...

    @property
    def operator_set_hash(self) -> str: ...

    @property
    def schema_hash(self) -> str: ...

    @property
    def operator_ids(self) -> frozenset[str]: ...

    def validate_strategy(self, strategy: StrategySpec) -> None:
        """Require the strategy's one primary domain and typed AST envelope."""

    def compile(self, strategy: StrategySpec) -> FrozenModel:
        """Return only a validated, immutable data execution plan."""


@dataclass(frozen=True, slots=True)
class LazyLab:
    """One approved lab descriptor that imports its domain module only on compilation."""

    domain: Domain
    module_name: str
    compiler_name: str
    compiled_plan_name: str
    ast_type_name: str
    operator_set_version: str
    operator_set_hash: str
    schema_hash: str
    operator_ids: frozenset[str]

    def validate_strategy(self, strategy: StrategySpec) -> None:
        """Reject a foreign, unpinned, or malformed AST before compilation."""
        if strategy.domain is not self.domain:
            raise _domain_error(
                "strategy.domain",
                f"expected {self.domain.value}, got {strategy.domain.value}",
            )
        if strategy.ast.domain is not strategy.domain:
            raise _domain_error("strategy.ast.domain", "must equal strategy.domain")
        expected_ast_type = getattr(domain_models, self.ast_type_name)
        if type(strategy.ast) is not expected_ast_type:
            raise _domain_error(
                "strategy.ast",
                f"must use the {self.ast_type_name} envelope for {self.domain.value}",
            )
        _validate_strategy_identity(strategy, self)
        _validate_ast(strategy, self.operator_ids)

    def compile(self, strategy: StrategySpec) -> FrozenModel:
        """Lazily dispatch to the domain compiler after ownership validation."""

        self.validate_strategy(strategy)
        module = _load_lab_module(self.module_name)
        compiler = getattr(module, self.compiler_name, None)
        compiled_plan_type = getattr(module, self.compiled_plan_name, None)
        compile_strategy = getattr(compiler, "compile", None)
        if not isinstance(compiled_plan_type, type) or not callable(compile_strategy):
            raise _registry_error(
                "registry",
                f"{self.module_name} does not expose the approved compiler contract",
            )
        compiled_plan = compile_strategy(strategy)
        if not isinstance(compiled_plan, compiled_plan_type) or not isinstance(
            compiled_plan, FrozenModel
        ):
            raise _registry_error(
                "registry",
                f"{self.module_name} compiler returned a non-data execution plan",
            )
        return compiled_plan


def _load_lab_module(module_name: str) -> object:
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            raise _capability_error(
                "registry", f"approved lab module {module_name} is unavailable"
            ) from error
        raise


class LabRegistry:
    """A closed registry with exactly one lazy lab per approved primary domain."""

    def __init__(self, labs: Iterable[Lab]) -> None:
        lab_by_domain: dict[Domain, Lab] = {}
        for lab in labs:
            if lab.domain in lab_by_domain:
                raise ValueError(f"duplicate lab registration for {lab.domain.value}")
            lab_by_domain[lab.domain] = lab
        expected_domains = frozenset(Domain)
        registered_domains = frozenset(lab_by_domain)
        if registered_domains != expected_domains:
            missing = sorted(domain.value for domain in expected_domains - registered_domains)
            unexpected = sorted(domain.value for domain in registered_domains - expected_domains)
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected: {', '.join(unexpected)}")
            raise ValueError(
                f"registry must contain each approved domain exactly once ({'; '.join(details)})"
            )
        self._labs = MappingProxyType(lab_by_domain)

    @property
    def domains(self) -> tuple[Domain, ...]:
        """Return all approved domains in their canonical enum declaration order."""

        return tuple(Domain)

    def get(self, domain: Domain) -> Lab:
        """Return the lazily-loaded descriptor for one primary domain."""

        try:
            return self._labs[domain]
        except KeyError as error:
            domain_value = domain.value if isinstance(domain, Domain) else repr(domain)
            raise _domain_error("strategy.domain", f"unregistered domain {domain_value}") from error

    def compile(self, strategy: StrategySpec) -> FrozenModel:
        """Compile one single-domain strategy without importing unrelated labs."""

        lab = self.get(strategy.domain)
        _validate_strategy_identity(strategy, lab)
        return lab.compile(strategy)


def _lab(
    domain: Domain,
    module_name: str,
    compiler_name: str,
    compiled_plan_name: str,
    ast_type_name: str,
    operator_surface: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
) -> LazyLab:
    canonical_operator_surface = tuple(sorted(operator_surface))
    operator_ids = frozenset(operator_id for operator_id, _ in canonical_operator_surface)
    if len(operator_ids) != len(canonical_operator_surface):
        raise ValueError(f"duplicate operator registration for {domain.value}")
    operator_set_hash = digest(
        "alpha-foundry.lab-operator-set",
        {
            "ast_type": ast_type_name,
            "compiled_plan_type": compiled_plan_name,
            "compiler": compiler_name,
            "domain": domain.value,
            "operators": canonical_operator_surface,
            "version": _OPERATOR_SET_VERSION,
        },
    )
    schema_hash = digest(
        "alpha-foundry.lab-strategy-schema",
        {
            "ast_type": ast_type_name,
            "domain": domain.value,
            "operator_set_version": _OPERATOR_SET_VERSION,
            "schema_version": _SCHEMA_VERSION,
            "strategy_fields": (
                "strategy_id",
                "version",
                "domain",
                "operator_set_version",
                "operator_set_hash",
                "schema_hash",
                "ast",
            ),
        },
    )
    return LazyLab(
        domain=domain,
        module_name=module_name,
        compiler_name=compiler_name,
        compiled_plan_name=compiled_plan_name,
        ast_type_name=ast_type_name,
        operator_set_version=_OPERATOR_SET_VERSION,
        operator_set_hash=operator_set_hash,
        schema_hash=schema_hash,
        operator_ids=operator_ids,
    )


_APPROVED_LABS = (
    _lab(
        Domain.FACTOR,
        "alpha_foundry.labs.factor",
        "FactorStrategyCompiler",
        "CompiledFactorStrategy",
        "FactorStrategyAst",
        (("ranked_long_short", (("long_count", "int"), ("short_count", "int"))),),
    ),
    _lab(
        Domain.STAT_ARB,
        "alpha_foundry.labs.statarb",
        "StatArbStrategyCompiler",
        "CompiledStatArbStrategy",
        "StatArbStrategyAst",
        (
            (
                "lagged_zscore_spread",
                (("lookback", "int"), ("entry_z", "Decimal"), ("exit_z", "Decimal")),
            ),
        ),
    ),
    _lab(
        Domain.MARKET_MAKING,
        "alpha_foundry.labs.market_making",
        "MarketMakingStrategyCompiler",
        "CompiledMarketMakingStrategy",
        "MarketMakingStrategyAst",
        (
            (
                "inventory_limited_quote",
                (("quote_size", "Decimal"), ("inventory_skew", "Decimal")),
            ),
        ),
    ),
    _lab(
        Domain.STRUCTURAL_FLOW,
        "alpha_foundry.labs.structural_flow",
        "StructuralFlowStrategyCompiler",
        "CompiledStructuralFlowStrategy",
        "StructuralFlowStrategyAst",
        (("decayed_event_impact", (("position_scale", "Decimal"),)),),
    ),
    _lab(
        Domain.CROSS_VENUE,
        "alpha_foundry.labs.cross_venue",
        "_CrossVenueStrategyCompiler",
        "CompiledCrossVenueStrategy",
        "CrossVenueStrategyAst",
        (
            (
                "cross_venue_spread",
                (
                    ("route_id", "str"),
                    ("minimum_edge_rate", "Decimal"),
                    ("maximum_quantity", "Decimal"),
                ),
            ),
        ),
    ),
    _lab(
        Domain.DERIVATIVES,
        "alpha_foundry.labs.derivatives",
        "_DerivativesStrategyCompiler",
        "CompiledDerivativesStrategy",
        "DerivativesStrategyAst",
        (
            (
                "lagged_hedge",
                (("signal_scale", "Decimal"), ("underlying_hedge_ratio", "Decimal")),
            ),
        ),
    ),
    _lab(
        Domain.EVENT_FUNDAMENTAL,
        "alpha_foundry.labs.event_fundamental",
        "_EventFundamentalStrategyCompiler",
        "CompiledEventFundamentalStrategy",
        "EventFundamentalStrategyAst",
        (
            (
                "event_surprise",
                (
                    ("entry_lag_periods", "int"),
                    ("holding_periods", "int"),
                    ("signal_threshold", "Decimal"),
                    ("position_size", "Decimal"),
                    ("maximum_gross_exposure", "Decimal"),
                    ("allow_short", "bool"),
                ),
            ),
        ),
    ),
    _lab(
        Domain.TIME_SERIES,
        "alpha_foundry.labs.time_series",
        "_TimeSeriesStrategyCompiler",
        "CompiledTimeSeriesStrategy",
        "TimeSeriesStrategyAst",
        (
            (
                "rolling_momentum",
                (
                    ("lookback_periods", "int"),
                    ("position_lag_periods", "int"),
                    ("rebalance_periods", "int"),
                    ("signal_threshold", "Decimal"),
                    ("position_size", "Decimal"),
                    ("maximum_gross_exposure", "Decimal"),
                    ("allow_short", "bool"),
                ),
            ),
        ),
    ),
)

DEFAULT_LAB_REGISTRY = LabRegistry(_APPROVED_LABS)


def _validate_strategy_identity(strategy: StrategySpec, lab: Lab) -> None:
    if strategy.operator_set_version != lab.operator_set_version:
        raise _schema_error(
            "strategy.operator_set_version",
            f"expected {lab.operator_set_version}, got {strategy.operator_set_version}",
        )
    if strategy.operator_set_hash != lab.operator_set_hash:
        raise _schema_error(
            "strategy.operator_set_hash", "does not match the approved operator set"
        )
    if strategy.schema_hash != lab.schema_hash:
        raise _schema_error("strategy.schema_hash", "does not match the approved strategy schema")


def _validate_ast(strategy: StrategySpec, operator_ids: frozenset[str]) -> None:
    if strategy.model_extra:
        raise _schema_error("strategy", "unknown fields are not allowed")
    if strategy.ast.model_extra:
        raise _schema_error("strategy.ast", "unknown fields are not allowed")
    _validate_ast_node(strategy.ast.root, "strategy.ast.root", operator_ids)


def _validate_ast_node(node: object, path: str, operator_ids: frozenset[str]) -> None:
    if isinstance(node, AstLiteral):
        if node.model_extra:
            raise _schema_error(path, "unknown fields are not allowed")
        if node.kind != "literal" or not _is_ast_scalar(node.value):
            raise _schema_error(path, "must contain a scalar declarative literal")
        return
    if not isinstance(node, AstOperator):
        raise _schema_error(path, "must be a declarative AST literal or operator")
    if node.model_extra:
        raise _schema_error(path, "unknown fields are not allowed")
    if node.kind != "operator" or not isinstance(node.operator_id, str) or not node.operator_id:
        raise _schema_error(path, "must contain a declarative operator ID")
    if node.operator_id.casefold() in _FORBIDDEN_OPERATOR_IDS:
        raise _schema_error(f"{path}.operator_id", "code-like operators are forbidden")
    if node.operator_id not in operator_ids:
        raise _schema_error(f"{path}.operator_id", "operator is not registered")
    parameter_names: set[str] = set()
    for parameter_index, parameter in enumerate(node.parameters):
        parameter_path = f"{path}.parameters[{parameter_index}]"
        if (
            not isinstance(parameter, AstParameter)
            or parameter.model_extra
            or not isinstance(parameter.name, str)
            or not parameter.name
            or not _is_ast_scalar(parameter.value)
        ):
            raise _schema_error(parameter_path, "must contain a scalar declarative parameter")
        if parameter.name in parameter_names:
            raise _schema_error(f"{parameter_path}.name", "duplicate parameter name")
        parameter_names.add(parameter.name)
    for argument_index, argument in enumerate(node.arguments):
        _validate_ast_node(argument, f"{path}.arguments[{argument_index}]", operator_ids)


def _is_ast_scalar(value: object) -> bool:
    if isinstance(value, Decimal):
        return value.is_finite()
    return value is None or isinstance(value, bool | int | str)


def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )


def _domain_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.DOMAIN,
            "Strategy primary-domain validation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )


def _capability_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.CAPABILITY,
            "Required compiler capability is unavailable",
            (ErrorField(path=path, reason=reason),),
        )
    )


def _registry_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.STATE,
            "Lab registry is inconsistent",
            (ErrorField(path=path, reason=reason),),
        )
    )


def validate_compilation_capability(
    strategy: StrategySpec, capability: CapabilitySnapshot, lab: Lab
) -> None:
    """Require matching reviewed versions plus relevant data, policy, and schema resources."""

    _validate_strategy_identity(strategy, lab)
    if not any(dataset.domain is strategy.domain for dataset in capability.datasets):
        raise _capability_error(
            "capability.datasets",
            f"no {strategy.domain.value} dataset is available",
        )
    if not any(policy.domain is strategy.domain for policy in capability.execution_policies):
        raise _capability_error(
            "capability.execution_policies",
            f"no {strategy.domain.value} execution policy is available",
        )
    if strategy.schema_hash not in capability.schema_hashes:
        raise _capability_error(
            "capability.schema_hashes",
            "does not include the strategy schema hash",
        )
