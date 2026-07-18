from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alpha_foundry.validation.walkforward import (
    TimedObservation,
    TimeWindow,
    WalkForwardSplit,
    assert_split_has_no_leakage,
)


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2025, 2, day, hour, tzinfo=UTC)


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


def _split(train_observation_id: str) -> WalkForwardSplit:
    return WalkForwardSplit(
        fold_id="fold-1",
        train_observation_ids=(train_observation_id,),
        test_observation_ids=("test",),
        purged_observation_ids=(),
        embargoed_observation_ids=(),
        test_window=TimeWindow(start=_at(5), end=_at(6)),
        embargo_window=None,
    )


def test_leakage_guard_rejects_future_observations_in_training() -> None:
    test = _observation("test", anchor_day=5, window_start_day=5, window_end_day=6)
    future = _observation("future", anchor_day=6, window_start_day=6, window_end_day=7)

    with pytest.raises(ValueError, match="must precede the test window"):
        assert_split_has_no_leakage(_split("future"), (test, future))


def test_leakage_guard_rejects_historical_rows_with_test_information_overlap() -> None:
    test = _observation("test", anchor_day=5, window_start_day=5, window_end_day=6)
    overlapping_history = _observation(
        "overlapping-history",
        anchor_day=4,
        anchor_hour=12,
        window_start_day=4,
        window_end_day=6,
    )

    with pytest.raises(ValueError, match="training information overlaps"):
        assert_split_has_no_leakage(_split("overlapping-history"), (test, overlapping_history))
