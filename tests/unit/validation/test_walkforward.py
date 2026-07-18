from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alpha_foundry.domain.models import (
    Domain,
    GateComparison,
    RankingDirection,
    RankingRule,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.validation.walkforward import (
    TimedObservation,
    TimeWindow,
    WalkForwardFold,
    WalkForwardSplit,
    assert_split_has_no_leakage,
    construct_profile_walk_forward_splits,
    construct_walk_forward_splits,
)


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2025, 1, day, hour, tzinfo=UTC)


def _observation(
    observation_id: str,
    *,
    anchor_day: int,
    anchor_hour: int = 0,
    window_start_day: int,
    window_start_hour: int = 0,
    window_end_day: int,
    window_end_hour: int = 0,
) -> TimedObservation:
    return TimedObservation(
        observation_id=observation_id,
        anchor_time=_at(anchor_day, anchor_hour),
        information_window=TimeWindow(
            start=_at(window_start_day, window_start_hour),
            end=_at(window_end_day, window_end_hour),
        ),
    )


def _profile(fold_ids: tuple[str, ...]) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile-1",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash="sha256:" + "1" * 64,
        hard_gates=(
            ValidationGate(
                gate_id="sharpe-gate",
                metric_name="sharpe",
                comparison=GateComparison.GTE,
                threshold=Decimal("1"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="sharpe",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=fold_ids,
        pbo_minimum_eligible=1,
        patience=1,
        holdout_policy_hash="sha256:" + "2" * 64,
        disclosure_policy_hash="sha256:" + "3" * 64,
        allowed_disclosure_fields=("sharpe",),
    )


def _split(
    *,
    train: tuple[str, ...] = (),
    test: tuple[str, ...] = ("test",),
    purged: tuple[str, ...] = (),
    embargoed: tuple[str, ...] = (),
    embargo_window: TimeWindow | None = None,
) -> WalkForwardSplit:
    return WalkForwardSplit(
        fold_id="fold-1",
        train_observation_ids=train,
        test_observation_ids=test,
        purged_observation_ids=purged,
        embargoed_observation_ids=embargoed,
        test_window=TimeWindow(start=_at(5), end=_at(6)),
        embargo_window=embargo_window,
    )


def test_construct_walk_forward_split_purges_information_overlap_and_embargoes_boundaries() -> None:
    observations = (
        _observation(
            "safe-history",
            anchor_day=1,
            anchor_hour=12,
            window_start_day=1,
            window_end_day=2,
        ),
        _observation(
            "touching-history",
            anchor_day=3,
            anchor_hour=12,
            window_start_day=3,
            window_end_day=4,
        ),
        _observation(
            "overlapping-history",
            anchor_day=4,
            anchor_hour=6,
            window_start_day=4,
            window_end_day=4,
            window_end_hour=12,
        ),
        _observation(
            "test",
            anchor_day=5,
            anchor_hour=12,
            window_start_day=4,
            window_end_day=6,
        ),
        _observation(
            "embargo-start",
            anchor_day=6,
            window_start_day=6,
            window_end_day=7,
        ),
        _observation(
            "embargo-middle",
            anchor_day=7,
            anchor_hour=12,
            window_start_day=7,
            window_end_day=8,
        ),
        _observation(
            "embargo-end",
            anchor_day=8,
            window_start_day=8,
            window_end_day=9,
        ),
        _observation(
            "future",
            anchor_day=9,
            window_start_day=9,
            window_end_day=10,
        ),
    )
    fold = WalkForwardFold(
        fold_id="fold-1",
        test_window=TimeWindow(start=_at(5), end=_at(6)),
        embargo=timedelta(days=2),
    )

    (split,) = construct_walk_forward_splits(reversed(observations), (fold,))

    assert split.train_observation_ids == ("safe-history", "touching-history")
    assert split.test_observation_ids == ("test",)
    assert split.purged_observation_ids == ("overlapping-history",)
    assert split.embargoed_observation_ids == ("embargo-start", "embargo-middle")
    assert split.embargo_window == TimeWindow(start=_at(6), end=_at(8))
    assert "future" not in split.train_observation_ids
    assert "embargo-end" not in split.embargoed_observation_ids


def test_construct_walk_forward_split_is_deterministic_for_arrival_order() -> None:
    observations = (
        _observation("history", anchor_day=1, window_start_day=1, window_end_day=2),
        _observation("test-a", anchor_day=5, window_start_day=5, window_end_day=6),
        _observation("test-b", anchor_day=5, anchor_hour=12, window_start_day=5, window_end_day=6),
    )
    fold = WalkForwardFold(
        fold_id="fold-1",
        test_window=TimeWindow(start=_at(5), end=_at(6)),
        embargo=timedelta(0),
    )

    first = construct_walk_forward_splits(observations, (fold,))
    second = construct_walk_forward_splits(tuple(reversed(observations)), (fold,))

    assert first == second
    assert first[0].test_observation_ids == ("test-a", "test-b")


def test_construct_walk_forward_split_rejects_a_fold_without_test_samples() -> None:
    observations = (_observation("history", anchor_day=1, window_start_day=1, window_end_day=2),)
    fold = WalkForwardFold(
        fold_id="fold-1",
        test_window=TimeWindow(start=_at(5), end=_at(6)),
        embargo=timedelta(0),
    )

    with pytest.raises(ValueError, match="has no test observations"):
        construct_walk_forward_splits(observations, (fold,))


def test_walk_forward_time_contracts_reject_naive_or_invalid_values_at_boundaries() -> None:
    window = TimeWindow(start=_at(5), end=_at(6))

    assert window.contains(_at(5))
    assert not window.contains(_at(6))
    assert not window.overlaps(TimeWindow(start=_at(6), end=_at(7)))

    with pytest.raises(ValueError, match="window start must be timezone-aware"):
        TimeWindow(start=datetime(2025, 1, 5), end=_at(6))
    with pytest.raises(ValueError, match="window end must be timezone-aware"):
        TimeWindow(start=_at(5), end=datetime(2025, 1, 6))
    with pytest.raises(ValueError, match="start before end"):
        TimeWindow(start=_at(6), end=_at(6))
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        window.contains(datetime(2025, 1, 5))
    with pytest.raises(ValueError, match="anchor_time must lie inside"):
        TimedObservation(
            observation_id="outside",
            anchor_time=_at(6),
            information_window=window,
        )
    with pytest.raises(ValueError, match="embargo must not be negative"):
        WalkForwardFold(
            fold_id="fold-1",
            test_window=window,
            embargo=timedelta(days=-1),
        )


@pytest.mark.parametrize(
    ("folds", "message"),
    [
        ((), "at least one walk-forward fold"),
        (
            (
                WalkForwardFold(
                    fold_id="fold-2",
                    test_window=TimeWindow(start=_at(7), end=_at(8)),
                    embargo=timedelta(0),
                ),
                WalkForwardFold(
                    fold_id="fold-1",
                    test_window=TimeWindow(start=_at(5), end=_at(6)),
                    embargo=timedelta(0),
                ),
            ),
            "supplied in chronological order",
        ),
        (
            (
                WalkForwardFold(
                    fold_id="fold-1",
                    test_window=TimeWindow(start=_at(5), end=_at(6)),
                    embargo=timedelta(0),
                ),
                WalkForwardFold(
                    fold_id="fold-1",
                    test_window=TimeWindow(start=_at(7), end=_at(8)),
                    embargo=timedelta(0),
                ),
            ),
            "fold IDs must be unique",
        ),
        (
            (
                WalkForwardFold(
                    fold_id="fold-1",
                    test_window=TimeWindow(start=_at(5), end=_at(7)),
                    embargo=timedelta(0),
                ),
                WalkForwardFold(
                    fold_id="fold-2",
                    test_window=TimeWindow(start=_at(6), end=_at(8)),
                    embargo=timedelta(0),
                ),
            ),
            "test windows must not overlap",
        ),
    ],
)
def test_construct_walk_forward_splits_rejects_invalid_fold_sequences(
    folds: tuple[WalkForwardFold, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        construct_walk_forward_splits((), folds)


def test_construct_profile_walk_forward_splits_requires_the_frozen_fold_sequence() -> None:
    folds = (
        WalkForwardFold(
            fold_id="fold-1",
            test_window=TimeWindow(start=_at(5), end=_at(6)),
            embargo=timedelta(0),
        ),
        WalkForwardFold(
            fold_id="fold-2",
            test_window=TimeWindow(start=_at(7), end=_at(8)),
            embargo=timedelta(0),
        ),
    )
    observations = (
        _observation("test-1", anchor_day=5, window_start_day=5, window_end_day=6),
        _observation("test-2", anchor_day=7, window_start_day=7, window_end_day=8),
    )

    splits = construct_profile_walk_forward_splits(
        profile=_profile(("fold-1", "fold-2")),
        observations=observations,
        folds=folds,
    )

    assert tuple(split.fold_id for split in splits) == ("fold-1", "fold-2")
    with pytest.raises(ValueError, match="exactly match"):
        construct_profile_walk_forward_splits(
            profile=_profile(("fold-2", "fold-1")),
            observations=observations,
            folds=folds,
        )


@pytest.mark.parametrize(
    ("split", "message"),
    [
        (_split(train=("missing",)), "references unknown observations"),
        (_split(test=()), "must contain at least one test observation"),
        (_split(test=("history",)), "test observation anchor lies outside"),
        (_split(train=("future",)), "training observations must precede"),
        (_split(train=("overlapping-history",)), "training information overlaps"),
        (_split(purged=("history",)), "purged observation must overlap"),
        (_split(purged=("future",)), "only historical observations may be purged"),
        (
            _split(
                train=("history",),
                embargoed=("history",),
                embargo_window=TimeWindow(start=_at(6), end=_at(7)),
            ),
            "embargoed observation cannot be used for training",
        ),
        (
            _split(
                embargoed=("history",),
                embargo_window=TimeWindow(start=_at(6), end=_at(7)),
            ),
            "embargoed observation lies outside",
        ),
    ],
)
def test_assert_split_has_no_leakage_rejects_tampered_split_membership(
    split: WalkForwardSplit, message: str
) -> None:
    observations = (
        _observation("history", anchor_day=1, window_start_day=1, window_end_day=2),
        _observation(
            "overlapping-history",
            anchor_day=4,
            window_start_day=4,
            window_end_day=6,
        ),
        _observation("test", anchor_day=5, window_start_day=5, window_end_day=6),
        _observation("future", anchor_day=6, window_start_day=6, window_end_day=7),
    )

    with pytest.raises(ValueError, match=message):
        assert_split_has_no_leakage(split, observations)


def test_walk_forward_split_rejects_duplicate_or_conflicting_membership() -> None:
    with pytest.raises(ValueError, match="train observation IDs must be unique"):
        _split(train=("history", "history"))
    with pytest.raises(ValueError, match="cannot train and test"):
        _split(train=("history",), test=("history",))
    with pytest.raises(ValueError, match="test observations cannot be marked as purged"):
        _split(purged=("test",))
    with pytest.raises(ValueError, match="test observations cannot be marked as embargoed"):
        _split(embargoed=("test",))

    duplicate = _observation("test", anchor_day=5, window_start_day=5, window_end_day=6)
    with pytest.raises(ValueError, match="observation IDs must be unique"):
        construct_walk_forward_splits(
            (duplicate, duplicate),
            (
                WalkForwardFold(
                    fold_id="fold-1",
                    test_window=TimeWindow(start=_at(5), end=_at(6)),
                    embargo=timedelta(0),
                ),
            ),
        )
