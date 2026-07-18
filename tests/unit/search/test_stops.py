from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import ErrorCode, ErrorDetail
from alpha_foundry.domain.models import (
    AstLiteral,
    Decision,
    Domain,
    FactorStrategyAst,
    GateComparison,
    GateResult,
    Metric,
    RankingDirection,
    RankingRule,
    StatArbStrategyAst,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.search.models import (
    Candidate,
    CandidateTrialTerminal,
    OperatorParameter,
    OperatorProposal,
    SearchFailureReason,
    SearchOperator,
    SearchSpec,
    SearchStopReason,
    TrialEvaluation,
    Universe,
    digest_bytes,
    operator_set_hash,
)
from alpha_foundry.search.runner import run_search


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _candidate(value: int, operator_identity: str) -> Candidate:
    return Candidate(
        domain=Domain.FACTOR,
        operator_set_hash=operator_identity,
        strategy_ast=FactorStrategyAst(root=AstLiteral(kind="literal", value=value)),
    )


def _universe(*values: int, operators: dict[str, SearchOperator]) -> Universe:
    operator_identity = operator_set_hash(operators)
    candidates = tuple(
        sorted(
            (_candidate(value, operator_identity) for value in values),
            key=lambda candidate: digest_bytes(candidate.candidate_hash),
        )
    )
    return Universe(
        domain=Domain.FACTOR,
        operator_set_hash=operator_identity,
        version="v1",
        candidates=candidates,
    )


def _profile(patience: int) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(20),
        hard_gates=(
            ValidationGate(
                gate_id="gate",
                metric_name="score",
                comparison=GateComparison.GTE,
                threshold=Decimal("0"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="score",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=("fold-0",),
        pbo_minimum_eligible=1,
        patience=patience,
        holdout_policy_hash=_digest(21),
        disclosure_policy_hash=_digest(22),
        allowed_disclosure_fields=(),
    )


def _search_spec(
    universe: Universe,
    profile: ValidationProfile,
    operators: dict[str, SearchOperator],
    *,
    initial_population_size: int = 1,
    offspring_count: int = 1,
    parent_pool_size: int = 1,
) -> SearchSpec:
    return SearchSpec(
        domain=Domain.FACTOR,
        seed=31,
        initial_population_size=initial_population_size,
        offspring_count=offspring_count,
        parent_pool_size=parent_pool_size,
        patience=profile.patience,
        operator_schedule=("op",),
        operator_set_hash=operator_set_hash(operators),
        universe_hash=universe.universe_hash,
        profile_hash=profile.profile_hash,
        dataset_hashes=(_digest(30),),
        policy_hashes=(_digest(31),),
        schema_hashes=(_digest(32),),
        code_hash=_digest(33),
    )


def _passed() -> TrialEvaluation:
    score = Decimal("1")
    return TrialEvaluation(
        terminal=CandidateTrialTerminal.EVALUATED,
        metrics=(Metric(name="score", value=score),),
        gate_results=(
            GateResult(
                gate_id="gate",
                decision=Decision.PASS,
                metric_name="score",
                threshold=Decimal("0"),
                comparison=GateComparison.GTE,
                observed_value=score,
            ),
        ),
        complete=True,
        error_code=None,
    )


def _rejected() -> TrialEvaluation:
    return TrialEvaluation(
        terminal=CandidateTrialTerminal.REJECTED_HARD_GATE,
        gate_results=(
            GateResult(
                gate_id="gate",
                decision=Decision.FAIL,
                metric_name="score",
                threshold=Decimal("0"),
                comparison=GateComparison.GTE,
                observed_value=Decimal("-1"),
                reason=ErrorDetail.for_code(ErrorCode.VALIDATION, "hard gate failed"),
            ),
        ),
        complete=False,
        error_code="AF-GATE",
    )


def _traversal(universe: Universe, seed: int) -> tuple[Candidate, ...]:
    from alpha_foundry.search.models import Traversal

    return tuple(
        sorted(
            universe.candidates,
            key=lambda candidate: (
                digest_bytes(
                    Traversal(candidate_hash=candidate.candidate_hash, seed=seed).traversal_hash
                ),
                digest_bytes(candidate.candidate_hash),
            ),
        )
    )


def _nullary_operator() -> SearchOperator:
    return SearchOperator(operator_id="op", arity=0, commutative=False, parameters=())


def _operator_set(operator: SearchOperator) -> dict[str, SearchOperator]:
    return {operator.operator_id: operator}


def test_initial_no_eligible_generation_starts_plateau_counter_and_later_stops() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile(patience=2)
    search_spec = _search_spec(universe, profile, operators)
    _, next_candidate, _ = _traversal(universe, search_spec.seed)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=next_candidate)},
        evaluate_candidate=lambda _: _rejected(),
    )

    assert result.stop_reason is SearchStopReason.PLATEAU
    assert result.best_candidate_hash is None
    assert result.plateau_counter == 2
    assert result.parent_pools[0].parent_candidate_hashes == ()
    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "REJECTED_HARD_GATE"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "REJECTED_HARD_GATE"),
    ]


def test_first_eligible_initializes_plateau_before_a_no_eligible_generation() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    initial, rejected_candidate, _ = _traversal(universe, search_spec.seed)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=rejected_candidate)},
        evaluate_candidate=lambda candidate: (
            _passed() if candidate.candidate_hash == initial.candidate_hash else _rejected()
        ),
    )

    assert result.stop_reason is SearchStopReason.PLATEAU
    assert result.best_candidate_hash == initial.candidate_hash
    assert result.plateau_counter == 1
    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "REJECTED_HARD_GATE"),
    ]


def test_universe_exhaustion_wins_when_plateau_would_also_trigger() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    _, remaining = _traversal(universe, search_spec.seed)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=remaining)},
        evaluate_candidate=lambda _: _passed(),
    )

    assert result.stop_reason is SearchStopReason.UNIVERSE_EXHAUSTED
    assert result.plateau_counter == 1
    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]


def test_same_seed_produces_identical_event_lineage() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    def run_once() -> object:
        return run_search(
            universe=universe,
            search_spec=search_spec,
            validation_profile=profile,
            operators=operators,
            operator_callbacks={"op": lambda _: OperatorProposal(invalid_reason="AF-INVALID")},
            evaluate_candidate=lambda _: _passed(),
        )

    first = run_once()
    second = run_once()

    assert first.events == second.events
    assert first.proposals == second.proposals
    assert [(event.event_type, event.kind.value) for event in first.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("proposal", "INVALID"),
        ("proposal", "DIRECT_FALLBACK"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]


def test_zero_initial_population_is_rejected_by_search_spec_validation() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, operators=operators)
    profile = _profile(patience=1)

    with pytest.raises(ValidationError, match="initial_population_size"):
        _search_spec(universe, profile, operators, initial_population_size=0)


def test_runner_rejects_operator_set_semantic_hash_mismatch() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    mismatched_operators = _operator_set(
        SearchOperator(operator_id="op", arity=1, commutative=False, parameters=())
    )

    with pytest.raises(
        ValueError, match="SearchSpec must pin the supplied operator-set semantic identity"
    ):
        run_search(
            universe=universe,
            search_spec=search_spec,
            validation_profile=profile,
            operators=mismatched_operators,
            operator_callbacks={"op": lambda _: OperatorProposal(invalid_reason="AF-UNUSED")},
            evaluate_candidate=lambda _: _passed(),
        )


def test_evaluator_exception_keeps_write_ahead_trial_lineage() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    def failing_evaluator(_: Candidate) -> TrialEvaluation:
        raise RuntimeError("evaluator failed")

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(invalid_reason="AF-UNUSED")},
        evaluate_candidate=failing_evaluator,
    )

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.EVALUATOR_FAILED
    assert [(event.event_type, event.kind.value, event.error_code) for event in result.events] == [
        ("trial", "TRIAL_STARTED", None),
        ("trial", "ENGINE_FAILED", "AF-ENGINE-CALLBACK"),
    ]
    assert result.proposed_candidate_hashes == universe.candidate_hashes
    assert result.trials[0].terminal is CandidateTrialTerminal.ENGINE_FAILED
    assert result.trials[0].evaluation is not None
    assert result.trials[0].evaluation.error_code == "AF-ENGINE-CALLBACK"


def test_operator_callback_exception_is_a_typed_non_normal_failure() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    def failing_callback(_: object) -> OperatorProposal:
        raise RuntimeError("operator failed")

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": failing_callback},
        evaluate_candidate=lambda _: _passed(),
    )

    proposal_events = [event for event in result.events if event.event_type == "proposal"]

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.OPERATOR_FAILED
    assert [
        (event.kind.value, event.candidate_hash, event.reason_code) for event in proposal_events
    ] == [("INVALID", None, "AF-OPERATOR-CALLBACK")]
    assert [(proposal.source.value, proposal.candidate_hash) for proposal in result.proposals] == [
        ("INITIAL", _traversal(universe, search_spec.seed)[0].candidate_hash),
    ]


def test_universe_exhaustion_stops_scheduling_remaining_slots() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators, offspring_count=2)
    _, remaining = _traversal(universe, search_spec.seed)
    callback_calls = 0

    def callback(_: object) -> OperatorProposal:
        nonlocal callback_calls
        callback_calls += 1
        return OperatorProposal(candidate=remaining)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": callback},
        evaluate_candidate=lambda _: _passed(),
    )

    assert [event for event in result.events if event.event_type == "proposal"] == []
    assert callback_calls == 1

    assert result.stop_reason is SearchStopReason.UNIVERSE_EXHAUSTED
    assert result.generation_count == 2
    assert [(trial.generation_index, trial.slot_index) for trial in result.trials] == [
        (0, 0),
        (1, 0),
    ]


def test_interruption_precedes_universe_exhaustion() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(invalid_reason="AF-UNUSED")},
        evaluate_candidate=lambda _: TrialEvaluation(
            terminal=CandidateTrialTerminal.INTERRUPTED,
            complete=False,
            error_code="AF-INTERRUPTED",
        ),
    )

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.INTERRUPTED
    assert [(event.event_type, event.kind.value, event.error_code) for event in result.events] == [
        ("trial", "TRIAL_STARTED", None),
        ("trial", "INTERRUPTED", "AF-INTERRUPTED"),
    ]


def test_nullary_operator_exhausts_its_sorted_parameter_grid_before_fallback() -> None:
    operator = SearchOperator(
        operator_id="op",
        arity=0,
        commutative=False,
        parameters=(OperatorParameter(name="choice", values=(1, 2)),),
    )
    operators = _operator_set(operator)
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    observed: list[tuple[object, tuple[str | int | Decimal | bool | None, ...]]] = []

    def reject_grid_point(invocation: object) -> OperatorProposal:
        parameter_alternative = invocation.parameter_alternative
        observed.append((parameter_alternative, invocation.parameter_values))
        return OperatorProposal(invalid_reason="AF-GRID-REJECTED")

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": reject_grid_point},
        evaluate_candidate=lambda _: _passed(),
    )

    alternatives = tuple(alternative for alternative, _ in observed)

    assert len(alternatives) == 2
    assert tuple(values for _, values in observed) in (((1,), (2,)), ((2,), (1,)))
    assert alternatives == tuple(
        sorted(
            alternatives,
            key=lambda alternative: (
                digest_bytes(alternative.parameter_hash),
                alternative.parameter_indices,
            ),
        )
    )
    assert [
        (event.kind.value, event.reason_code)
        for event in result.events
        if event.event_type == "proposal"
    ] == [
        ("INVALID", "AF-GRID-REJECTED"),
        ("INVALID", "AF-GRID-REJECTED"),
        ("DIRECT_FALLBACK", None),
    ]


@pytest.mark.parametrize(("commutative", "expected_invocations"), [(True, 1), (False, 2)])
def test_binary_operator_enumerates_only_legal_parent_arity(
    commutative: bool, expected_invocations: int
) -> None:
    operator = SearchOperator(
        operator_id="op",
        arity=2,
        commutative=commutative,
        parameters=(),
    )
    operators = _operator_set(operator)
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        initial_population_size=2,
        parent_pool_size=2,
    )
    initial_first, initial_second, _ = _traversal(universe, search_spec.seed)
    observed_parent_hashes: list[tuple[str, ...]] = []

    def reject_parent_tuple(invocation: object) -> OperatorProposal:
        observed_parent_hashes.append(invocation.parent_alternative.parent_hashes)
        return OperatorProposal(invalid_reason="AF-PARENTS-REJECTED")

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": reject_parent_tuple},
        evaluate_candidate=lambda _: _passed(),
    )

    canonical_parents = tuple(
        sorted(
            (initial_first.candidate_hash, initial_second.candidate_hash),
            key=digest_bytes,
        )
    )

    assert result.stop_reason is SearchStopReason.UNIVERSE_EXHAUSTED
    assert len(observed_parent_hashes) == expected_invocations
    if commutative:
        assert observed_parent_hashes == [canonical_parents]
    else:
        assert set(observed_parent_hashes) == {
            canonical_parents,
            tuple(reversed(canonical_parents)),
        }


@pytest.mark.parametrize(
    ("evaluator_result", "expected_error_code"),
    (
        (object(), "AF-ENGINE-CALLBACK"),
        (
            TrialEvaluation(
                terminal=CandidateTrialTerminal.ENGINE_FAILED,
                complete=False,
                error_code="AF-ENGINE-RETURNED",
            ),
            "AF-ENGINE-RETURNED",
        ),
    ),
)
def test_invalid_or_engine_failed_evaluator_result_is_a_typed_failure(
    evaluator_result: object, expected_error_code: str
) -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(invalid_reason="AF-UNUSED")},
        evaluate_candidate=lambda _: evaluator_result,
    )

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.EVALUATOR_FAILED
    assert [(event.event_type, event.kind.value, event.error_code) for event in result.events] == [
        ("trial", "TRIAL_STARTED", None),
        ("trial", "ENGINE_FAILED", expected_error_code),
    ]
    assert result.trials[0].terminal is CandidateTrialTerminal.ENGINE_FAILED
    assert result.trials[0].evaluation is not None
    assert result.trials[0].evaluation.error_code == expected_error_code


def test_non_proposal_operator_result_becomes_a_typed_operator_failure() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: object()},
        evaluate_candidate=lambda _: _passed(),
    )

    proposal_events = [event for event in result.events if event.event_type == "proposal"]

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.OPERATOR_FAILED
    assert [(event.kind.value, event.reason_code) for event in proposal_events] == [
        ("INVALID", "AF-OPERATOR-RESULT")
    ]
    assert len(result.trials) == 1


def test_runner_rejects_outside_operator_candidate_before_fallback() -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    candidate = _candidate(99, operator_set_hash(operators))

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=candidate)},
        evaluate_candidate=lambda _: _passed(),
    )

    proposal_events = [event for event in result.events if event.event_type == "proposal"]

    assert result.stop_reason is SearchStopReason.UNIVERSE_EXHAUSTED
    assert result.failure_reason is None
    assert [(event.kind.value, event.reason_code) for event in proposal_events] == [
        ("OUTSIDE_UNIVERSE", None),
        ("DIRECT_FALLBACK", None),
    ]


@pytest.mark.parametrize("identity", ("domain", "operator-set"))
def test_runner_rejects_wrong_operator_identity_as_typed_failure(identity: str) -> None:
    operators = _operator_set(_nullary_operator())
    universe = _universe(0, 1, operators=operators)
    profile = _profile(patience=1)
    search_spec = _search_spec(universe, profile, operators)
    candidate = (
        Candidate(
            domain=Domain.STAT_ARB,
            operator_set_hash=operator_set_hash(operators),
            strategy_ast=StatArbStrategyAst(root=AstLiteral(kind="literal", value=99)),
        )
        if identity == "domain"
        else _candidate(99, _digest(99))
    )

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=candidate)},
        evaluate_candidate=lambda _: _passed(),
    )

    proposal_events = [event for event in result.events if event.event_type == "proposal"]

    assert result.stop_reason is None
    assert result.failure_reason is SearchFailureReason.OPERATOR_FAILED
    assert [
        (event.kind.value, event.candidate_hash, event.reason_code) for event in proposal_events
    ] == [("INVALID", candidate.candidate_hash, "AF-OPERATOR-IDENTITY")]
    assert len(result.trials) == 1
