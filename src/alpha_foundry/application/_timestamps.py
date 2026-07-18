"""Shared UTC timestamp serialization for application persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from alpha_foundry.domain.models import _utc_timestamp


def format_utc_timestamp(value: datetime) -> str:
    return _utc_timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc_timestamp(value: str) -> datetime:
    normalized = value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(UTC)


__all__ = ["format_utc_timestamp", "parse_utc_timestamp"]
