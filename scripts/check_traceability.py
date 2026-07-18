"""Fail closed when approved requirements lose traceable test evidence."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "docs/01_Requirements.md"
TEST_PLAN = ROOT / "docs/07_TestPlan.md"


def _ids(prefix: str, last: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{number:03d}" for number in range(1, last + 1))


REQUIRED_FR_GROUPS = {
    "knowledge and generation": (*_ids("FR-KNOW-", 6), *_ids("FR-GEN-", 5)),
    "single-domain DSL": _ids("FR-DSL-", 6),
    "finite search and ledger": (*_ids("FR-SRCH-", 9), *_ids("FR-LEDGER-", 6)),
    "engines and policies": (*_ids("FR-ENG-", 8), *_ids("FR-POL-", 3)),
    "validation and PBO": (
        *_ids("FR-VAL-", 4),
        *_ids("FR-PBO-", 5),
        *_ids("FR-PROFILE-", 3),
    ),
    "holdout and publication": (
        *_ids("FR-HOLD-", 5),
        *_ids("FR-PUB-", 4),
        *_ids("FR-REJ-", 3),
    ),
    "interfaces, reports, and jobs": (
        *_ids("FR-IFACE-", 3),
        *_ids("FR-RPT-", 6),
        *_ids("FR-JOB-", 5),
    ),
}
REQUIRED_NFR_IDS = (
    *_ids("NFR-DET-", 3),
    "NFR-SEC-001",
    "NFR-REL-001",
    "NFR-MNT-001",
    "NFR-TST-001",
)
REQUIRED_AC_IDS = _ids("AC-", 21)
_CROSS_OS_MARKER = re.compile(r"(?:cross[- ]os|windows.{0,40}linux)", re.IGNORECASE)

REQUIRED_DOCUMENTS = (
    "docs/01_Requirements.md",
    "docs/02_Architecture.md",
    "docs/04_API.md",
    "docs/05_Database.md",
    "docs/06_CodingGuidelines.md",
    "docs/07_TestPlan.md",
)
REQUIRED_SOURCE_PATHS = {
    "canonical identity": ("src/alpha_foundry/domain/canonical.py",),
    "single-domain compiler registry": ("src/alpha_foundry/labs/registry.py",),
    "finite search": ("src/alpha_foundry/search/runner.py",),
    "validation": (
        "src/alpha_foundry/validation/walkforward.py",
        "src/alpha_foundry/validation/pbo.py",
        "src/alpha_foundry/validation/holdout.py",
    ),
    "disclosure and reports": (
        "src/alpha_foundry/validation/disclosure.py",
        "src/alpha_foundry/reporting.py",
    ),
}
REQUIRED_TEST_PATHS = {
    "canonical identity": ("tests/unit/test_canonical.py",),
    "single-domain DSL": (
        "tests/contract/test_domain_models.py",
        "tests/contract/test_lab_registry.py",
        "tests/contract/test_strategy_compiler.py",
    ),
    "finite search and ledger": (
        "tests/unit/search/test_digests.py",
        "tests/unit/search/test_parent_pool.py",
        "tests/unit/search/test_slots.py",
        "tests/unit/search/test_stops.py",
    ),
    "validation and PBO": (
        "tests/unit/validation/test_walkforward.py",
        "tests/unit/validation/test_pbo.py",
        "tests/integration/test_pbo_certificate.py",
        "tests/security/test_future_overlap.py",
    ),
    "one-shot holdout and disclosure": (
        "tests/contract/test_validation_registry.py",
        "tests/integration/test_holdout_atomic.py",
        "tests/recovery/test_holdout_crash.py",
        "tests/security/test_holdout_feedback.py",
    ),
    "architecture boundaries": (
        "tests/architecture/test_domain_boundaries.py",
        "tests/architecture/test_no_composition.py",
    ),
}


def _missing_ids(text: str, identifiers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        identifier for identifier in identifiers if not re.search(rf"\b{identifier}\b", text)
    )


def _check_paths(errors: list[str], groups: dict[str, tuple[str, ...]], kind: str) -> None:
    for group, relative_paths in groups.items():
        missing = tuple(path for path in relative_paths if not (ROOT / path).is_file())
        if missing:
            errors.append(f"missing {kind} for {group}: {', '.join(missing)}")


def _read_text(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"cannot read {path.relative_to(ROOT).as_posix()}: {error}")
        return None


def _check_test_contents(errors: list[str]) -> None:
    for group, relative_paths in REQUIRED_TEST_PATHS.items():
        for relative_path in relative_paths:
            path = ROOT / relative_path
            if not path.is_file():
                continue
            text = _read_text(path, errors)
            if text is not None and not re.search(
                r"^\s*(?:async\s+)?def test_", text, re.MULTILINE
            ):
                errors.append(f"test path has no pytest test function for {group}: {relative_path}")


def _check_acceptance_rows(test_plan: str, errors: list[str]) -> None:
    for index, acceptance_id in enumerate(REQUIRED_AC_IDS, start=1):
        row = re.search(
            rf"^\|\s*AT-{index:03d}\s*\|\s*{acceptance_id}\s*\|\s*([^|\n]+)\|\s*$",
            test_plan,
            flags=re.MULTILINE,
        )
        if row is None:
            errors.append(
                f"missing acceptance-to-test row: AT-{index:03d} must map {acceptance_id} to tests"
            )
        elif not row.group(1).strip() or row.group(1).strip() == "-":
            errors.append(f"empty test evidence in AT-{index:03d} for {acceptance_id}")


def main() -> int:
    errors: list[str] = []

    for relative_path in REQUIRED_DOCUMENTS:
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing authority document: {relative_path}")
    _check_paths(errors, REQUIRED_SOURCE_PATHS, "source path")
    _check_paths(errors, REQUIRED_TEST_PATHS, "test path")
    _check_test_contents(errors)

    requirements = _read_text(REQUIREMENTS, errors) if REQUIREMENTS.is_file() else None
    if requirements is not None:
        for group, identifiers in REQUIRED_FR_GROUPS.items():
            missing = _missing_ids(requirements, identifiers)
            if missing:
                errors.append(f"requirements omit {group} IDs: {', '.join(missing)}")
        missing_nfrs = _missing_ids(requirements, REQUIRED_NFR_IDS)
        if missing_nfrs:
            errors.append(f"requirements omit non-functional IDs: {', '.join(missing_nfrs)}")
        missing_acceptance = _missing_ids(requirements, REQUIRED_AC_IDS)
        if missing_acceptance:
            errors.append(f"requirements omit acceptance IDs: {', '.join(missing_acceptance)}")

    test_plan = _read_text(TEST_PLAN, errors) if TEST_PLAN.is_file() else None
    if test_plan is not None:
        _check_acceptance_rows(test_plan, errors)
        if not _CROSS_OS_MARKER.search(test_plan):
            errors.append("test plan omits cross-OS AF-CANON release evidence")

    if errors:
        print("Traceability check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Requirements, acceptance criteria, and required test evidence are traceable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
