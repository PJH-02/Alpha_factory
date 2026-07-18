from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from alpha_foundry.domain import Domain
from alpha_foundry.knowledge import Citation, Claim, ClaimPin, KnowledgePack


def _claim(identifier: str, version: str, statement: str) -> Claim:
    return Claim(
        id=identifier,
        version=version,
        domain=Domain.FACTOR,
        statement=statement,
        source="research-note",
        citations=(
            Citation(
                source_id="source-1",
                locator="section-1",
                excerpt=f"evidence for {identifier}",
            ),
        ),
    )


@given(st.permutations((0, 1)))
def test_knowledge_pack_hash_is_order_independent_and_claims_are_frozen(
    order: tuple[int, int],
) -> None:
    claims = (
        _claim("claim-alpha", "1.0.0", "Alpha evidence is stable."),
        _claim("claim-beta", "2.0.0", "Beta evidence is stable."),
    )
    pack = KnowledgePack(
        id="factor-pack",
        version="1.0.0",
        claims=tuple(claims[index] for index in order),
    )
    canonical_pack = KnowledgePack(id="factor-pack", version="1.0.0", claims=claims)

    assert pack.content_hash == canonical_pack.content_hash
    with pytest.raises(ValidationError):
        claims[0].statement = "mutated evidence"  # type: ignore[misc]


def test_knowledge_hash_pins_exact_claim_revision_and_rejects_stale_hashes() -> None:
    first_revision = _claim("claim-alpha", "1.0.0", "The first revision.")
    second_revision = _claim("claim-alpha", "1.1.0", "The revised statement.")
    pack = KnowledgePack(
        id="factor-pack",
        version="1.1.0",
        claims=(first_revision, second_revision),
    )

    assert first_revision.content_hash != second_revision.content_hash
    assert (
        KnowledgePack(id="factor-pack", version="1.0.0", claims=(first_revision,)).content_hash
        != pack.content_hash
    )
    assert pack.resolve_pins((first_revision.pin(), second_revision.pin())) == (
        first_revision,
        second_revision,
    )

    stale_pin = ClaimPin(
        id=first_revision.id,
        version=first_revision.version,
        hash=second_revision.content_hash,
    )
    with pytest.raises(ValueError, match="does not match"):
        pack.resolve_pins((stale_pin,))


def test_knowledge_pack_rejects_duplicate_claim_revisions_and_invalid_versions() -> None:
    claim = _claim("claim-alpha", "1.0.0", "One revision.")

    with pytest.raises(ValueError, match="duplicate claim ID/version"):
        KnowledgePack(id="factor-pack", version="1.0.0", claims=(claim, claim))
    with pytest.raises(ValueError, match="semantic version"):
        KnowledgePack(id="factor-pack", version="latest", claims=(claim,))
