"""Deterministic AF-CANON contract tests."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest
from hypothesis import given
from hypothesis import strategies as st

from alpha_foundry.domain import CanonicalizationError, canonical_bytes, digest

FIXED_OBJECT_ENCODING = bytes.fromhex(
    "080000000000000026000000020005616c7068610300000000000000026f6b00046265746104000000000000000137"
)


@given(
    st.dictionaries(
        keys=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
        values=st.integers(),
        min_size=2,
        max_size=8,
    )
)
def test_mapping_order_does_not_change_canonical_bytes_or_digest(
    fields: dict[str, int],
) -> None:
    reverse_insertion_order = dict(reversed(tuple(fields.items())))

    assert canonical_bytes(fields) == canonical_bytes(reverse_insertion_order)
    assert digest("AF:TEST:1", fields) == digest("AF:TEST:1", reverse_insertion_order)


def test_canonical_bytes_match_the_named_field_wire_vector() -> None:
    value = {"beta": 7, "alpha": "ok"}

    assert canonical_bytes(value) == FIXED_OBJECT_ENCODING
    assert digest("AF:TEST:1", value) == (
        f"sha256:{sha256(b'AF:TEST:1\x00' + FIXED_OBJECT_ENCODING[9:]).hexdigest()}"
    )


def test_text_decimal_and_timestamps_are_normalized() -> None:
    offset_timestamp = datetime(
        2024,
        1,
        2,
        8,
        34,
        5,
        6,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    utc_timestamp = datetime(2024, 1, 2, 3, 4, 5, 6, tzinfo=UTC)

    assert canonical_bytes("e\u0301") == canonical_bytes("\u00e9")
    assert digest("AF:CANON:e\u0301", {"name": "e\u0301"}) == digest(
        "AF:CANON:\u00e9", {"name": "\u00e9"}
    )
    assert canonical_bytes(Decimal("-0.000")) == canonical_bytes(Decimal("0"))
    assert canonical_bytes(Decimal("1.2300")) == canonical_bytes(Decimal("1.23"))
    assert canonical_bytes(offset_timestamp) == canonical_bytes(utc_timestamp)
    assert canonical_bytes(utc_timestamp) == (
        b"\x09" + (27).to_bytes(8, "big") + b"2024-01-02T03:04:05.000006Z"
    )


def test_list_order_is_semantic_for_canonical_bytes_and_digests() -> None:
    forward = ["first", "second"]
    reversed_order = list(reversed(forward))

    assert canonical_bytes(forward) != canonical_bytes(reversed_order)
    assert digest("AF:LIST:1", {"items": forward}) != digest("AF:LIST:1", {"items": reversed_order})


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")],
    ids=[
        "float-nan",
        "float-infinity",
        "float-negative-infinity",
        "decimal-nan",
        "decimal-infinity",
    ],
)
def test_nan_and_infinity_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError, match="NaN and Infinity"):
        canonical_bytes(value)

    with pytest.raises(CanonicalizationError, match="NaN and Infinity"):
        digest("AF:TEST:1", {"invalid": value})


class DuplicateFieldMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise KeyError(key)

    def __iter__(self):
        return iter(("field",))

    def __len__(self) -> int:
        return 1

    def items(self):
        return (("field", "first"), ("field", "second"))


@pytest.mark.parametrize(
    ("domain", "value", "message"),
    [
        (1, {}, "domain separator must be a string"),
        ("", {}, "domain separator must not be empty"),
        ("AF\x00CANON", {}, "domain separator must not contain NUL"),
        ("AF:CANON:1", ["not", "an", "object"], "must be named-field objects"),
    ],
)
def test_digest_requires_a_valid_domain_and_named_field_object(
    domain: object,
    value: object,
    message: str,
) -> None:
    with pytest.raises(CanonicalizationError, match=message):
        digest(domain, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({1: "value"}, "field names must be strings"),
        ({"é": "value"}, "field names must be ASCII"),
        ({"": "value"}, "field names must not be empty"),
    ],
)
def test_digest_rejects_invalid_named_field_keys(
    fields: Mapping[object, object],
    message: str,
) -> None:
    with pytest.raises(CanonicalizationError, match=message):
        digest("AF:CANON:1", fields)


def test_digest_rejects_duplicate_and_oversized_field_names() -> None:
    with pytest.raises(CanonicalizationError, match="field names must be unique"):
        digest("AF:CANON:1", DuplicateFieldMapping())

    with pytest.raises(CanonicalizationError, match="field name is too long"):
        digest("AF:CANON:1", {"a" * 65536: "value"})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (1.25, "binary floating-point values"),
        (bytearray(b"mutable"), "bytearray is mutable"),
        (datetime(2024, 1, 2, 3, 4, 5), "timestamps must be timezone-aware"),
        ("\ud800", "strings must be valid Unicode"),
        (object(), "unsupported AF-CANON value type"),
    ],
)
def test_canonical_bytes_rejects_ambiguous_value_shapes(value: object, message: str) -> None:
    with pytest.raises(CanonicalizationError, match=message):
        canonical_bytes(value)
    with pytest.raises(CanonicalizationError, match=message):
        digest("AF:CANON:1", {"value": value})


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        (Decimal("-1200E-2"), b"-12"),
        (Decimal("1.2300E-6"), b"0.00000123"),
    ],
)
def test_decimal_normalization_handles_signed_and_subunit_exponents(
    value: Decimal,
    normalized: bytes,
) -> None:
    assert canonical_bytes(value) == b"\x05" + len(normalized).to_bytes(8, "big") + normalized
