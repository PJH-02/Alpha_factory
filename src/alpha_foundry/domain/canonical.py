"""Deterministic AF-CANON encoding and digest helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Final
from unicodedata import normalize

_NULL: Final = 0
_FALSE: Final = 1
_TRUE: Final = 2
_STRING: Final = 3
_INTEGER: Final = 4
_DECIMAL: Final = 5
_BYTES: Final = 6
_LIST: Final = 7
_OBJECT: Final = 8
_TIMESTAMP_V1: Final = 9

_MAX_SCALAR_TEXT_BYTES: Final = 4096


class CanonicalizationError(ValueError):
    """Raised when a value has no unambiguous AF-CANON representation."""


def canonical_bytes(value: object) -> bytes:
    """Return the self-delimiting AF-CANON encoding for *value*.

    Object fields are sorted by their UTF-8 field names. Lists and tuples preserve
    their declared order. The returned value includes its type and length so it can
    be nested without implementation-defined framing.
    """

    return _encode_value(value)


def digest(domain: str, value: object) -> str:
    """Return the AF-CANON SHA-256 digest for a named-field object.

    Normative identities are named-field objects. Their digest input is
    ``domain_utf8 || 0x00 || field_count_u32be || encoded_fields``. This deliberately
    excludes the enclosing object type/length used by :func:`canonical_bytes`.
    """

    if not isinstance(domain, str):
        raise CanonicalizationError("domain separator must be a string")
    normalized_domain = _normalized_text(domain)
    if not normalized_domain:
        raise CanonicalizationError("domain separator must not be empty")
    if "\x00" in normalized_domain:
        raise CanonicalizationError("domain separator must not contain NUL")
    if not isinstance(value, Mapping):
        raise CanonicalizationError("AF-CANON digest values must be named-field objects")

    encoded_domain = normalized_domain.encode("utf-8")
    payload = _encode_object_payload(value)
    return f"sha256:{sha256(encoded_domain + b'\x00' + payload).hexdigest()}"


def _encode_value(value: object) -> bytes:
    tag, payload = _value_parts(value)
    return bytes((tag,)) + _u64(len(payload)) + payload


def _value_parts(value: object) -> tuple[int, bytes]:
    if value is None:
        return _NULL, b""
    if isinstance(value, bool):
        return (_TRUE if value else _FALSE), b""
    if isinstance(value, int):
        return _INTEGER, _normalized_integer(value).encode("ascii")
    if isinstance(value, Decimal):
        return _DECIMAL, _normalized_decimal(value).encode("ascii")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError("NaN and Infinity are forbidden in AF-CANON")
        raise CanonicalizationError("binary floating-point values are forbidden in AF-CANON")
    if isinstance(value, datetime):
        return _TIMESTAMP_V1, _normalized_timestamp(value).encode("ascii")
    if isinstance(value, str):
        return _STRING, _normalized_text(value).encode("utf-8")
    if isinstance(value, bytes):
        return _BYTES, value
    if isinstance(value, Mapping):
        return _OBJECT, _encode_object_payload(value)
    if isinstance(value, (list, tuple)):
        return _LIST, b"".join(_encode_value(item) for item in value)
    if isinstance(value, bytearray):
        raise CanonicalizationError("bytearray is mutable; use bytes in AF-CANON")
    raise CanonicalizationError(f"unsupported AF-CANON value type: {type(value).__name__}")


def _encode_object_payload(value: Mapping[object, object]) -> bytes:
    encoded_fields: list[tuple[bytes, bytes]] = []
    seen_names: set[str] = set()
    for name, field_value in value.items():
        if not isinstance(name, str):
            raise CanonicalizationError("AF-CANON object field names must be strings")
        if not name.isascii():
            raise CanonicalizationError("AF-CANON object field names must be ASCII")
        if not name:
            raise CanonicalizationError("AF-CANON object field names must not be empty")
        if name in seen_names:
            raise CanonicalizationError("AF-CANON object field names must be unique")
        seen_names.add(name)
        encoded_name = name.encode("ascii")
        if len(encoded_name) > 0xFFFF:
            raise CanonicalizationError("AF-CANON object field name is too long")
        encoded_fields.append((encoded_name, _encode_value(field_value)))

    encoded_fields.sort(key=lambda field: field[0])
    if len(encoded_fields) > 0xFFFFFFFF:
        raise CanonicalizationError("AF-CANON object has too many fields")

    body = bytearray(_u32(len(encoded_fields)))
    for encoded_name, encoded_value in encoded_fields:
        tag = encoded_value[0]
        payload = encoded_value[9:]
        body.extend(_u16(len(encoded_name)))
        body.extend(encoded_name)
        body.append(tag)
        body.extend(_u64(len(payload)))
        body.extend(payload)
    return bytes(body)


def _normalized_text(value: str) -> str:
    try:
        normalized = normalize("NFC", value)
        normalized.encode("utf-8")
    except (TypeError, UnicodeEncodeError) as error:
        raise CanonicalizationError("AF-CANON strings must be valid Unicode") from error
    return normalized


def _normalized_integer(value: int) -> str:
    maximum_digits = _maximum_decimal_digits(value)
    if maximum_digits + (1 if value < 0 else 0) > _MAX_SCALAR_TEXT_BYTES:
        raise CanonicalizationError("AF-CANON integers exceed the encoded-size limit")
    return str(value)


def _maximum_decimal_digits(value: int) -> int:
    bits = abs(value).bit_length()
    return max(1, (bits * 30103 + 99999) // 100000)


def _normalized_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CanonicalizationError("NaN and Infinity are forbidden in AF-CANON")
    if value.is_zero():
        return "0"

    adjusted = value.adjusted()
    if adjusted >= _MAX_SCALAR_TEXT_BYTES or adjusted <= -_MAX_SCALAR_TEXT_BYTES:
        raise CanonicalizationError("AF-CANON Decimals exceed the encoded-size limit")

    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise CanonicalizationError("finite decimals must have an integer exponent")
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1

    digit_count = end
    if exponent >= 0:
        encoded_length = digit_count + exponent
    else:
        decimal_point = digit_count + exponent
        encoded_length = (
            digit_count + 1 if decimal_point > 0 else 2 + (-decimal_point) + digit_count
        )
    if encoded_length + (1 if sign else 0) > _MAX_SCALAR_TEXT_BYTES:
        raise CanonicalizationError("AF-CANON Decimals exceed the encoded-size limit")

    text = "".join(str(digit) for digit in digits[:end])
    if exponent >= 0:
        normalized = text + ("0" * exponent)
    else:
        decimal_point = len(text) + exponent
        if decimal_point > 0:
            normalized = text[:decimal_point] + "." + text[decimal_point:]
        else:
            normalized = "0." + ("0" * -decimal_point) + text
    return ("-" if sign else "") + normalized


def _normalized_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalizationError("AF-CANON timestamps must be timezone-aware")
    utc_value = value.astimezone(UTC)
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}"
        f".{utc_value.microsecond:06d}Z"
    )


def _u16(value: int) -> bytes:
    return value.to_bytes(2, "big")


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "big")
