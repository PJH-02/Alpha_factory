"""Complete-ledger CSCV/PBO certification and deterministic calculation.

The implementation deliberately separates certification from calculation.  A
CSCV result can be calculated only from a certificate whose exact trial and
fold equality checks have passed; absent values are never imputed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from itertools import combinations
from math import comb

from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    Decision,
    GateComparison,
    GateResult,
    RankingDirection,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.search.models import (
    CandidateTrial,
    CandidateTrialTerminal,
    SearchRunResult,
    digest_bytes,
)

_PBO_MAX_PRECISION = 34
_PBO_MIN_EMIN = -6143
_PBO_MAX_EMAX = 6144


class PboCertificateDecision(StrEnum):
    """The only states of a PBO input completeness certificate."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class TrialTerminalEvidence:
    """Terminal ledger evidence used to determine PBO eligibility without invention."""

    trial_id: str
    terminal: CandidateTrialTerminal
    complete: bool
    failed_hard_gate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.trial_id, "trial_id")
        if not isinstance(self.terminal, CandidateTrialTerminal):
            raise ValueError("trial terminal must be a CandidateTrialTerminal")
        if not isinstance(self.complete, bool):
            raise ValueError("trial score completeness must be a boolean")
        _require_unique(self.failed_hard_gate_ids, "failed hard-gate IDs")
        for gate_id in self.failed_hard_gate_ids:
            _require_identifier(gate_id, "failed hard-gate ID")
        if self.terminal is CandidateTrialTerminal.EVALUATED:
            if self.failed_hard_gate_ids:
                raise ValueError("an evaluated trial cannot name failed hard gates")
        elif self.terminal is CandidateTrialTerminal.REJECTED_HARD_GATE:
            if not self.failed_hard_gate_ids:
                raise ValueError("a hard-gate-rejected trial must name failed hard gates")
        elif self.complete:
            raise ValueError("only evaluated or hard-gate-rejected trials can have complete scores")


@dataclass(frozen=True, slots=True)
class PboEligibilityPolicy:
    """The profile-pinned exception allowing selected hard-gate rows into PBO."""

    profile_hash: str
    eligible_hard_gate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_digest(self.profile_hash, "profile_hash")
        _require_unique(self.eligible_hard_gate_ids, "eligible hard-gate IDs")
        for gate_id in self.eligible_hard_gate_ids:
            _require_identifier(gate_id, "eligible hard-gate ID")

    def permits(self, *, profile: ValidationProfile, trial: CandidateTrial) -> bool:
        """Return whether a completed trial has profile-exact PBO eligibility evidence."""
        if self.profile_hash != profile.profile_hash:
            return False
        evaluation = trial.evaluation
        if (
            evaluation is None
            or evaluation.terminal is not trial.terminal
            or not evaluation.complete
            or _profile_gate_evidence_error(profile, trial) is not None
        ):
            return False
        if trial.terminal is CandidateTrialTerminal.EVALUATED:
            return True
        if trial.terminal is not CandidateTrialTerminal.REJECTED_HARD_GATE:
            return False
        failed_gate_id = evaluation.gate_results[-1].gate_id
        return failed_gate_id in self.eligible_hard_gate_ids


@dataclass(frozen=True, slots=True)
class TrialFoldScore:
    """One observed scalar PBO score; no missing-score value exists."""

    trial_id: str
    fold_id: str
    score: Decimal

    def __post_init__(self) -> None:
        _require_identifier(self.trial_id, "trial_id")
        _require_identifier(self.fold_id, "fold_id")


@dataclass(frozen=True, slots=True)
class FoldCoverage:
    """Observed fold IDs for one PBO matrix row, retained in the certificate."""

    trial_id: str
    observed_fold_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.trial_id, "trial_id")
        _require_unique(self.observed_fold_ids, "observed fold IDs")
        for fold_id in self.observed_fold_ids:
            _require_identifier(fold_id, "observed fold ID")


@dataclass(frozen=True, slots=True)
class PboCompletenessCertificate:
    """Authoritative, lineage-bound evidence that authorizes one exact CSCV matrix."""

    lineage_id: str
    validation_id: str
    profile_hash: str
    search_run_hash: str
    score_direction: RankingDirection
    matrix_digest: str | None
    decision: PboCertificateDecision
    reason: ErrorDetail | None
    started_trial_ids: tuple[str, ...]
    terminal_trial_ids: tuple[str, ...]
    eligible_trial_ids: tuple[str, ...]
    matrix_trial_ids: tuple[str, ...]
    expected_fold_ids: tuple[str, ...]
    fold_coverage: tuple[FoldCoverage, ...]
    normal_stop: bool
    minimum_eligible: int

    def __post_init__(self) -> None:
        _require_identifier(self.lineage_id, "lineage_id")
        _require_identifier(self.validation_id, "validation_id")
        _require_digest(self.profile_hash, "profile_hash")
        _require_digest(self.search_run_hash, "search_run_hash")
        if not isinstance(self.score_direction, RankingDirection):
            raise ValueError("score_direction must be a RankingDirection")
        if not isinstance(self.decision, PboCertificateDecision):
            raise ValueError("decision must be a PboCertificateDecision")
        if self.matrix_digest is not None:
            _require_digest(self.matrix_digest, "matrix_digest")
        if not isinstance(self.normal_stop, bool):
            raise ValueError("normal_stop must be a boolean")
        if self.minimum_eligible < 1:
            raise ValueError("minimum_eligible must be at least one")
        if self.decision is PboCertificateDecision.FAIL:
            if self.reason is None or self.reason.code is not ErrorCode.PBO_INCOMPLETE:
                raise ValueError("a failing PBO certificate requires AF-PBO-INCOMPLETE")
            return
        if self.reason is not None:
            raise ValueError("a passing PBO certificate cannot carry a reason")
        if self.matrix_digest is None:
            raise ValueError("a passing PBO certificate requires a matrix digest")
        for values, label in (
            (self.started_trial_ids, "started trial IDs"),
            (self.terminal_trial_ids, "terminal trial IDs"),
            (self.eligible_trial_ids, "eligible trial IDs"),
            (self.matrix_trial_ids, "matrix trial IDs"),
            (self.expected_fold_ids, "expected fold IDs"),
        ):
            _require_unique(values, label)
            for value in values:
                _require_identifier(value, label[:-1])
        _require_unique(
            tuple(item.trial_id for item in self.fold_coverage), "fold coverage trial IDs"
        )
        if len(self.expected_fold_ids) < 2 or len(self.expected_fold_ids) % 2:
            raise ValueError(
                "a passing PBO certificate requires an even number of at least two folds"
            )
        if set(self.started_trial_ids) != set(self.terminal_trial_ids):
            raise ValueError("a passing PBO certificate requires started=terminal")
        if set(self.eligible_trial_ids) != set(self.matrix_trial_ids):
            raise ValueError("a passing PBO certificate requires eligible=matrix")
        if {item.trial_id for item in self.fold_coverage} != set(self.matrix_trial_ids):
            raise ValueError("a passing PBO certificate requires coverage for every matrix trial")
        expected_folds = set(self.expected_fold_ids)
        if any(set(item.observed_fold_ids) != expected_folds for item in self.fold_coverage):
            raise ValueError("a passing PBO certificate requires the exact fold set on every row")
        if not self.normal_stop:
            raise ValueError("a passing PBO certificate requires a normally stopped search")
        if len(self.eligible_trial_ids) < self.minimum_eligible:
            raise ValueError("a passing PBO certificate requires the minimum eligible count")

    @property
    def certificate_hash(self) -> str:
        """Return the immutable identity required by matrix calculation and holdout."""
        if not self.is_complete or self.matrix_digest is None:
            raise ValueError("only a passing PBO certificate has an authorization identity")
        return digest(
            "AF:PBO:CERTIFICATE:1",
            {
                "eligible_trial_ids": list(self.eligible_trial_ids),
                "expected_fold_ids": list(self.expected_fold_ids),
                "fold_coverage": [
                    {
                        "observed_fold_ids": list(item.observed_fold_ids),
                        "trial_id": item.trial_id,
                    }
                    for item in self.fold_coverage
                ],
                "lineage_id": self.lineage_id,
                "matrix_digest": digest_bytes(self.matrix_digest),
                "matrix_trial_ids": list(self.matrix_trial_ids),
                "minimum_eligible": self.minimum_eligible,
                "normal_stop": self.normal_stop,
                "profile_hash": digest_bytes(self.profile_hash),
                "score_direction": self.score_direction.value,
                "search_run_hash": digest_bytes(self.search_run_hash),
                "started_trial_ids": list(self.started_trial_ids),
                "terminal_trial_ids": list(self.terminal_trial_ids),
                "validation_id": self.validation_id,
            },
        )

    @property
    def is_complete(self) -> bool:
        """Return whether this certificate authorizes matrix calculation and holdout."""
        return self.decision is PboCertificateDecision.PASS

    def blocking_reason(self) -> ErrorDetail | None:
        """Return the typed reason that blocks holdout/publication, when any."""
        return self.reason


@dataclass(frozen=True, slots=True)
class TrialFoldMatrix:
    """A fully certified complete trial-by-fold score matrix."""

    profile_hash: str
    fold_ids: tuple[str, ...]
    trial_ids: tuple[str, ...]
    score_direction: RankingDirection
    scores: tuple[TrialFoldScore, ...]
    certificate_hash: str | None = None
    matrix_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.profile_hash, "profile_hash")
        _require_unique(self.fold_ids, "fold IDs")
        _require_unique(self.trial_ids, "trial IDs")
        if len(self.fold_ids) < 2 or len(self.fold_ids) % 2:
            raise ValueError("CSCV requires an even number of at least two folds")
        if not self.trial_ids:
            raise ValueError("a trial-by-fold matrix requires at least one trial")
        expected_pairs = {
            (trial_id, fold_id) for trial_id in self.trial_ids for fold_id in self.fold_ids
        }
        actual_pairs = {(score.trial_id, score.fold_id) for score in self.scores}
        if len(actual_pairs) != len(self.scores) or actual_pairs != expected_pairs:
            raise ValueError("matrix scores must contain each trial/fold pair exactly once")
        if any(
            not isinstance(score.score, Decimal) or not score.score.is_finite()
            for score in self.scores
        ):
            raise ValueError("matrix scores must be finite Decimals")
        if (self.certificate_hash is None) != (self.matrix_digest is None):
            raise ValueError("certified matrices require both certificate and matrix digests")
        if self.certificate_hash is not None:
            matrix_digest = self.matrix_digest
            if matrix_digest is None:
                raise ValueError("certified matrices require a matrix digest")
            _require_digest(self.certificate_hash, "certificate_hash")
            _require_digest(matrix_digest, "matrix_digest")
            if matrix_digest != self.matrix_hash:
                raise ValueError("matrix_digest must bind the exact trial-fold matrix")

    @property
    def matrix_hash(self) -> str:
        """Return the digest of all score values and matrix membership."""
        return _matrix_digest(
            profile_hash=self.profile_hash,
            fold_ids=self.fold_ids,
            trial_ids=self.trial_ids,
            score_direction=self.score_direction,
            scores=self.scores,
        )

    def score(self, trial_id: str, fold_id: str) -> Decimal:
        """Return one certified matrix score."""
        for item in self.scores:
            if item.trial_id == trial_id and item.fold_id == fold_id:
                return item.score
        raise ValueError("trial/fold pair is not present in the matrix")


@dataclass(frozen=True, slots=True)
class CscvPartition:
    """One combinatorially symmetric train/test fold partition."""

    partition_id: str
    in_sample_fold_ids: tuple[str, ...]
    out_of_sample_fold_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.partition_id, "partition_id")
        if not self.in_sample_fold_ids or not self.out_of_sample_fold_ids:
            raise ValueError(
                "a CSCV partition requires non-empty in-sample and out-of-sample folds"
            )
        _require_unique(self.in_sample_fold_ids, "in-sample fold IDs")
        _require_unique(self.out_of_sample_fold_ids, "out-of-sample fold IDs")
        if set(self.in_sample_fold_ids) & set(self.out_of_sample_fold_ids):
            raise ValueError("CSCV in-sample and out-of-sample folds must be disjoint")


@dataclass(frozen=True, slots=True)
class CscvOutcome:
    """The selected IS winner and its OOS relative rank for one CSCV partition."""

    partition: CscvPartition
    selected_trial_id: str
    in_sample_score: Decimal
    out_of_sample_score: Decimal
    out_of_sample_rank: int
    relative_rank: Decimal
    logit: Decimal
    overfit: bool

    def __post_init__(self) -> None:
        _require_identifier(self.selected_trial_id, "selected_trial_id")
        if self.out_of_sample_rank < 1:
            raise ValueError("out-of-sample rank must be at least one")
        values = (
            self.in_sample_score,
            self.out_of_sample_score,
            self.relative_rank,
            self.logit,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("CSCV outcomes must be finite")
        if not Decimal(0) < self.relative_rank < Decimal(1):
            raise ValueError("relative rank must be strictly between zero and one")
        if not isinstance(self.overfit, bool):
            raise ValueError("overfit must be a boolean")
        if self.overfit is not (self.logit < Decimal(0)):
            raise ValueError("overfit must equal logit < 0")


@dataclass(frozen=True, slots=True)
class PboResult:
    """A certificate-bound CSCV formula result: PBO is ``overfit_count / partition_count``."""

    lineage_id: str
    validation_id: str
    profile_hash: str
    search_run_hash: str
    certificate_hash: str
    matrix_digest: str
    decimal_precision: int
    partitions: tuple[CscvOutcome, ...]
    overfit_count: int
    partition_count: int
    probability_of_backtest_overfitting: Decimal

    def __post_init__(self) -> None:
        _require_identifier(self.lineage_id, "lineage_id")
        _require_identifier(self.validation_id, "validation_id")
        _require_digest(self.profile_hash, "profile_hash")
        _require_digest(self.search_run_hash, "search_run_hash")
        _require_digest(self.certificate_hash, "certificate_hash")
        _require_digest(self.matrix_digest, "matrix_digest")
        if (
            not isinstance(self.decimal_precision, int)
            or isinstance(self.decimal_precision, bool)
            or self.decimal_precision < 1
        ):
            raise ValueError("decimal_precision must be positive")
        if (
            not isinstance(self.overfit_count, int)
            or isinstance(self.overfit_count, bool)
            or not isinstance(self.partition_count, int)
            or isinstance(self.partition_count, bool)
        ):
            raise ValueError("PBO counts must be integers")
        if self.partition_count != len(self.partitions) or self.partition_count < 1:
            raise ValueError("partition_count must equal the non-empty outcome count")
        if self.overfit_count != sum(outcome.overfit for outcome in self.partitions):
            raise ValueError("overfit_count must equal the outcome count")
        if (
            not isinstance(self.probability_of_backtest_overfitting, Decimal)
            or not self.probability_of_backtest_overfitting.is_finite()
            or not Decimal(0) <= self.probability_of_backtest_overfitting <= Decimal(1)
        ):
            raise ValueError("PBO must be a finite probability")
        with localcontext(_decimal_context(self.decimal_precision)):
            expected_probability = Decimal(self.overfit_count) / Decimal(self.partition_count)
        if self.probability_of_backtest_overfitting != expected_probability:
            raise ValueError("PBO must equal overfit_count / partition_count")

    @property
    def pbo_result_hash(self) -> str:
        """Return the full immutable calculation and certificate provenance identity."""
        return digest(
            "AF:PBO:RESULT:1",
            {
                "certificate_hash": digest_bytes(self.certificate_hash),
                "decimal_precision": self.decimal_precision,
                "lineage_id": self.lineage_id,
                "matrix_digest": digest_bytes(self.matrix_digest),
                "overfit_count": self.overfit_count,
                "partition_count": self.partition_count,
                "partitions": [
                    {
                        "in_sample_fold_ids": list(outcome.partition.in_sample_fold_ids),
                        "in_sample_score": outcome.in_sample_score,
                        "logit": outcome.logit,
                        "out_of_sample_fold_ids": list(outcome.partition.out_of_sample_fold_ids),
                        "out_of_sample_rank": outcome.out_of_sample_rank,
                        "out_of_sample_score": outcome.out_of_sample_score,
                        "overfit": outcome.overfit,
                        "partition_id": outcome.partition.partition_id,
                        "relative_rank": outcome.relative_rank,
                        "selected_trial_id": outcome.selected_trial_id,
                    }
                    for outcome in self.partitions
                ],
                "probability_of_backtest_overfitting": self.probability_of_backtest_overfitting,
                "profile_hash": digest_bytes(self.profile_hash),
                "search_run_hash": digest_bytes(self.search_run_hash),
                "validation_id": self.validation_id,
            },
        )


def certify_pbo_input(
    *,
    profile: ValidationProfile,
    eligibility_policy: PboEligibilityPolicy,
    scores: Iterable[TrialFoldScore],
    lineage_id: str,
    validation_id: str,
    search_run: SearchRunResult,
    score_direction: RankingDirection,
) -> PboCompletenessCertificate:
    """Certify PBO only from an authoritative, profile-bound search ledger."""
    return _certify_search_run_input(
        lineage_id=lineage_id,
        validation_id=validation_id,
        search_run=search_run,
        profile=profile,
        eligibility_policy=eligibility_policy,
        scores=scores,
        score_direction=score_direction,
    )


def _certify_search_run_input(
    *,
    lineage_id: str,
    validation_id: str,
    search_run: SearchRunResult,
    profile: ValidationProfile,
    eligibility_policy: PboEligibilityPolicy,
    scores: Iterable[TrialFoldScore],
    score_direction: RankingDirection,
) -> PboCompletenessCertificate:
    """Certify an exact PBO matrix from the completed authoritative search ledger."""
    _require_identifier(lineage_id, "lineage_id")
    _require_identifier(validation_id, "validation_id")
    if not isinstance(search_run, SearchRunResult):
        raise ValueError("search_run must be a completed SearchRunResult")
    if not isinstance(score_direction, RankingDirection):
        raise ValueError("score_direction must be a RankingDirection")
    if score_direction is not profile.ranking[0].direction:
        raise ValueError("score_direction must match the frozen validation profile")

    expected_fold_ids = profile.fold_ids
    score_rows = tuple(scores)
    started = tuple(trial.trial_id for trial in search_run.trials)
    terminal_ids: list[str] = []
    derived_eligible: list[str] = []
    violations: list[str] = []
    try:
        SearchRunResult.model_validate(
            search_run.model_dump(mode="python", exclude_computed_fields=True)
        )
    except (TypeError, ValueError):
        violations.append("search run violates authoritative completed-ledger invariants")
    for trial in search_run.trials:
        if trial.terminal is None or trial.evaluation is None:
            violations.append("search run contains a started trial without terminal evidence")
            continue
        terminal_ids.append(trial.trial_id)
        gate_evidence_error = _profile_gate_evidence_error(profile, trial)
        if gate_evidence_error is not None:
            violations.append(
                f"trial {trial.trial_id!r} hard-gate evidence does not exactly match "
                f"the frozen profile: {gate_evidence_error}"
            )
            continue
        if eligibility_policy.permits(profile=profile, trial=trial):
            derived_eligible.append(trial.trial_id)
    terminal_id_tuple = tuple(terminal_ids)
    derived_eligible_tuple = tuple(sorted(derived_eligible))

    _record_invalid_identifiers(started, "started trial IDs", violations)
    _record_invalid_identifiers(terminal_id_tuple, "terminal trial IDs", violations)
    _record_duplicates(started, "started trial IDs", violations)
    _record_duplicates(terminal_id_tuple, "terminal trial IDs", violations)
    _record_duplicates(expected_fold_ids, "profile fold IDs", violations)
    if len(expected_fold_ids) < 2 or len(expected_fold_ids) % 2:
        violations.append("CSCV requires an even number of at least two profile folds")
    if search_run.profile_hash != profile.profile_hash:
        violations.append("search run profile hash does not match the validation profile")
    if eligibility_policy.profile_hash != profile.profile_hash:
        violations.append("eligibility policy profile hash does not match the validation profile")
    unknown_eligible_gates = set(eligibility_policy.eligible_hard_gate_ids) - {
        gate.gate_id for gate in profile.hard_gates
    }
    if unknown_eligible_gates:
        violations.append("eligibility policy names hard gates absent from the validation profile")
    if set(started) != set(terminal_id_tuple) or len(started) != len(terminal_id_tuple):
        violations.append("started and terminal trial IDs are not exactly equal")

    matrix_ids, coverage = _matrix_coverage(score_rows, violations)
    if set(derived_eligible_tuple) != set(matrix_ids) or len(derived_eligible_tuple) != len(
        matrix_ids
    ):
        violations.append("eligible and matrix trial IDs are not exactly equal")
    expected_fold_set = set(expected_fold_ids)
    for item in coverage:
        if set(item.observed_fold_ids) != expected_fold_set or len(item.observed_fold_ids) != len(
            expected_fold_ids
        ):
            violations.append(
                f"matrix row {item.trial_id!r} does not have the exact profile fold set"
            )
    derived_eligible_set = set(derived_eligible_tuple)
    for score in score_rows:
        if not isinstance(score.score, Decimal) or not score.score.is_finite():
            violations.append("matrix contains a non-finite or non-Decimal score")
            break
        if score.trial_id not in derived_eligible_set:
            violations.append("matrix contains a score for a non-eligible terminal")
            break
        if score.fold_id not in expected_fold_set:
            violations.append("matrix contains a score for a fold outside the profile")
            break
    normal_stop = search_run.stop_reason is not None and search_run.failure_reason is None
    if not normal_stop:
        violations.append("PBO requires a normally stopped search run")
    if len(derived_eligible_tuple) < profile.pbo_minimum_eligible:
        violations.append("eligible trial count is below the profile minimum")

    try:
        matrix_digest = _matrix_digest(
            profile_hash=profile.profile_hash,
            fold_ids=expected_fold_ids,
            trial_ids=derived_eligible_tuple,
            score_direction=score_direction,
            scores=score_rows,
        )
    except ValueError:
        matrix_digest = None
    return _certificate(
        lineage_id=lineage_id,
        validation_id=validation_id,
        search_run_hash=_search_run_hash(search_run),
        profile=profile,
        score_direction=score_direction,
        matrix_digest=matrix_digest,
        violations=violations,
        started=started,
        terminal_ids=terminal_id_tuple,
        eligible=derived_eligible_tuple,
        matrix_ids=matrix_ids,
        coverage=coverage,
        normal_stop=normal_stop,
    )


def build_trial_fold_matrix(
    *,
    certificate: PboCompletenessCertificate,
    scores: Iterable[TrialFoldScore],
    score_direction: RankingDirection,
) -> TrialFoldMatrix:
    """Build only the certificate-bound score matrix authorized for calculation."""
    if not certificate.is_complete:
        raise DomainError(certificate.reason or _pbo_reason("PBO certificate is incomplete"))
    if score_direction is not certificate.score_direction:
        raise DomainError(_pbo_reason("score direction does not match the PBO certificate"))
    score_rows = tuple(scores)
    certified_pairs = {
        (trial_id, fold_id)
        for trial_id in certificate.matrix_trial_ids
        for fold_id in certificate.expected_fold_ids
    }
    actual_pairs = {(score.trial_id, score.fold_id) for score in score_rows}
    if actual_pairs != certified_pairs or len(actual_pairs) != len(score_rows):
        raise DomainError(_pbo_reason("scores do not exactly match the certified matrix"))
    if any(
        not isinstance(score.score, Decimal) or not score.score.is_finite() for score in score_rows
    ):
        raise DomainError(_pbo_reason("certified matrix contains a non-finite score"))
    ordered_scores = tuple(sorted(score_rows, key=lambda item: (item.trial_id, item.fold_id)))
    actual_matrix_digest = _matrix_digest(
        profile_hash=certificate.profile_hash,
        fold_ids=certificate.expected_fold_ids,
        trial_ids=certificate.matrix_trial_ids,
        score_direction=score_direction,
        scores=ordered_scores,
    )
    if certificate.matrix_digest != actual_matrix_digest:
        raise DomainError(_pbo_reason("scores do not match the certificate matrix digest"))
    return TrialFoldMatrix(
        profile_hash=certificate.profile_hash,
        fold_ids=certificate.expected_fold_ids,
        trial_ids=certificate.matrix_trial_ids,
        score_direction=score_direction,
        scores=ordered_scores,
        certificate_hash=certificate.certificate_hash,
        matrix_digest=actual_matrix_digest,
    )


def construct_cscv_partitions(fold_ids: Iterable[str]) -> tuple[CscvPartition, ...]:
    """Enumerate all ``C(S, S/2)`` deterministic CSCV partitions for ``S`` folds."""
    frozen_fold_ids = tuple(fold_ids)
    _require_unique(frozen_fold_ids, "fold IDs")
    for fold_id in frozen_fold_ids:
        _require_identifier(fold_id, "fold ID")
    fold_count = len(frozen_fold_ids)
    if fold_count < 2 or fold_count % 2:
        raise ValueError("CSCV requires an even number of at least two folds")
    half = fold_count // 2
    result: list[CscvPartition] = []
    for index, in_sample_indices in enumerate(combinations(range(fold_count), half)):
        in_sample_index_set = set(in_sample_indices)
        result.append(
            CscvPartition(
                partition_id=f"cscv-{index:0{len(str(comb(fold_count, half) - 1))}d}",
                in_sample_fold_ids=tuple(
                    frozen_fold_ids[position] for position in in_sample_indices
                ),
                out_of_sample_fold_ids=tuple(
                    fold_id
                    for position, fold_id in enumerate(frozen_fold_ids)
                    if position not in in_sample_index_set
                ),
            )
        )
    return tuple(result)


def calculate_pbo(
    matrix: TrialFoldMatrix,
    *,
    certificate: PboCompletenessCertificate,
    decimal_precision: int,
) -> PboResult:
    """Calculate PBO only from the exact matrix bound to a passing certificate."""
    if decimal_precision < 1:
        raise ValueError("decimal_precision must be positive")
    if not certificate.is_complete:
        raise DomainError(certificate.reason or _pbo_reason("PBO certificate is incomplete"))
    certificate_matrix_digest = certificate.matrix_digest
    if certificate_matrix_digest is None:
        raise DomainError(_pbo_reason("passing PBO certificate has no matrix digest"))
    if (
        matrix.profile_hash != certificate.profile_hash
        or matrix.fold_ids != certificate.expected_fold_ids
        or matrix.trial_ids != certificate.matrix_trial_ids
        or matrix.score_direction is not certificate.score_direction
        or matrix.certificate_hash != certificate.certificate_hash
        or matrix.matrix_digest != certificate.matrix_digest
        or matrix.matrix_hash != certificate.matrix_digest
    ):
        raise DomainError(_pbo_reason("matrix is not bound to the passing PBO certificate"))
    partitions = construct_cscv_partitions(matrix.fold_ids)
    outcomes: list[CscvOutcome] = []
    trial_count = len(matrix.trial_ids)
    for partition in partitions:
        in_sample_means = {
            trial_id: _mean(
                tuple(matrix.score(trial_id, fold_id) for fold_id in partition.in_sample_fold_ids),
                decimal_precision,
            )
            for trial_id in matrix.trial_ids
        }
        selected_trial_id = _best_trial(in_sample_means, matrix.score_direction)
        out_of_sample_means = {
            trial_id: _mean(
                tuple(
                    matrix.score(trial_id, fold_id) for fold_id in partition.out_of_sample_fold_ids
                ),
                decimal_precision,
            )
            for trial_id in matrix.trial_ids
        }
        ordered_worst_to_best = _worst_to_best(out_of_sample_means, matrix.score_direction)
        rank = ordered_worst_to_best.index(selected_trial_id) + 1
        with localcontext(_decimal_context(decimal_precision)) as context:
            relative_rank = Decimal(rank) / Decimal(trial_count + 1)
            logit = relative_rank.ln(context=context) - (Decimal(1) - relative_rank).ln(
                context=context
            )
        outcomes.append(
            CscvOutcome(
                partition=partition,
                selected_trial_id=selected_trial_id,
                in_sample_score=in_sample_means[selected_trial_id],
                out_of_sample_score=out_of_sample_means[selected_trial_id],
                out_of_sample_rank=rank,
                relative_rank=relative_rank,
                logit=logit,
                overfit=logit < Decimal(0),
            )
        )
    overfit_count = sum(outcome.overfit for outcome in outcomes)
    with localcontext(_decimal_context(decimal_precision)):
        probability = Decimal(overfit_count) / Decimal(len(outcomes))
    return PboResult(
        lineage_id=certificate.lineage_id,
        validation_id=certificate.validation_id,
        profile_hash=matrix.profile_hash,
        search_run_hash=certificate.search_run_hash,
        certificate_hash=certificate.certificate_hash,
        matrix_digest=certificate_matrix_digest,
        decimal_precision=decimal_precision,
        partitions=tuple(outcomes),
        overfit_count=overfit_count,
        partition_count=len(outcomes),
        probability_of_backtest_overfitting=probability,
    )


def _profile_gate_evidence_error(profile: ValidationProfile, trial: CandidateTrial) -> str | None:
    """Return the frozen-profile mismatch that bars a terminal from PBO eligibility."""
    evaluation = trial.evaluation
    if evaluation is None:
        return "terminal trial has no evaluation"
    if evaluation.terminal is not trial.terminal:
        return "evaluation terminal does not match the trial terminal"
    metric_values: dict[str, Decimal] = {}
    for metric in evaluation.metrics:
        if metric.name in metric_values:
            return f"evaluation metric {metric.name!r} is duplicated"
        if not isinstance(metric.value, Decimal) or not metric.value.is_finite():
            return f"evaluation metric {metric.name!r} is not a finite Decimal"
        metric_values[metric.name] = metric.value
    if trial.terminal is CandidateTrialTerminal.EVALUATED:
        expected_gates = profile.hard_gates
        expected_decisions = (Decision.PASS,) * len(expected_gates)
    elif trial.terminal is CandidateTrialTerminal.REJECTED_HARD_GATE:
        if not evaluation.gate_results:
            return "hard-gate rejection has no gate evidence"
        if len(evaluation.gate_results) > len(profile.hard_gates):
            return "hard-gate evidence exceeds the frozen profile gate sequence"
        expected_gates = profile.hard_gates[: len(evaluation.gate_results)]
        expected_decisions = (Decision.PASS,) * (len(expected_gates) - 1) + (Decision.FAIL,)
    else:
        return None
    if len(evaluation.gate_results) != len(expected_gates):
        return "required frozen-profile gate IDs are incomplete"
    for gate, result, expected_decision in zip(
        expected_gates, evaluation.gate_results, expected_decisions, strict=True
    ):
        result_error = _gate_result_evidence_error(gate, result)
        if result_error is not None:
            return result_error
        if trial.terminal is CandidateTrialTerminal.EVALUATED:
            metric_value = metric_values.get(gate.metric_name)
            if metric_value is None:
                return f"gate {gate.gate_id!r} metric is absent from evaluation metrics"
            if metric_value != result.observed_value:
                return f"gate {gate.gate_id!r} observed value differs from evaluation metrics"
        if result.decision is not expected_decision:
            return (
                f"gate {gate.gate_id!r} observed decision does not match "
                f"the {expected_decision.value} terminal evidence"
            )
    return None


def _gate_result_evidence_error(gate: ValidationGate, result: object) -> str | None:
    """Return whether one gate result exactly proves its frozen gate decision."""
    if not isinstance(result, GateResult):
        return "gate evidence is not a typed GateResult"
    if (
        result.gate_id != gate.gate_id
        or result.metric_name != gate.metric_name
        or result.threshold != gate.threshold
        or result.comparison is not gate.comparison
    ):
        return f"gate {gate.gate_id!r} identity, metric, threshold, or comparison differs"
    if not isinstance(result.observed_value, Decimal) or not result.observed_value.is_finite():
        return f"gate {gate.gate_id!r} observed value is not a finite Decimal"
    observed_decision = (
        Decision.PASS
        if _comparison_passes(gate.comparison, result.observed_value, gate.threshold)
        else Decision.FAIL
    )
    if result.decision is not observed_decision:
        return f"gate {gate.gate_id!r} observed decision is inconsistent with its value"
    if observed_decision is Decision.PASS and result.reason is not None:
        return f"gate {gate.gate_id!r} passing evidence has a failure reason"
    if observed_decision is Decision.FAIL and result.reason is None:
        return f"gate {gate.gate_id!r} failed evidence has no typed reason"
    return None


def _comparison_passes(comparison: GateComparison, value: Decimal, threshold: Decimal) -> bool:
    if comparison is GateComparison.LT:
        return value < threshold
    if comparison is GateComparison.LTE:
        return value <= threshold
    if comparison is GateComparison.GT:
        return value > threshold
    if comparison is GateComparison.GTE:
        return value >= threshold
    if comparison is GateComparison.EQ:
        return value == threshold
    raise ValueError(f"unsupported gate comparison: {comparison}")


def _matrix_coverage(
    scores: tuple[TrialFoldScore, ...], violations: list[str]
) -> tuple[tuple[str, ...], tuple[FoldCoverage, ...]]:
    folds_by_trial: dict[str, list[str]] = {}
    pairs: set[tuple[str, str]] = set()
    for score in scores:
        if not isinstance(score.trial_id, str) or not score.trial_id:
            violations.append("matrix contains an invalid trial ID")
            continue
        if not isinstance(score.fold_id, str) or not score.fold_id:
            violations.append("matrix contains an invalid fold ID")
            continue
        pair = (score.trial_id, score.fold_id)
        if pair in pairs:
            violations.append("matrix contains a duplicate trial/fold score")
            continue
        pairs.add(pair)
        folds_by_trial.setdefault(score.trial_id, []).append(score.fold_id)
    matrix_ids = tuple(sorted(folds_by_trial))
    coverage = tuple(
        FoldCoverage(trial_id=trial_id, observed_fold_ids=tuple(sorted(fold_ids)))
        for trial_id, fold_ids in sorted(folds_by_trial.items())
    )
    return matrix_ids, coverage


def _certificate(
    *,
    lineage_id: str,
    validation_id: str,
    search_run_hash: str,
    profile: ValidationProfile,
    score_direction: RankingDirection,
    matrix_digest: str | None,
    violations: list[str],
    started: tuple[str, ...],
    terminal_ids: tuple[str, ...],
    eligible: tuple[str, ...],
    matrix_ids: tuple[str, ...],
    coverage: tuple[FoldCoverage, ...],
    normal_stop: bool,
) -> PboCompletenessCertificate:
    if violations:
        details = tuple(
            ErrorField(path=f"pbo[{index}]", reason=message)
            for index, message in enumerate(dict.fromkeys(violations))
        )
        reason = ErrorDetail.for_code(
            code=ErrorCode.PBO_INCOMPLETE,
            message="PBO input is incomplete",
            details=details,
        )
        decision = PboCertificateDecision.FAIL
    else:
        reason = None
        decision = PboCertificateDecision.PASS
    return PboCompletenessCertificate(
        lineage_id=lineage_id,
        validation_id=validation_id,
        profile_hash=profile.profile_hash,
        search_run_hash=search_run_hash,
        score_direction=score_direction,
        matrix_digest=matrix_digest,
        decision=decision,
        reason=reason,
        started_trial_ids=tuple(sorted(started)),
        terminal_trial_ids=tuple(sorted(terminal_ids)),
        eligible_trial_ids=tuple(sorted(eligible)),
        matrix_trial_ids=matrix_ids,
        expected_fold_ids=profile.fold_ids,
        fold_coverage=coverage,
        normal_stop=normal_stop,
        minimum_eligible=profile.pbo_minimum_eligible,
    )


def _matrix_digest(
    *,
    profile_hash: str,
    fold_ids: tuple[str, ...],
    trial_ids: tuple[str, ...],
    score_direction: RankingDirection,
    scores: tuple[TrialFoldScore, ...],
) -> str:
    _require_digest(profile_hash, "profile_hash")
    if not isinstance(score_direction, RankingDirection):
        raise ValueError("score_direction must be a RankingDirection")
    if any(not isinstance(score.score, Decimal) or not score.score.is_finite() for score in scores):
        raise ValueError("matrix scores must be finite Decimals")
    return digest(
        "AF:PBO:MATRIX:1",
        {
            "fold_ids": list(fold_ids),
            "profile_hash": digest_bytes(profile_hash),
            "score_direction": score_direction.value,
            "scores": [
                {
                    "fold_id": score.fold_id,
                    "score": score.score,
                    "trial_id": score.trial_id,
                }
                for score in sorted(scores, key=lambda item: (item.trial_id, item.fold_id))
            ],
            "trial_ids": list(trial_ids),
        },
    )


def _search_run_hash(search_run: SearchRunResult) -> str:
    """Digest the full completed ledger supplied to the PBO certificate."""
    return digest("AF:PBO:SEARCH_RUN:1", {"search_run": search_run.model_dump(mode="json")})


def _pbo_reason(message: str) -> ErrorDetail:
    return ErrorDetail.for_code(ErrorCode.PBO_INCOMPLETE, message)


def _decimal_context(decimal_precision: int) -> Context:
    if (
        not isinstance(decimal_precision, int)
        or isinstance(decimal_precision, bool)
        or not 1 <= decimal_precision <= _PBO_MAX_PRECISION
    ):
        raise ValueError("decimal_precision must be positive")
    return Context(
        prec=decimal_precision,
        rounding=ROUND_HALF_EVEN,
        Emin=_PBO_MIN_EMIN,
        Emax=_PBO_MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[],
    )


def _mean(values: tuple[Decimal, ...], decimal_precision: int) -> Decimal:
    if not values:
        raise ValueError("a CSCV mean requires at least one score")
    with localcontext(_decimal_context(decimal_precision)):
        return sum(values, Decimal(0)) / Decimal(len(values))


def _best_trial(scores: dict[str, Decimal], direction: RankingDirection) -> str:
    if direction is RankingDirection.DESC:
        return min(scores, key=lambda trial_id: (-scores[trial_id], trial_id))
    return min(scores, key=lambda trial_id: (scores[trial_id], trial_id))


def _worst_to_best(scores: dict[str, Decimal], direction: RankingDirection) -> tuple[str, ...]:
    if direction is RankingDirection.DESC:
        return tuple(sorted(scores, key=lambda trial_id: (scores[trial_id], trial_id)))
    return tuple(sorted(scores, key=lambda trial_id: (-scores[trial_id], trial_id)))


def _record_duplicates(values: tuple[str, ...], label: str, violations: list[str]) -> None:
    if len(values) != len(set(values)):
        violations.append(f"{label} contain duplicates")


def _record_invalid_identifiers(values: tuple[str, ...], label: str, violations: list[str]) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        violations.append(f"{label} contain an invalid identifier")


def _require_digest(value: str, label: str) -> None:
    try:
        digest_bytes(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest") from error


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
