"""Canonical, public-only ResearchReport serialization and rendering."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from unicodedata import normalize

from alpha_foundry.domain.models import ResearchReport

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_JSON_MEDIA_TYPE = "application/json"
_HTML_MEDIA_TYPE = "text/html"


def canonical_research_report_document(report: ResearchReport) -> dict[str, JsonValue]:
    """Return the schema-validated JSON document used as the report authority."""
    document = _canonical_json_value(report.model_dump(mode="python", by_alias=True))
    if not isinstance(document, dict):  # Defensive: ResearchReport is object-shaped by contract.
        raise ValueError("ResearchReport must serialize to a JSON object")
    return document


def canonical_research_report_json(report: ResearchReport) -> bytes:
    """Serialize a report to deterministic UTF-8 JSON with no insignificant whitespace."""
    document = canonical_research_report_document(report)
    return json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def parse_canonical_research_report(payload: bytes) -> ResearchReport:
    """Parse and require byte-for-byte canonical ResearchReport JSON."""
    try:
        text = payload.decode("utf-8")
        decoded = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("report artifact is not valid UTF-8 JSON") from error
    if not isinstance(decoded, Mapping):
        raise ValueError("report artifact must be a JSON object")
    document = dict(decoded)
    metrics = document.get("disclosed_metrics")
    if isinstance(metrics, list):
        normalized_metrics: list[object] = []
        for metric in metrics:
            if not isinstance(metric, Mapping):
                normalized_metrics.append(metric)
                continue
            normalized_metric = dict(metric)
            for field_name in ("value", "threshold"):
                field_value = normalized_metric.get(field_name)
                if isinstance(field_value, str):
                    try:
                        normalized_metric[field_name] = Decimal(field_value)
                    except InvalidOperation:
                        pass
            normalized_metrics.append(normalized_metric)
        document["disclosed_metrics"] = normalized_metrics
    report = ResearchReport.model_validate(document)
    if canonical_research_report_json(report) != payload:
        raise ValueError("report artifact is not canonical ResearchReport JSON")
    return report


def render_research_report_html(report: ResearchReport) -> bytes:
    """Render deterministic HTML solely from canonical JSON with all content escaped."""
    canonical_json = canonical_research_report_json(report).decode("utf-8")
    escaped_json = html.escape(canonical_json, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Alpha Foundry Research Report</title>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Alpha Foundry Research Report</h1>\n"
        f"<pre>{escaped_json}</pre>\n"
        "</body>\n"
        "</html>\n"
    ).encode()


def _canonical_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("report timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, Mapping):
        document: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("report JSON object keys must be strings")
            normalized_key = normalize("NFC", key)
            if normalized_key in document:
                raise ValueError("report JSON object keys collide after NFC normalization")
            document[normalized_key] = _canonical_json_value(item)
        return document
    raise ValueError(f"unsupported ResearchReport JSON value: {type(value).__name__}")


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("report decimal values must be finite")
    if value.is_zero():
        return "0"

    sign, digits, decimal_exponent = value.as_tuple()
    exponent = int(decimal_exponent)
    while digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        decimal_point = len(coefficient) + exponent
        if decimal_point > 0:
            rendered = f"{coefficient[:decimal_point]}.{coefficient[decimal_point:]}"
        else:
            rendered = f"0.{'0' * -decimal_point}{coefficient}"
    return f"-{rendered}" if sign else rendered


__all__ = [
    "_HTML_MEDIA_TYPE",
    "_JSON_MEDIA_TYPE",
    "canonical_research_report_document",
    "canonical_research_report_json",
    "parse_canonical_research_report",
    "render_research_report_html",
]
