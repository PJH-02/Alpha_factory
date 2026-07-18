"""Finite deterministic search runner with complete in-memory trial lineage."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from itertools import combinations, permutations, product
from typing import Protocol

from alpha_foundry.domain.models import ValidationProfile

from .models import (
    Candidate,
    CandidateTrial,
    CandidateTrialTerminal,
    OperatorInvocation,
    OperatorProposal,
    ParameterAlternative,
    ParentAlternative,
    ParentPool,
    Proposal,
    ProposalEvent,
    ProposalEventKind,
    ProposalSource,
    SearchFailureReason,
    SearchOperator,
    SearchRunResult,
    SearchSpec,
    SearchStopReason,
    TrialEvaluation,
    TrialEvent,
    TrialEventKind,
    Universe,
    digest_bytes,
    operator_set_hash,
)
from .ranking import RankedTrial, compare_score_vectors, rank_trials
from .universe import first_unseen_candidate, traversal_order


class OperatorCallback(Protocol):
    """A deterministic mapping from a finite operator alternative to one proposal."""

    def __call__(self, invocation: OperatorInvocation) -> OperatorProposal: ...


class CandidateEvaluator(Protocol):
    """A deterministic evaluator that always returns one terminal trial outcome."""

    def __call__(self, candidate: Candidate) -> TrialEvaluation: ...


class SearchRunner:
    """Execute one finite SearchSpec without global randomness or live parent ranking."""

    def __init__(
        self,
        *,
        universe: Universe,
        search_spec: SearchSpec,
        validation_profile: ValidationProfile,
        operators: Mapping[str, SearchOperator],
        operator_callbacks: Mapping[str, OperatorCallback],
        evaluate_candidate: CandidateEvaluator,
    ) -> None:
        self._universe = universe
        self._search_spec = search_spec
        self._profile = validation_profile
        self._operators = dict(operators)
        self._operator_callbacks = dict(operator_callbacks)
        self._evaluate_candidate = evaluate_candidate
        self._validate_pinned_resources()

        self._events: list[ProposalEvent | TrialEvent] = []
        self._trials: list[CandidateTrial] = []
        self._proposals: list[Proposal] = []
        self._proposed: set[str] = set()
        self._visited: set[str] = set()
        self._proposed_order: list[str] = []
        self._visited_order: list[str] = []
        self._parent_pools: list[ParentPool] = []
        self._event_sequence = 0
        self._best_scores: tuple[Decimal, ...] | None = None
        self._best_candidate_hash: str | None = None
        self._interrupted = False
        self._plateau_counter = 0
        self._failure_reason: SearchFailureReason | None = None
        self._has_run = False

    def run(self) -> SearchRunResult:
        """Run generation zero and finite offspring generations to one terminal outcome."""
        if self._has_run:
            raise RuntimeError("a SearchRunner instance owns exactly one run-scoped lineage")
        self._has_run = True

        initial_trials: list[CandidateTrial] = []
        for slot_index, candidate in enumerate(
            traversal_order(self._universe, self._search_spec.seed)[
                : self._search_spec.initial_population_size
            ]
        ):
            initial_trials.append(
                self._accept_candidate(
                    candidate=candidate,
                    generation_index=0,
                    slot_index=slot_index,
                    source=ProposalSource.INITIAL,
                    operator_id=None,
                    parent_alternative=None,
                    parameter_alternative=None,
                )
            )
            if self._failure_reason is not None:
                return self._result(
                    generation_count=1,
                    stop_reason=None,
                    failure_reason=self._failure_reason,
                )
            if self._interrupted:
                return self._result(
                    generation_count=1,
                    stop_reason=None,
                    failure_reason=SearchFailureReason.INTERRUPTED,
                )
        normal_stop, failure_reason = self._complete_generation(initial_trials)
        if normal_stop is not None or failure_reason is not None:
            return self._result(
                generation_count=1,
                stop_reason=normal_stop,
                failure_reason=failure_reason,
            )

        generation_index = 1
        while len(self._visited) < len(self._universe.candidates):
            parent_pool = self._freeze_parent_pool(generation_index)
            self._parent_pools.append(parent_pool)
            generation_trials: list[CandidateTrial] = []
            for slot_index in range(self._search_spec.offspring_count):
                trial = self._run_slot(generation_index, slot_index, parent_pool)
                if trial is not None:
                    generation_trials.append(trial)
                if self._failure_reason is not None:
                    return self._result(
                        generation_count=generation_index + 1,
                        stop_reason=None,
                        failure_reason=self._failure_reason,
                    )
                if self._interrupted:
                    return self._result(
                        generation_count=generation_index + 1,
                        stop_reason=None,
                        failure_reason=SearchFailureReason.INTERRUPTED,
                    )
                if len(self._visited) == len(self._universe.candidates):
                    break
            normal_stop, failure_reason = self._complete_generation(generation_trials)
            if normal_stop is not None or failure_reason is not None:
                return self._result(
                    generation_count=generation_index + 1,
                    stop_reason=normal_stop,
                    failure_reason=failure_reason,
                )
            generation_index += 1

        return self._result(
            generation_count=generation_index,
            stop_reason=SearchStopReason.UNIVERSE_EXHAUSTED,
            failure_reason=None,
        )

    def _validate_pinned_resources(self) -> None:
        if self._universe.domain is not self._search_spec.domain:
            raise ValueError("universe and SearchSpec domains must match")
        if self._universe.operator_set_hash != self._search_spec.operator_set_hash:
            raise ValueError("universe and SearchSpec operator_set_hash values must match")
        try:
            computed_operator_set_hash = operator_set_hash(self._operators)
        except ValueError as error:
            raise ValueError(f"invalid registered operator set: {error}") from error
        if computed_operator_set_hash != self._search_spec.operator_set_hash:
            raise ValueError("SearchSpec must pin the supplied operator-set semantic identity")
        if self._universe.operator_set_hash != computed_operator_set_hash:
            raise ValueError("universe must pin the supplied operator-set semantic identity")
        if set(self._operator_callbacks) != set(self._operators):
            raise ValueError("operator callbacks must exactly match the registered operator set")
        if self._universe.universe_hash != self._search_spec.universe_hash:
            raise ValueError("SearchSpec must pin the supplied universe hash")
        if self._profile.domain is not self._search_spec.domain:
            raise ValueError("validation profile and SearchSpec domains must match")
        if self._profile.profile_hash != self._search_spec.profile_hash:
            raise ValueError("SearchSpec must pin the supplied validation profile hash")
        if self._profile.patience != self._search_spec.patience:
            raise ValueError("SearchSpec patience must match the frozen validation profile")
        for operator_id in self._search_spec.operator_schedule:
            if operator_id not in self._operators:
                raise ValueError(
                    f"operator schedule references unregistered operator: {operator_id}"
                )
            if operator_id not in self._operator_callbacks:
                raise ValueError(f"operator schedule has no deterministic callback: {operator_id}")

    def _freeze_parent_pool(self, generation_index: int) -> ParentPool:
        ranked = rank_trials(self._profile, self._trials)
        retained = ranked[: self._search_spec.parent_pool_size]
        return ParentPool(
            generation_index=generation_index,
            parent_candidate_hashes=tuple(item.trial.candidate_hash for item in retained),
            profile_hash=self._search_spec.profile_hash,
            search_spec_hash=self._search_spec.search_spec_hash,
        )

    def _run_slot(
        self, generation_index: int, slot_index: int, parent_pool: ParentPool
    ) -> CandidateTrial | None:
        schedule_length = len(self._search_spec.operator_schedule)
        for schedule_offset in range(schedule_length):
            operator_id = self._search_spec.operator_schedule[
                (
                    generation_index * self._search_spec.offspring_count
                    + slot_index
                    + schedule_offset
                )
                % schedule_length
            ]
            operator = self._operators[operator_id]
            callback = self._operator_callbacks[operator_id]
            for parent_alternative in self._parent_alternatives(
                generation_index, slot_index, schedule_offset, operator, parent_pool
            ):
                for parameter_alternative in self._parameter_alternatives(
                    generation_index, slot_index, schedule_offset, operator
                ):
                    parents = tuple(
                        self._universe.candidate_for_hash(candidate_hash)
                        for candidate_hash in parent_alternative.parent_hashes
                    )
                    parameter_values = tuple(
                        parameter.values[index]
                        for parameter, index in zip(
                            operator.parameters,
                            parameter_alternative.parameter_indices,
                            strict=True,
                        )
                    )
                    invocation = OperatorInvocation(
                        operator=operator,
                        parent_alternative=parent_alternative,
                        parameter_alternative=parameter_alternative,
                        parents=parents,
                        parameter_values=parameter_values,
                    )
                    proposal: OperatorProposal | None = None
                    failure_code: str | None = None
                    try:
                        raw_proposal: object = callback(invocation)
                    except Exception:
                        failure_code = "AF-OPERATOR-CALLBACK"
                    else:
                        if not isinstance(raw_proposal, OperatorProposal):
                            failure_code = "AF-OPERATOR-RESULT"
                        else:
                            try:
                                proposal = OperatorProposal(
                                    **raw_proposal.model_dump(mode="python")
                                )
                            except Exception:
                                failure_code = "AF-OPERATOR-RESULT"
                    if proposal is None:
                        self._append_proposal_event(
                            generation_index=generation_index,
                            slot_index=slot_index,
                            kind=ProposalEventKind.INVALID,
                            candidate_hash=None,
                            operator_id=operator.operator_id,
                            parent_hash=parent_alternative.parent_hash,
                            parameter_hash=parameter_alternative.parameter_hash,
                            reason_code=failure_code or "AF-OPERATOR-RESULT",
                        )
                        self._failure_reason = SearchFailureReason.OPERATOR_FAILED
                        return None
                    if proposal.candidate is None:
                        self._append_proposal_event(
                            generation_index=generation_index,
                            slot_index=slot_index,
                            kind=ProposalEventKind.INVALID,
                            candidate_hash=None,
                            operator_id=operator.operator_id,
                            parent_hash=parent_alternative.parent_hash,
                            parameter_hash=parameter_alternative.parameter_hash,
                            reason_code=proposal.invalid_reason,
                        )
                        continue
                    candidate = proposal.candidate
                    if (
                        candidate.domain is not self._search_spec.domain
                        or candidate.operator_set_hash != self._search_spec.operator_set_hash
                    ):
                        self._append_proposal_event(
                            generation_index=generation_index,
                            slot_index=slot_index,
                            kind=ProposalEventKind.INVALID,
                            candidate_hash=candidate.candidate_hash,
                            operator_id=operator.operator_id,
                            parent_hash=parent_alternative.parent_hash,
                            parameter_hash=parameter_alternative.parameter_hash,
                            reason_code="AF-OPERATOR-IDENTITY",
                        )
                        self._failure_reason = SearchFailureReason.OPERATOR_FAILED
                        return None
                    candidate_hash = candidate.candidate_hash
                    if not self._universe.contains(candidate_hash):
                        self._append_proposal_event(
                            generation_index=generation_index,
                            slot_index=slot_index,
                            kind=ProposalEventKind.OUTSIDE_UNIVERSE,
                            candidate_hash=candidate_hash,
                            operator_id=operator.operator_id,
                            parent_hash=parent_alternative.parent_hash,
                            parameter_hash=parameter_alternative.parameter_hash,
                            reason_code=None,
                        )
                        continue
                    if candidate_hash in self._proposed or candidate_hash in self._visited:
                        self._append_proposal_event(
                            generation_index=generation_index,
                            slot_index=slot_index,
                            kind=ProposalEventKind.DUPLICATE,
                            candidate_hash=candidate_hash,
                            operator_id=operator.operator_id,
                            parent_hash=parent_alternative.parent_hash,
                            parameter_hash=parameter_alternative.parameter_hash,
                            reason_code=None,
                        )
                        continue
                    return self._accept_candidate(
                        candidate=candidate,
                        generation_index=generation_index,
                        slot_index=slot_index,
                        source=ProposalSource.OPERATOR,
                        operator_id=operator.operator_id,
                        parent_alternative=parent_alternative,
                        parameter_alternative=parameter_alternative,
                    )

        fallback = first_unseen_candidate(
            self._universe,
            self._search_spec.seed,
            self._proposed,
            self._visited,
        )
        if fallback is None:
            self._append_proposal_event(
                generation_index=generation_index,
                slot_index=slot_index,
                kind=ProposalEventKind.SLOT_EXHAUSTED,
                candidate_hash=None,
                operator_id=None,
                parent_hash=None,
                parameter_hash=None,
                reason_code=None,
            )
            return None
        self._append_proposal_event(
            generation_index=generation_index,
            slot_index=slot_index,
            kind=ProposalEventKind.DIRECT_FALLBACK,
            candidate_hash=fallback.candidate_hash,
            operator_id=None,
            parent_hash=None,
            parameter_hash=None,
            reason_code=None,
        )
        return self._accept_candidate(
            candidate=fallback,
            generation_index=generation_index,
            slot_index=slot_index,
            source=ProposalSource.DIRECT_FALLBACK,
            operator_id=None,
            parent_alternative=None,
            parameter_alternative=None,
        )

    def _parent_alternatives(
        self,
        generation_index: int,
        slot_index: int,
        schedule_offset: int,
        operator: SearchOperator,
        parent_pool: ParentPool,
    ) -> tuple[ParentAlternative, ...]:
        parent_hashes = parent_pool.parent_candidate_hashes
        if operator.arity > len(parent_hashes):
            return ()
        tuples: tuple[tuple[str, ...], ...]
        if operator.arity == 0:
            tuples = ((),)
        elif operator.commutative:
            canonical_hashes = tuple(sorted(parent_hashes, key=digest_bytes))
            tuples = tuple(combinations(canonical_hashes, operator.arity))
        else:
            tuples = tuple(permutations(parent_hashes, operator.arity))
        alternatives = tuple(
            ParentAlternative(
                generation_index=generation_index,
                operator_id=operator.operator_id,
                parent_hashes=parent_tuple,
                schedule_offset=schedule_offset,
                seed=self._search_spec.seed,
                slot_index=slot_index,
            )
            for parent_tuple in tuples
        )
        return tuple(
            sorted(
                alternatives,
                key=lambda alternative: (
                    digest_bytes(alternative.parent_hash),
                    tuple(digest_bytes(value) for value in alternative.parent_hashes),
                ),
            )
        )

    def _parameter_alternatives(
        self,
        generation_index: int,
        slot_index: int,
        schedule_offset: int,
        operator: SearchOperator,
    ) -> tuple[ParameterAlternative, ...]:
        index_ranges = tuple(range(len(parameter.values)) for parameter in operator.parameters)
        index_vectors = tuple(product(*index_ranges)) if index_ranges else ((),)
        alternatives = tuple(
            ParameterAlternative(
                generation_index=generation_index,
                operator_id=operator.operator_id,
                parameter_indices=indices,
                schedule_offset=schedule_offset,
                seed=self._search_spec.seed,
                slot_index=slot_index,
            )
            for indices in index_vectors
        )
        return tuple(
            sorted(
                alternatives,
                key=lambda alternative: (
                    digest_bytes(alternative.parameter_hash),
                    alternative.parameter_indices,
                ),
            )
        )

    def _accept_candidate(
        self,
        *,
        candidate: Candidate,
        generation_index: int,
        slot_index: int,
        source: ProposalSource,
        operator_id: str | None,
        parent_alternative: ParentAlternative | None,
        parameter_alternative: ParameterAlternative | None,
    ) -> CandidateTrial:
        candidate_hash = candidate.candidate_hash
        if candidate_hash in self._proposed or candidate_hash in self._visited:
            raise RuntimeError("accepted candidates must be run-unseen")
        self._proposed.add(candidate_hash)
        self._visited.add(candidate_hash)
        self._proposed_order.append(candidate_hash)
        self._visited_order.append(candidate_hash)
        self._proposals.append(
            Proposal(
                candidate_hash=candidate_hash,
                generation_index=generation_index,
                slot_index=slot_index,
                source=source,
                operator_id=operator_id,
                parent_hash=(
                    parent_alternative.parent_hash if parent_alternative is not None else None
                ),
                parameter_hash=(
                    parameter_alternative.parameter_hash
                    if parameter_alternative is not None
                    else None
                ),
            )
        )
        return self._start_and_evaluate(
            candidate=candidate,
            generation_index=generation_index,
            slot_index=slot_index,
            operator_id=operator_id,
            parent_alternative=parent_alternative,
            parameter_alternative=parameter_alternative,
        )

    def _start_and_evaluate(
        self,
        *,
        candidate: Candidate,
        generation_index: int,
        slot_index: int,
        operator_id: str | None,
        parent_alternative: ParentAlternative | None,
        parameter_alternative: ParameterAlternative | None,
    ) -> CandidateTrial:
        ledger_position = len(self._trials)
        trial_id = f"trial-{ledger_position}"
        self._append_trial_event(
            trial_id=trial_id,
            ledger_position=ledger_position,
            kind=TrialEventKind.TRIAL_STARTED,
            error_code=None,
        )
        try:
            result: object = self._evaluate_candidate(candidate)
            if not isinstance(result, TrialEvaluation):
                raise TypeError("candidate evaluator must return TrialEvaluation")
            evaluation = TrialEvaluation(**result.model_dump(mode="python"))
        except Exception:
            evaluation = TrialEvaluation(
                terminal=CandidateTrialTerminal.ENGINE_FAILED,
                metrics=(),
                gate_results=(),
                complete=False,
                error_code="AF-ENGINE-CALLBACK",
            )
            self._failure_reason = SearchFailureReason.EVALUATOR_FAILED
        trial = CandidateTrial(
            trial_id=trial_id,
            ledger_position=ledger_position,
            candidate_hash=candidate.candidate_hash,
            generation_index=generation_index,
            slot_index=slot_index,
            operator_id=operator_id,
            parent_hashes=(
                parent_alternative.parent_hashes if parent_alternative is not None else ()
            ),
            parameter_indices=(
                parameter_alternative.parameter_indices if parameter_alternative is not None else ()
            ),
            terminal=evaluation.terminal,
            evaluation=evaluation,
        )
        self._trials.append(trial)
        self._append_trial_event(
            trial_id=trial_id,
            ledger_position=ledger_position,
            kind=TrialEventKind(evaluation.terminal.value),
            error_code=evaluation.error_code,
        )
        if evaluation.terminal is CandidateTrialTerminal.INTERRUPTED:
            self._interrupted = True
        if evaluation.terminal is CandidateTrialTerminal.ENGINE_FAILED:
            self._failure_reason = SearchFailureReason.EVALUATOR_FAILED
        return trial

    def _complete_generation(
        self, generation_trials: list[CandidateTrial]
    ) -> tuple[SearchStopReason | None, SearchFailureReason | None]:
        if self._failure_reason is not None:
            return None, self._failure_reason
        if generation_trials:
            ranked = rank_trials(self._profile, generation_trials)
            self._update_plateau(ranked)
        if self._interrupted:
            return None, SearchFailureReason.INTERRUPTED
        if len(self._visited) == len(self._universe.candidates):
            return SearchStopReason.UNIVERSE_EXHAUSTED, None
        if not generation_trials:
            return None, SearchFailureReason.FAILED_INVARIANT
        if self._plateau_counter >= self._search_spec.patience:
            return SearchStopReason.PLATEAU, None
        return None, None

    def _update_plateau(self, ranked: tuple[RankedTrial, ...]) -> None:
        if not ranked:
            self._plateau_counter += 1
            return
        generation_best = ranked[0]
        if self._best_scores is None:
            self._best_scores = generation_best.quantized_scores
            self._best_candidate_hash = generation_best.trial.candidate_hash
            self._plateau_counter = 0
            return
        score_comparison = compare_score_vectors(
            self._profile, generation_best.quantized_scores, self._best_scores
        )
        if score_comparison > 0:
            self._best_scores = generation_best.quantized_scores
            self._best_candidate_hash = generation_best.trial.candidate_hash
            self._plateau_counter = 0
            return
        if (
            score_comparison == 0
            and self._best_candidate_hash is not None
            and digest_bytes(generation_best.trial.candidate_hash)
            < digest_bytes(self._best_candidate_hash)
        ):
            self._best_candidate_hash = generation_best.trial.candidate_hash
        self._plateau_counter += 1

    def _append_proposal_event(
        self,
        *,
        generation_index: int,
        slot_index: int,
        kind: ProposalEventKind,
        candidate_hash: str | None,
        operator_id: str | None,
        parent_hash: str | None,
        parameter_hash: str | None,
        reason_code: str | None,
    ) -> None:
        self._events.append(
            ProposalEvent(
                sequence=self._event_sequence,
                generation_index=generation_index,
                slot_index=slot_index,
                kind=kind,
                candidate_hash=candidate_hash,
                operator_id=operator_id,
                parent_hash=parent_hash,
                parameter_hash=parameter_hash,
                reason_code=reason_code,
            )
        )
        self._event_sequence += 1

    def _append_trial_event(
        self,
        *,
        trial_id: str,
        ledger_position: int,
        kind: TrialEventKind,
        error_code: str | None,
    ) -> None:
        self._events.append(
            TrialEvent(
                sequence=self._event_sequence,
                trial_id=trial_id,
                ledger_position=ledger_position,
                kind=kind,
                error_code=error_code,
            )
        )
        self._event_sequence += 1

    def _result(
        self,
        *,
        generation_count: int,
        stop_reason: SearchStopReason | None,
        failure_reason: SearchFailureReason | None,
    ) -> SearchRunResult:
        return SearchRunResult(
            search_spec_hash=self._search_spec.search_spec_hash,
            universe_hash=self._universe.universe_hash,
            profile_hash=self._search_spec.profile_hash,
            stop_reason=stop_reason,
            failure_reason=failure_reason,
            best_candidate_hash=self._best_candidate_hash,
            plateau_counter=self._plateau_counter,
            generation_count=generation_count,
            parent_pools=tuple(self._parent_pools),
            proposals=tuple(self._proposals),
            proposed_candidate_hashes=tuple(self._proposed_order),
            visited_candidate_hashes=tuple(self._visited_order),
            events=tuple(self._events),
            trials=tuple(self._trials),
        )


def run_search(
    *,
    universe: Universe,
    search_spec: SearchSpec,
    validation_profile: ValidationProfile,
    operators: Mapping[str, SearchOperator],
    operator_callbacks: Mapping[str, OperatorCallback],
    evaluate_candidate: CandidateEvaluator,
) -> SearchRunResult:
    """Run a complete finite deterministic search with the supplied pure callbacks."""
    return SearchRunner(
        universe=universe,
        search_spec=search_spec,
        validation_profile=validation_profile,
        operators=operators,
        operator_callbacks=operator_callbacks,
        evaluate_candidate=evaluate_candidate,
    ).run()
