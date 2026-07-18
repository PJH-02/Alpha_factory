"""Shared validation helpers for lab payload models."""

from __future__ import annotations

from datetime import datetime

from alpha_foundry.domain.models import _require_unique as require_unique
from alpha_foundry.domain.models import _utc_timestamp as require_utc_timestamp


def require_strictly_increasing(values: tuple[datetime, ...], label: str) -> None:
    if any(current <= previous for previous, current in zip(values[:-1], values[1:], strict=True)):
        raise ValueError(f"{label} must be strictly increasing")


def require_matrix_shape(
    values: tuple[tuple[object, ...], ...], rows: int, columns: int, label: str
) -> None:
    if len(values) != rows:
        raise ValueError(f"{label} must contain exactly {rows} rows")
    if any(len(row) != columns for row in values):
        raise ValueError(f"{label} rows must contain exactly {columns} values")


__all__ = [
    "require_matrix_shape",
    "require_strictly_increasing",
    "require_unique",
    "require_utc_timestamp",
]
