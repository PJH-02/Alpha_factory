"""Immutable contracts for finite deterministic candidate search."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.canonical import canonical_bytes, digest
from alpha_foundry.domain.models import (
    Decision,
    Digest,
    Domain,
    FrozenModel,
    GateResult,
    Identifier,
    Metric,
    TypedStrategyAst,
)


def digest_bytes(value: str) -> bytes:
    """Return raw bytes only for a canonical lowercase SHA-256 display digest."""
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("digest must use the sha256:<64 lowercase hex> form")
    return bytes.fromhex(value[7:])


def _require_raw_sorted_unique(values: tuple[str, ...], field_name: str) -> None:
    raw_values = tuple(digest_bytes(value) for value in values)
    if raw_values != tuple(sorted(raw_values)):
        raise ValueError(f"{field_name} must be in raw digest-byte ascending order")
    if len(raw_values) != len(set(raw_values)):
        raise ValueError(f"{field_name} must not contain duplicate digests")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


class Candidate(FrozenModel):
    """A typed, single-domain candidate with its exhaustive canonical identity."""

    domain: Domain
    operator_set_hash: Digest
    strategy_ast: TypedStrategyAst

    @model_validator(mode="after")
    def _validate_primary_domain(self) -> Candidate:
        if self.strategy_ast.domain is not self.domain:
            raise ValueError("candidate domain must match its strategy AST domain")
        return self

    @property
    def candidate_hash(self) -> str:
        return digest(
            "AF:CANDIDATE:1",
            {
                "domain": self.domain.value,
                "operator_set_hash": digest_bytes(self.operator_set_hash),
                "strategy_ast": self.strategy_ast.model_dump(mode="python"),
            },
        )


class Universe(FrozenModel):
    """A versioned, finite candidate set ordered by raw candidate digest bytes."""

    domain: Domain
    operator_set_hash: Digest
    version: Identifier
    candidates: tuple[Candidate, ...]

    @model_validator(mode="after")
    def _validate_candidates(self) -> Universe:
        if any(candidate.domain is not self.domain for candidate in self.candidates):
            raise ValueError("universe candidates must all use the universe domain")
        if any(
            candidate.operator_set_hash != self.operator_set_hash for candidate in self.candidates
        ):
            raise ValueError("universe candidates must all use the universe operator_set_hash")
        _require_raw_sorted_unique(self.candidate_hashes, "universe candidate hashes")
        return self

    @property
    def candidate_hashes(self) -> tuple[str, ...]:
        return tuple(candidate.candidate_hash for candidate in self.candidates)

    @property
    def universe_hash(self) -> str:
        return digest(
            "AF:UNIVERSE:1",
            {
                "candidate_hashes": [digest_bytes(value) for value in self.candidate_hashes],
                "domain": self.domain.value,
                "operator_set_hash": digest_bytes(self.operator_set_hash),
                "version": self.version,
            },
        )

    def contains(self, candidate_hash: str) -> bool:
        return candidate_hash in self.candidate_hashes

    def candidate_for_hash(self, candidate_hash: str) -> Candidate:
        for candidate in self.candidates:
            if candidate.candidate_hash == candidate_hash:
                return candidate
        raise KeyError(candidate_hash)


CandidateUniverse = Universe


class SearchSpec(FrozenModel):
    """All direct immutable inputs to a deterministic finite search run."""

    domain: Domain
    seed: int
    initial_population_size: int = Field(ge=1)
    offspring_count: int = Field(ge=1)
    parent_pool_size: int = Field(ge=0)
    patience: int = Field(ge=1)
    operator_schedule: tuple[Identifier, ...] = Field(min_length=1)
    operator_set_hash: Digest
    universe_hash: Digest
    profile_hash: Digest
    dataset_hashes: tuple[Digest, ...]
    policy_hashes: tuple[Digest, ...]
    schema_hashes: tuple[Digest, ...]
    code_hash: Digest

    @model_validator(mode="after")
    def _validate_hash_ordering(self) -> SearchSpec:
        _require_raw_sorted_unique(self.dataset_hashes, "dataset_hashes")
        _require_raw_sorted_unique(self.policy_hashes, "policy_hashes")
        _require_raw_sorted_unique(self.schema_hashes, "schema_hashes")
        return self

    @property
    def search_spec_hash(self) -> str:
        return digest(
            "AF:SEARCH_SPEC:1",
            {
                "code_hash": digest_bytes(self.code_hash),
                "dataset_hashes": [digest_bytes(value) for value in self.dataset_hashes],
                "domain": self.domain.value,
                "initial_population_size": self.initial_population_size,
                "offspring_count": self.offspring_count,
                "operator_schedule": list(self.operator_schedule),
                "operator_set_hash": digest_bytes(self.operator_set_hash),
                "parent_pool_size": self.parent_pool_size,
                "patience": self.patience,
                "policy_hashes": [digest_bytes(value) for value in self.policy_hashes],
                "profile_hash": digest_bytes(self.profile_hash),
                "schema_hashes": [digest_bytes(value) for value in self.schema_hashes],
                "seed": self.seed,
                "universe_hash": digest_bytes(self.universe_hash),
            },
        )


class OperatorParameter(FrozenModel):
    """One finite, canonical-order parameter grid for a registered operator."""

    name: Identifier
    values: tuple[str | int | Decimal | bool | None, ...] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def _validate_values(
        cls, values: tuple[str | int | Decimal | bool | None, ...]
    ) -> tuple[str | int | Decimal | bool | None, ...]:
        if any(isinstance(value, float) for value in values):
            raise ValueError(
                "operator parameter grids must not contain binary floating-point values"
            )
        if any(isinstance(value, Decimal) and not value.is_finite() for value in values):
            raise ValueError("operator parameter grids must contain finite Decimals")
        encoded_values = tuple(canonical_bytes(value) for value in values)
        if encoded_values != tuple(sorted(encoded_values)):
            raise ValueError("operator parameter values must be in AF-CANON byte order")
        if len(encoded_values) != len(set(encoded_values)):
            raise ValueError("operator parameter values must be deduplicated by AF-CANON bytes")
        return values


class SearchOperator(FrozenModel):
    """Finite operator metadata pinned transitively by ``operator_set_hash``."""

    operator_id: Identifier
    arity: int = Field(ge=0)
    commutative: bool
    parameters: tuple[OperatorParameter, ...]

    @model_validator(mode="after")
    def _validate_parameters(self) -> SearchOperator:
        if self.commutative and self.arity < 2:
            raise ValueError("only multi-parent operators may be commutative")
        names = tuple(parameter.name for parameter in self.parameters)
        if any(not name.isascii() for name in names):
            raise ValueError("operator parameter names must be ASCII")
        if names != tuple(sorted(names, key=lambda value: value.encode("ascii"))):
            raise ValueError("operator parameter names must use ASCII byte order")
        _require_unique(names, "operator parameter names")
        return self

    @property
    def semantic_hash(self) -> str:
        """Return the AF-CANON digest of every operator semantic field modeled here."""
        return digest(
            "AF:SEARCH_OPERATOR:1",
            {
                "arity": self.arity,
                "commutative": self.commutative,
                "operator_id": self.operator_id,
                "parameters": [
                    {"name": parameter.name, "values": list(parameter.values)}
                    for parameter in self.parameters
                ],
            },
        )


def operator_set_hash(operators: Mapping[str, SearchOperator]) -> str:
    """Return the canonical identity of a complete registered operator set.

    The current public operator contract models only IDs, arity, commutativity,
    and finite parameter grids.  Callers must bind every registered operator and
    cannot substitute a mapping key for an operator's declared identity.
    """
    entries: list[SearchOperator] = []
    for operator_id, operator in operators.items():
        if not isinstance(operator, SearchOperator):
            raise ValueError("operator set entries must be SearchOperator instances")
        if operator_id != operator.operator_id:
            raise ValueError("operator mapping keys must match declared operator IDs")
        entries.append(operator)
    if len({operator.operator_id for operator in entries}) != len(entries):
        raise ValueError("operator set must not contain duplicate operator IDs")
    return digest(
        "AF:OPERATOR_SET:1",
        {
            "operators": [
                {
                    "operator_hash": digest_bytes(operator.semantic_hash),
                    "operator_id": operator.operator_id,
                }
                for operator in sorted(
                    entries, key=lambda operator: operator.operator_id.encode("utf-8")
                )
            ]
        },
    )


class Traversal(FrozenModel):
    """The exhaustive digest for a candidate's seed-dependent traversal position."""

    candidate_hash: Digest
    seed: int

    @property
    def traversal_hash(self) -> str:
        return digest(
            "AF:TRAVERSAL:1",
            {"candidate_hash": digest_bytes(self.candidate_hash), "seed": self.seed},
        )

    @property
    def traversal_digest(self) -> str:
        return self.traversal_hash


class ParentAlternative(FrozenModel):
    """A legal frozen-parent tuple for a single offspring slot."""

    generation_index: int = Field(ge=1)
    operator_id: Identifier
    parent_hashes: tuple[Digest, ...]
    schedule_offset: int = Field(ge=0)
    seed: int
    slot_index: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_distinct_parents(self) -> ParentAlternative:
        _require_unique(self.parent_hashes, "parent_hashes")
        return self

    @property
    def parent_hash(self) -> str:
        return digest(
            "AF:PARENT:1",
            {
                "generation_index": self.generation_index,
                "operator_id": self.operator_id,
                "parent_hashes": [digest_bytes(value) for value in self.parent_hashes],
                "schedule_offset": self.schedule_offset,
                "seed": self.seed,
                "slot_index": self.slot_index,
            },
        )

    @property
    def parent_digest(self) -> str:
        return self.parent_hash


class ParameterAlternative(FrozenModel):
    """One finite operator-grid point for a single offspring slot."""

    generation_index: int = Field(ge=1)
    operator_id: Identifier
    parameter_indices: tuple[int, ...]
    schedule_offset: int = Field(ge=0)
    seed: int
    slot_index: int = Field(ge=0)

    @field_validator("parameter_indices")
    @classmethod
    def _validate_parameter_indices(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value < 0 for value in values):
            raise ValueError("parameter indices must be non-negative")
        return values

    @property
    def parameter_hash(self) -> str:
        return digest(
            "AF:PARAMETER:1",
            {
                "generation_index": self.generation_index,
                "operator_id": self.operator_id,
                "parameter_indices": list(self.parameter_indices),
                "schedule_offset": self.schedule_offset,
                "seed": self.seed,
                "slot_index": self.slot_index,
            },
        )

    @property
    def parameter_digest(self) -> str:
        return self.parameter_hash


class ParentPool(FrozenModel):
    """An immutable ranked parent snapshot created before an offspring generation."""

    generation_index: int = Field(ge=1)
    parent_candidate_hashes: tuple[Digest, ...]
    profile_hash: Digest
    search_spec_hash: Digest

    @model_validator(mode="after")
    def _validate_candidates(self) -> ParentPool:
        _require_unique(self.parent_candidate_hashes, "parent_candidate_hashes")
        return self

    @property
    def parent_pool_hash(self) -> str:
        return digest(
            "AF:PARENT_POOL:1",
            {
                "generation_index": self.generation_index,
                "parent_candidate_hashes": [
                    digest_bytes(value) for value in self.parent_candidate_hashes
                ],
                "profile_hash": digest_bytes(self.profile_hash),
                "search_spec_hash": digest_bytes(self.search_spec_hash),
            },
        )

    @property
    def parent_pool_digest(self) -> str:
        return self.parent_pool_hash


class CandidateTrialTerminal(StrEnum):
    EVALUATED = "EVALUATED"
    REJECTED_PREFLIGHT = "REJECTED_PREFLIGHT"
    REJECTED_HARD_GATE = "REJECTED_HARD_GATE"
    ENGINE_FAILED = "ENGINE_FAILED"
    INTERRUPTED = "INTERRUPTED"


class TrialEvaluation(FrozenModel):
    """The terminal result returned by a deterministic candidate evaluator."""

    terminal: CandidateTrialTerminal
    metrics: tuple[Metric, ...] = ()
    gate_results: tuple[GateResult, ...] = ()
    complete: bool
    error_code: Identifier | None

    @model_validator(mode="after")
    def _validate_terminal_evidence(self) -> TrialEvaluation:
        _require_unique(tuple(metric.name for metric in self.metrics), "evaluation metric names")
        _require_unique(tuple(result.gate_id for result in self.gate_results), "gate result ids")
        if self.terminal is CandidateTrialTerminal.EVALUATED:
            if self.error_code is not None:
                raise ValueError("evaluated trials must not carry an error code")
            if not self.gate_results:
                raise ValueError("evaluated trials require hard-gate evidence")
            if any(result.decision is not Decision.PASS for result in self.gate_results):
                raise ValueError("evaluated trials cannot contain failed hard-gate evidence")
            return self
        if self.metrics:
            raise ValueError("non-evaluated terminals must not invent score metrics")
        if self.error_code is None:
            raise ValueError("non-evaluated trials require a typed error code")
        if self.terminal is CandidateTrialTerminal.REJECTED_HARD_GATE:
            if not self.gate_results or not any(
                result.decision is Decision.FAIL for result in self.gate_results
            ):
                raise ValueError("hard-gate-rejected trials require failed hard-gate evidence")
            return self
        if self.complete:
            raise ValueError("only evaluated or hard-gate-rejected trials can be complete")
        if self.gate_results:
            raise ValueError("this terminal must not carry hard-gate evidence")
        return self


class ProposalEventKind(StrEnum):
    INVALID = "INVALID"
    OUTSIDE_UNIVERSE = "OUTSIDE_UNIVERSE"
    DUPLICATE = "DUPLICATE"
    DIRECT_FALLBACK = "DIRECT_FALLBACK"
    SLOT_EXHAUSTED = "SLOT_EXHAUSTED"


class TrialEventKind(StrEnum):
    TRIAL_STARTED = "TRIAL_STARTED"
    EVALUATED = CandidateTrialTerminal.EVALUATED.value
    REJECTED_PREFLIGHT = CandidateTrialTerminal.REJECTED_PREFLIGHT.value
    REJECTED_HARD_GATE = CandidateTrialTerminal.REJECTED_HARD_GATE.value
    ENGINE_FAILED = CandidateTrialTerminal.ENGINE_FAILED.value
    INTERRUPTED = CandidateTrialTerminal.INTERRUPTED.value


class ProposalSource(StrEnum):
    INITIAL = "INITIAL"
    OPERATOR = "OPERATOR"
    DIRECT_FALLBACK = "DIRECT_FALLBACK"


class ProposalEvent(FrozenModel):
    """An append-only record for one consumed rejected alternative or slot outcome."""

    event_type: Literal["proposal"] = "proposal"
    sequence: int = Field(ge=0)
    generation_index: int = Field(ge=0)
    slot_index: int = Field(ge=0)
    kind: ProposalEventKind
    candidate_hash: Digest | None
    operator_id: Identifier | None
    parent_hash: Digest | None
    parameter_hash: Digest | None
    reason_code: Identifier | None


class Proposal(FrozenModel):
    """The accepted candidate relation that precedes exactly one started trial."""

    candidate_hash: Digest
    generation_index: int = Field(ge=0)
    slot_index: int = Field(ge=0)
    source: ProposalSource
    operator_id: Identifier | None
    parent_hash: Digest | None
    parameter_hash: Digest | None


class CandidateTrial(FrozenModel):
    """A write-ahead trial record with exactly one terminal after evaluation."""

    trial_id: Identifier
    ledger_position: int = Field(ge=0)
    candidate_hash: Digest
    generation_index: int = Field(ge=0)
    slot_index: int = Field(ge=0)
    operator_id: Identifier | None
    parent_hashes: tuple[Digest, ...]
    parameter_indices: tuple[int, ...]

    @field_validator("parameter_indices")
    @classmethod
    def _validate_trial_parameter_indices(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(value < 0 for value in values):
            raise ValueError("trial parameter indices must be non-negative")
        return values

    terminal: CandidateTrialTerminal | None
    evaluation: TrialEvaluation | None

    @model_validator(mode="after")
    def _validate_terminal_pair(self) -> CandidateTrial:
        if (self.terminal is None) != (self.evaluation is None):
            raise ValueError("trial terminal and evaluation must be recorded together")
        if self.evaluation is not None and self.evaluation.terminal is not self.terminal:
            raise ValueError("trial terminal must match the evaluation terminal")
        return self


class TrialEvent(FrozenModel):
    """An append-only start or terminal event for a candidate trial."""

    event_type: Literal["trial"] = "trial"
    sequence: int = Field(ge=0)
    trial_id: Identifier
    ledger_position: int = Field(ge=0)
    kind: TrialEventKind
    error_code: Identifier | None


class OperatorInvocation(FrozenModel):
    """The complete deterministic input passed to an operator callback."""

    operator: SearchOperator
    parent_alternative: ParentAlternative
    parameter_alternative: ParameterAlternative
    parents: tuple[Candidate, ...]
    parameter_values: tuple[str | int | Decimal | bool | None, ...]


class OperatorProposal(FrozenModel):
    """A deterministic operator callback result, either typed candidate or rejection."""

    candidate: Candidate | None = None
    invalid_reason: Identifier | None = None

    @model_validator(mode="after")
    def _validate_result(self) -> OperatorProposal:
        if (self.candidate is None) == (self.invalid_reason is None):
            raise ValueError(
                "operator proposal must contain exactly one candidate or invalid_reason"
            )
        return self


type SearchEvent = Annotated[ProposalEvent | TrialEvent, Field(discriminator="event_type")]


class SearchStopReason(StrEnum):
    PLATEAU = "PLATEAU"
    UNIVERSE_EXHAUSTED = "UNIVERSE_EXHAUSTED"


class SearchFailureReason(StrEnum):
    FAILED_INVARIANT = "FAILED_INVARIANT"
    OPERATOR_FAILED = "OPERATOR_FAILED"
    EVALUATOR_FAILED = "EVALUATOR_FAILED"
    INTERRUPTED = "INTERRUPTED"


class SearchRunResult(FrozenModel):
    """The full deterministic lineage and terminal state of an in-memory search run."""

    search_spec_hash: Digest
    universe_hash: Digest
    profile_hash: Digest
    stop_reason: SearchStopReason | None
    failure_reason: SearchFailureReason | None
    best_candidate_hash: Digest | None
    plateau_counter: int = Field(ge=0)
    generation_count: int = Field(ge=1)
    parent_pools: tuple[ParentPool, ...]
    proposals: tuple[Proposal, ...]
    proposed_candidate_hashes: tuple[Digest, ...]
    visited_candidate_hashes: tuple[Digest, ...]
    events: tuple[SearchEvent, ...]
    trials: tuple[CandidateTrial, ...]

    @model_validator(mode="after")
    def _validate_terminal_lineage(self) -> SearchRunResult:
        if (self.stop_reason is None) == (self.failure_reason is None):
            raise ValueError("a search run must have exactly one normal stop or typed failure")
        expected_events = tuple(range(len(self.events)))
        if tuple(event.sequence for event in self.events) != expected_events:
            raise ValueError("lineage event sequences must be contiguous from zero")
        expected_positions = tuple(range(len(self.trials)))
        if tuple(trial.ledger_position for trial in self.trials) != expected_positions:
            raise ValueError("trial ledger positions must be contiguous from zero")
        if any(trial.terminal is None for trial in self.trials):
            raise ValueError("completed search results cannot contain dangling started trials")
        _require_unique(self.proposed_candidate_hashes, "proposed_candidate_hashes")
        _require_unique(self.visited_candidate_hashes, "visited_candidate_hashes")
        if self.proposed_candidate_hashes != self.visited_candidate_hashes:
            raise ValueError("proposed and visited candidate lineage must match exactly")
        if (
            tuple(proposal.candidate_hash for proposal in self.proposals)
            != self.proposed_candidate_hashes
        ):
            raise ValueError("accepted proposals must preserve proposed candidate order")
        if len(self.proposals) != len(self.trials):
            raise ValueError("each accepted proposal must create exactly one trial")
        if tuple(trial.candidate_hash for trial in self.trials) != self.proposed_candidate_hashes:
            raise ValueError("started trials must preserve proposed candidate order")
        if (
            self.best_candidate_hash is not None
            and self.best_candidate_hash not in self.visited_candidate_hashes
        ):
            raise ValueError("best candidate must be a visited candidate")
        expected_pool_generations = tuple(range(1, len(self.parent_pools) + 1))
        if tuple(pool.generation_index for pool in self.parent_pools) != expected_pool_generations:
            raise ValueError("parent pools must be immutable consecutive generation snapshots")
        if any(
            pool.profile_hash != self.profile_hash or pool.search_spec_hash != self.search_spec_hash
            for pool in self.parent_pools
        ):
            raise ValueError("parent pools must pin this result's frozen profile and SearchSpec")
        trial_events = tuple(event for event in self.events if isinstance(event, TrialEvent))
        if len(trial_events) != 2 * len(self.trials):
            raise ValueError("each trial must have exactly one start and one terminal event")
        engine_failed = any(
            trial.terminal is CandidateTrialTerminal.ENGINE_FAILED for trial in self.trials
        )
        if engine_failed and self.failure_reason is not SearchFailureReason.EVALUATOR_FAILED:
            raise ValueError("engine-failed trials require an evaluator failure result")
        for trial in self.trials:
            terminal = trial.terminal
            if terminal is None:
                raise ValueError("completed search results cannot contain dangling started trials")
            matching = tuple(event for event in trial_events if event.trial_id == trial.trial_id)
            if tuple(event.kind for event in matching) != (
                TrialEventKind.TRIAL_STARTED,
                TrialEventKind(terminal.value),
            ):
                raise ValueError(
                    "trial events must contain one ordered start and matching terminal"
                )
        return self
