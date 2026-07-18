"""Deterministic traversal and direct-fallback helpers for finite universes."""

from __future__ import annotations

from collections.abc import Container

from .models import Candidate, Traversal, Universe, digest_bytes


def traversal_digest(candidate_hash: str, seed: int) -> str:
    """Return the exhaustive seed-dependent traversal digest for one candidate."""
    return Traversal(candidate_hash=candidate_hash, seed=seed).traversal_hash


def traversal_order(universe: Universe, seed: int) -> tuple[Candidate, ...]:
    """Order every universe candidate by traversal digest, then raw candidate digest."""
    return tuple(
        sorted(
            universe.candidates,
            key=lambda candidate: (
                digest_bytes(traversal_digest(candidate.candidate_hash, seed)),
                digest_bytes(candidate.candidate_hash),
            ),
        )
    )


def first_unseen_candidate(
    universe: Universe,
    seed: int,
    proposed: Container[str],
    visited: Container[str],
) -> Candidate | None:
    """Scan the deterministic traversal once and return its first run-unseen member."""
    for candidate in traversal_order(universe, seed):
        candidate_hash = candidate.candidate_hash
        if candidate_hash not in proposed and candidate_hash not in visited:
            return candidate
    return None
