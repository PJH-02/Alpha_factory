from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha_foundry.application.jobs import SystemClock
from alpha_foundry.bootstrap import ApplicationFacade
from alpha_foundry.cli import main
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore
from alpha_foundry.infrastructure.db import SQLiteStore
from alpha_foundry.infrastructure.llm import FakeProvider


@pytest.fixture
def facade(tmp_path: Path) -> ApplicationFacade:
    provider = FakeProvider(
        [
            {
                "title": "Alpha Paper Summary",
                "summary": "Condenses the factor paper into one grounded wiki page.",
                "body_markdown": "The paper argues for a ranked liquid-universe factor with explicit availability.",
                "key_people": ["Alice Researcher", "Bob PM"],
                "claims": [
                    {
                        "statement": "Rank a liquid universe using the reviewed factor signal.",
                        "citations": [
                            {
                                "locator": "section-1",
                                "excerpt": "The factor signal is available at the observation time.",
                            }
                        ],
                    }
                ],
            },
            {
                "ideas": [
                    {
                        "title": "Liquidity-Aware Factor Screen",
                        "summary": "Pairs the reviewed factor with a liquidity filter.",
                        "body_markdown": "Test whether the reviewed signal improves after excluding illiquid tails.",
                        "supporting_claim_ids": ["paper-alpha.claim.1"],
                    }
                ]
            },
        ]
    )
    runtime = ApplicationFacade(
        store=SQLiteStore(tmp_path / "metadata.sqlite"),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        providers={"fake": provider},
        clock=SystemClock(),
    )
    try:
        yield runtime
    finally:
        runtime.close()


def _run_json(
    capsys: pytest.CaptureFixture[str],
    facade: ApplicationFacade,
    arguments: tuple[str, ...],
) -> tuple[int, dict[str, object]]:
    exit_code = main(("--json", *arguments), facade=facade)
    captured = capsys.readouterr()
    payload = captured.out if exit_code == 0 else captured.err
    return exit_code, json.loads(payload)


def test_e2e_cli_adds_domain_articles_and_generates_alpha_from_wiki(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    facade: ApplicationFacade,
) -> None:
    article_root = tmp_path / "article"
    factor_root = article_root / "FACTOR"
    factor_root.mkdir(parents=True)
    (factor_root / "paper-alpha.md").write_text(
        "# Alpha Paper\n\nThe factor signal is available at the observation time.\n",
        encoding="utf-8",
    )
    wiki_root = tmp_path / "knowledge" / "wiki"

    exit_code, added = _run_json(
        capsys,
        facade,
        (
            "article-add",
            str(article_root),
            "--wiki-root",
            str(wiki_root),
        ),
    )

    assert exit_code == 0
    imported = added["imports"][0]
    assert imported["domain"] == "FACTOR"
    assert imported["article_count"] == 1
    assert imported["knowledge_pack"]["id"] == "factor-articles"
    assert imported["knowledge_pack"]["version"] == "1.0.0"
    assert imported["models_used"] == ["wiki-fake"]
    assert Path(imported["pack_path"]).exists()
    assert Path(imported["index_path"]).exists()
    assert Path(imported["page_paths"][0]).exists()

    exit_code, generated = _run_json(
        capsys,
        facade,
        (
            "alpha-generate",
            "liquid factor signal",
            "--domain",
            "FACTOR",
            "--wiki-root",
            str(wiki_root),
            "--idea-count",
            "1",
        ),
    )

    assert exit_code == 0
    assert generated["domain"] == "FACTOR"
    assert generated["request"] == "liquid factor signal"
    assert generated["pack_id"] == "factor-articles"
    assert generated["pack_version"] == "1.0.0"
    assert generated["models_used"] == ["wiki-fake"]
    idea_path = Path(generated["idea_paths"][0])
    assert idea_path.exists()
    assert "Liquidity-Aware Factor Screen" in idea_path.read_text(encoding="utf-8")

    provider = facade._provider_snapshot["fake"]
    assert isinstance(provider, FakeProvider)
    assert provider.calls[1].payload["query"] == "liquid factor signal"
    assert provider.calls[1].payload["claims"][0]["source"] == (
        "pages/factor-articles/1.0.0/paper-alpha.md"
    )


def test_e2e_cli_rejects_articles_outside_domain_directories(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    facade: ApplicationFacade,
) -> None:
    article_root = tmp_path / "article"
    article_root.mkdir()
    (article_root / "unclassified.md").write_text("# Unclassified\n", encoding="utf-8")

    exit_code, error = _run_json(capsys, facade, ("article-add", str(article_root)))

    assert exit_code == 2
    assert error["error"]["code"] == "AF-SCHEMA-001"
