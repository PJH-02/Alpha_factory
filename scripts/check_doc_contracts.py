"""Check that the authority documents retain the approved Phase 0 decisions."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_AUTHORITY_DOCUMENTS = (
    "README.md",
    "docs/01_Requirements.md",
    "docs/02_Architecture.md",
    "docs/03_DevelopmentPlan.md",
    "docs/04_API.md",
    "docs/05_Database.md",
    "docs/06_CodingGuidelines.md",
    "docs/07_TestPlan.md",
    "docs/reference.md",
)
REQUIRED_NEW_ADRS = (
    "docs/ADR/ADR-0009-finite-deterministic-search-and-trial-ledger.md",
    "docs/ADR/ADR-0010-validation-holdout-and-publication.md",
)
FORBIDDEN_STALE_PHRASES = {
    "four development days": re.compile(
        r"\b(?:four|4)[-\s]+(?:development\s+)?days?\b|4일\s*(?:MVP|개발|일정|제약|범위|내)",
        re.IGNORECASE,
    ),
    "six smoke engines": re.compile(
        r"\b(?:six|6)[-\s]+(?:domain\s+)?smoke(?:\s+engines?)?\b"
        r"|6개\s*(?:(?:domain|도메인)\s*)?(?:smoke|스모크)",
        re.IGNORECASE,
    ),
}
CROSS_DOMAIN_DEFERRAL_DOCUMENTS = (
    "docs/01_Requirements.md",
    "docs/ADR/ADR-0008-cross-domain-composition.md",
)
CROSS_DOMAIN_DEFERRAL_MARKERS = {
    "cross-domain composition": re.compile(
        r"cross[-\s]+domain(?:\s+[a-z/]+)?\s+composition|교차\s*도메인", re.IGNORECASE
    ),
    "Meta Portfolio": re.compile(r"meta\s+portfolio", re.IGNORECASE),
    "deferred/연기": re.compile(r"\bdeferred\b|연기|후속\s*(?:릴리스|release)", re.IGNORECASE),
}


def main() -> int:
    errors: list[str] = []
    required_paths = (*REQUIRED_AUTHORITY_DOCUMENTS, *REQUIRED_NEW_ADRS)

    for relative_path in required_paths:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing required document: {relative_path}")

    markdown_paths = [ROOT / "README.md"]
    docs_directory = ROOT / "docs"
    if docs_directory.is_dir():
        markdown_paths.extend(sorted(docs_directory.rglob("*.md")))

    for path in markdown_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(ROOT).as_posix()
        for phrase, pattern in FORBIDDEN_STALE_PHRASES.items():
            if pattern.search(text):
                errors.append(f"forbidden stale phrase ({phrase}): {relative_path}")

    for relative_path in CROSS_DOMAIN_DEFERRAL_DOCUMENTS:
        path = ROOT / relative_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker, pattern in CROSS_DOMAIN_DEFERRAL_MARKERS.items():
            if not pattern.search(text):
                errors.append(f"missing cross-domain deferral marker ({marker}): {relative_path}")

    if errors:
        print("Document contract check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Document contracts are satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
