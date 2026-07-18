"""Deterministic purged and embargoed walk-forward split construction.

All time intervals in this module are half-open: ``[start, end)``.  This makes a
sample ending exactly when another begins non-overlapping and prevents a
boundary value from belonging to two folds.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from alpha_foundry.domain.models import ValidationProfile


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A timezone-aware half-open interval used for information-leakage checks."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "window start")
        _require_aware(self.end, "window end")
        if self.start >= self.end:
            raise ValueError("a time window must have start before end")

    def contains(self, value: datetime) -> bool:
        """Return whether *value* belongs to this half-open interval."""
        _require_aware(value, "timestamp")
        return self.start <= value < self.end

    def overlaps(self, other: TimeWindow) -> bool:
        """Return whether two half-open intervals share any instant."""
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class TimedObservation:
    """One sample and the complete interval from which it can reveal information."""

    observation_id: str
    anchor_time: datetime
    information_window: TimeWindow

    def __post_init__(self) -> None:
        _require_identifier(self.observation_id, "observation_id")
        _require_aware(self.anchor_time, "anchor_time")
        if not self.information_window.contains(self.anchor_time):
            raise ValueError("anchor_time must lie inside the information window")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """The test period and explicit embargo duration for one frozen fold."""

    fold_id: str
    test_window: TimeWindow
    embargo: timedelta

    def __post_init__(self) -> None:
        _require_identifier(self.fold_id, "fold_id")
        if self.embargo < timedelta(0):
            raise ValueError("embargo must not be negative")

    @property
    def embargo_window(self) -> TimeWindow | None:
        """Return the post-test embargo interval, or ``None`` for zero duration."""
        if self.embargo == timedelta(0):
            return None
        return TimeWindow(self.test_window.end, self.test_window.end + self.embargo)


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    """An auditable, ID-only result of one purged/embargoed walk-forward fold."""

    fold_id: str
    train_observation_ids: tuple[str, ...]
    test_observation_ids: tuple[str, ...]
    purged_observation_ids: tuple[str, ...]
    embargoed_observation_ids: tuple[str, ...]
    test_window: TimeWindow
    embargo_window: TimeWindow | None

    def __post_init__(self) -> None:
        _require_identifier(self.fold_id, "fold_id")
        _require_unique(self.train_observation_ids, "train observation IDs")
        _require_unique(self.test_observation_ids, "test observation IDs")
        _require_unique(self.purged_observation_ids, "purged observation IDs")
        _require_unique(self.embargoed_observation_ids, "embargoed observation IDs")
        train = set(self.train_observation_ids)
        test = set(self.test_observation_ids)
        if train & test:
            raise ValueError("a walk-forward split cannot train and test the same observation")
        if test & set(self.purged_observation_ids):
            raise ValueError("test observations cannot be marked as purged")
        if test & set(self.embargoed_observation_ids):
            raise ValueError("test observations cannot be marked as embargoed")


def construct_walk_forward_splits(
    observations: Iterable[TimedObservation], folds: Iterable[WalkForwardFold]
) -> tuple[WalkForwardSplit, ...]:
    """Build deterministic chronological splits and verify their leakage invariants.

    A test observation has an anchor in the fold's test window.  Training is
    strictly historical (its anchor precedes the test start).  Historical rows
    whose information windows overlap any test information window are purged.
    The embargo is recorded and checked against all observations; in a strict
    walk-forward split it cannot enter training because training is historical.
    """
    ordered_observations = _ordered_observations(observations)
    ordered_folds = _ordered_folds(folds)
    result: list[WalkForwardSplit] = []
    for fold in ordered_folds:
        test_rows = tuple(
            observation
            for observation in ordered_observations
            if fold.test_window.contains(observation.anchor_time)
        )
        if not test_rows:
            raise ValueError(f"walk-forward fold {fold.fold_id!r} has no test observations")

        test_windows = tuple(row.information_window for row in test_rows)
        historical_rows = tuple(
            observation
            for observation in ordered_observations
            if observation.anchor_time < fold.test_window.start
        )
        purged_rows = tuple(
            observation
            for observation in historical_rows
            if any(observation.information_window.overlaps(window) for window in test_windows)
        )
        purged_ids = {row.observation_id for row in purged_rows}
        train_rows = tuple(
            observation
            for observation in historical_rows
            if observation.observation_id not in purged_ids
        )
        embargo_window = fold.embargo_window
        embargoed_rows = (
            tuple(
                observation
                for observation in ordered_observations
                if embargo_window is not None and embargo_window.contains(observation.anchor_time)
            )
            if embargo_window is not None
            else ()
        )
        split = WalkForwardSplit(
            fold_id=fold.fold_id,
            train_observation_ids=tuple(row.observation_id for row in train_rows),
            test_observation_ids=tuple(row.observation_id for row in test_rows),
            purged_observation_ids=tuple(row.observation_id for row in purged_rows),
            embargoed_observation_ids=tuple(row.observation_id for row in embargoed_rows),
            test_window=fold.test_window,
            embargo_window=embargo_window,
        )
        assert_split_has_no_leakage(split, ordered_observations)
        result.append(split)
    return tuple(result)


def construct_profile_walk_forward_splits(
    *,
    profile: ValidationProfile,
    observations: Iterable[TimedObservation],
    folds: Iterable[WalkForwardFold],
) -> tuple[WalkForwardSplit, ...]:
    """Construct splits only when their ordered IDs exactly match the frozen profile."""
    frozen_folds = tuple(folds)
    if tuple(fold.fold_id for fold in frozen_folds) != profile.fold_ids:
        raise ValueError("walk-forward fold IDs must exactly match the frozen validation profile")
    return construct_walk_forward_splits(observations, frozen_folds)


def assert_split_has_no_leakage(
    split: WalkForwardSplit, observations: Iterable[TimedObservation]
) -> None:
    """Raise ``ValueError`` unless a split is strictly historical and leak-free."""
    rows = _observation_map(observations)
    referenced_ids = (
        split.train_observation_ids
        + split.test_observation_ids
        + split.purged_observation_ids
        + split.embargoed_observation_ids
    )
    unknown_ids = sorted(set(referenced_ids) - set(rows))
    if unknown_ids:
        raise ValueError(f"split references unknown observations: {', '.join(unknown_ids)}")

    test_rows = tuple(rows[row_id] for row_id in split.test_observation_ids)
    if not test_rows:
        raise ValueError("a walk-forward split must contain at least one test observation")
    for observation in test_rows:
        if not split.test_window.contains(observation.anchor_time):
            raise ValueError("test observation anchor lies outside its test window")

    embargoed = set(split.embargoed_observation_ids)
    for row_id in split.train_observation_ids:
        observation = rows[row_id]
        if observation.anchor_time >= split.test_window.start:
            raise ValueError("walk-forward training observations must precede the test window")
        if any(
            observation.information_window.overlaps(test.information_window) for test in test_rows
        ):
            raise ValueError("training information overlaps a test information window")
        if row_id in embargoed:
            raise ValueError("an embargoed observation cannot be used for training")

    for row_id in split.purged_observation_ids:
        observation = rows[row_id]
        if observation.anchor_time >= split.test_window.start:
            raise ValueError(
                "only historical observations may be purged from walk-forward training"
            )
        if not any(
            observation.information_window.overlaps(test.information_window) for test in test_rows
        ):
            raise ValueError("a purged observation must overlap a test information window")

    if split.embargo_window is not None:
        for row_id in split.embargoed_observation_ids:
            if not split.embargo_window.contains(rows[row_id].anchor_time):
                raise ValueError("an embargoed observation lies outside the embargo window")


def _ordered_observations(observations: Iterable[TimedObservation]) -> tuple[TimedObservation, ...]:
    rows = tuple(observations)
    identifiers = tuple(row.observation_id for row in rows)
    _require_unique(identifiers, "observation IDs")
    return tuple(sorted(rows, key=lambda row: (row.anchor_time, row.observation_id)))


def _ordered_folds(folds: Iterable[WalkForwardFold]) -> tuple[WalkForwardFold, ...]:
    supplied = tuple(folds)
    if not supplied:
        raise ValueError("at least one walk-forward fold is required")
    _require_unique(tuple(fold.fold_id for fold in supplied), "fold IDs")
    ordered = tuple(sorted(supplied, key=lambda fold: (fold.test_window.start, fold.fold_id)))
    if supplied != ordered:
        raise ValueError("walk-forward folds must be supplied in chronological order")
    prior_end: datetime | None = None
    for fold in ordered:
        if prior_end is not None and fold.test_window.start < prior_end:
            raise ValueError("walk-forward test windows must not overlap")
        prior_end = fold.test_window.end
    return ordered


def _observation_map(observations: Iterable[TimedObservation]) -> dict[str, TimedObservation]:
    rows = tuple(observations)
    _require_unique(tuple(row.observation_id for row in rows), "observation IDs")
    return {row.observation_id: row for row in rows}


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
