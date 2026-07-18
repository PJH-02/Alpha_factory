"""Deterministic, capability-gated dispatch to isolated lab compilers."""

from __future__ import annotations

from alpha_foundry.domain.models import CapabilitySnapshot, FrozenModel, StrategySpec
from alpha_foundry.labs.registry import (
    DEFAULT_LAB_REGISTRY,
    LabRegistry,
    validate_compilation_capability,
)


class StrategyCompiler:
    """Compile one pinned single-domain strategy into an immutable data plan only."""

    def __init__(self, registry: LabRegistry | None = None) -> None:
        self._registry = registry if registry is not None else DEFAULT_LAB_REGISTRY

    def compile(self, strategy: StrategySpec, capability: CapabilitySnapshot) -> FrozenModel:
        """Validate ownership and resource pins before lazily dispatching one lab compiler."""

        lab = self._registry.get(strategy.domain)
        lab.validate_strategy(strategy)
        validate_compilation_capability(strategy, capability, lab)
        return lab.compile(strategy)


def compile_strategy(strategy: StrategySpec, capability: CapabilitySnapshot) -> FrozenModel:
    """Compile through the approved registry with no generic cross-domain DSL."""

    return StrategyCompiler().compile(strategy, capability)
