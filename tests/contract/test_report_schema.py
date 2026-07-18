from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from decimal import Decimal

from alpha_foundry.domain.models import Decision, DisclosedMetric, Domain, ResearchReport
from alpha_foundry.reporting import (
    canonical_research_report_json,
    parse_canonical_research_report,
    render_research_report_html,
)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _report(*, strategy_id: str = "factor-strategy") -> ResearchReport:
    return ResearchReport(
        publication_id="publication-1",
        strategy_id=strategy_id,
        domain=Domain.FACTOR,
        evidence_ids=("validation-1", "pbo-certificate-1"),
        provider_attempt_ids=("provider-attempt-1",),
        dataset_hashes=(_digest(1),),
        config_hash=_digest(2),
        code_hash=_digest(3),
        schema_hashes=(_digest(4),),
        lineage_id="lineage-1",
        validation_decision=Decision.PASS,
        disclosed_metrics=(
            DisclosedMetric(
                name="sharpe",
                value=Decimal("1.250"),
                threshold=Decimal("1.00"),
                decision=Decision.PASS,
            ),
        ),
        artifact_hashes=(_digest(5),),
        created_at=datetime(2026, 7, 12, 9, 30, 1, tzinfo=UTC),
    )


def test_ct_report_001_canonical_report_schema_requires_complete_public_provenance() -> None:
    report = _report()
    required_fields = {
        "publication_id",
        "strategy_id",
        "domain",
        "evidence_ids",
        "provider_attempt_ids",
        "dataset_hashes",
        "config_hash",
        "code_hash",
        "schema_hashes",
        "lineage_id",
        "validation_decision",
        "disclosed_metrics",
        "artifact_hashes",
        "created_at",
    }

    schema = ResearchReport.model_json_schema()
    assert set(schema["required"]) == required_fields

    payload = canonical_research_report_json(report)
    assert canonical_research_report_json(report) == payload
    document = json.loads(payload)
    assert set(document) == required_fields
    assert required_fields <= set(document)
    assert document["created_at"] == "2026-07-12T09:30:01.000000Z"
    assert document["disclosed_metrics"] == [
        {"decision": "PASS", "name": "sharpe", "threshold": "1", "value": "1.25"}
    ]
    assert payload.decode("utf-8") == json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert parse_canonical_research_report(payload) == report


def test_ct_report_002_html_is_deterministic_and_escapes_only_canonical_json() -> None:
    report = _report(strategy_id="factor-<script>alert('x')</script>&\"")
    canonical_json = canonical_research_report_json(report)

    first = render_research_report_html(report)
    second = render_research_report_html(report)

    assert first == second
    assert b"<script>" not in first
    assert b"&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;&amp;\\&quot;" in first
    assert (
        first
        == (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            "<title>Alpha Foundry Research Report</title>\n"
            "</head>\n"
            "<body>\n"
            "<h1>Alpha Foundry Research Report</h1>\n"
            f"<pre>{html.escape(canonical_json.decode('utf-8'), quote=True)}</pre>\n"
            "</body>\n"
            "</html>\n"
        ).encode()
    )
