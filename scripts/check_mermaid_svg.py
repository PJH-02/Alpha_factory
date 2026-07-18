"""Render the locked Mermaid diagram into a temporary file and compare it read-only."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/diagrams/alpha_foundry_overview.mmd"
COMMITTED_SVG = ROOT / "docs/diagrams/alpha_foundry_overview.svg"
PACKAGE_JSON = ROOT / "package.json"
PACKAGE_LOCK = ROOT / "package-lock.json"
LOCKED_MERMAID_VERSION = "11.4.2"
_GENERATED_MERMAID_ID = re.compile(r"\bmermaid-\d{10,}(?:-\d+)?\b")
_TIMESTAMP_ELEMENT = re.compile(
    r"(<dc:date\b[^>]*>)[^<]+(</dc:date\s*>)",
    re.IGNORECASE,
)
_TIMESTAMP_ATTRIBUTE = re.compile(
    r"(\b(?:data-)?(?:generated(?:-at)?|timestamp|created(?:-at)?)\s*=\s*[\"'])"
    r"[^\"']*([\"'])",
    re.IGNORECASE,
)


class CheckerError(RuntimeError):
    """Raised for a missing or mismatched local rendering toolchain."""


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CheckerError(f"cannot read {path.relative_to(ROOT).as_posix()}: {error}") from error


def _require_locked_mermaid() -> Path:
    package = _read_json(PACKAGE_JSON)
    lock = _read_json(PACKAGE_LOCK)
    if not isinstance(package, dict) or not isinstance(lock, dict):
        raise CheckerError("package manifests must be JSON objects")

    declared = package.get("devDependencies")
    if (
        not isinstance(declared, dict)
        or declared.get("@mermaid-js/mermaid-cli") != LOCKED_MERMAID_VERSION
    ):
        raise CheckerError(
            f"package.json must pin @mermaid-js/mermaid-cli to {LOCKED_MERMAID_VERSION}"
        )

    packages = lock.get("packages")
    locked = (
        packages.get("node_modules/@mermaid-js/mermaid-cli") if isinstance(packages, dict) else None
    )
    if not isinstance(locked, dict) or locked.get("version") != LOCKED_MERMAID_VERSION:
        raise CheckerError(
            f"package-lock.json must lock @mermaid-js/mermaid-cli to {LOCKED_MERMAID_VERSION}"
        )

    executable_name = "mmdc.cmd" if os.name == "nt" else "mmdc"
    executable = ROOT / "node_modules" / ".bin" / executable_name
    installed_manifest = ROOT / "node_modules" / "@mermaid-js" / "mermaid-cli" / "package.json"
    if not executable.is_file() or not installed_manifest.is_file():
        raise CheckerError("locked local mmdc is unavailable; install dependencies with npm ci")
    installed = _read_json(installed_manifest)
    if not isinstance(installed, dict) or installed.get("version") != LOCKED_MERMAID_VERSION:
        raise CheckerError(
            f"installed @mermaid-js/mermaid-cli is not locked version {LOCKED_MERMAID_VERSION}; run npm ci"
        )
    return executable


def _normalize_svg(text: str) -> str:
    """Remove only Mermaid's generated root ID and explicitly generated timestamps."""
    normalized = text.replace("\r\n", "\n")
    normalized = _GENERATED_MERMAID_ID.sub("mermaid-GENERATED-ID", normalized)
    normalized = _TIMESTAMP_ELEMENT.sub(r"\1GENERATED-TIMESTAMP\2", normalized)
    return _TIMESTAMP_ATTRIBUTE.sub(r"\1GENERATED-TIMESTAMP\2", normalized)


def _render(executable: Path, output: Path) -> None:
    command = (
        str(executable),
        "-i",
        str(SOURCE),
        "-o",
        str(output),
        "-b",
        "transparent",
    )
    try:
        result = subprocess.run(
            command,
            cwd=output.parent,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        raise CheckerError(f"could not execute locked local mmdc: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "mmdc returned no diagnostic"
        raise CheckerError(f"mmdc render failed ({result.returncode}): {detail}")
    if not output.is_file():
        raise CheckerError(
            "mmdc reported success but did not create an SVG in the temporary directory"
        )


def main() -> int:
    if not SOURCE.is_file() or not COMMITTED_SVG.is_file():
        missing = [
            path.relative_to(ROOT).as_posix()
            for path in (SOURCE, COMMITTED_SVG)
            if not path.is_file()
        ]
        print(f"Mermaid checker could not run: missing {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        executable = _require_locked_mermaid()
        committed = _normalize_svg(COMMITTED_SVG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="alpha-foundry-mermaid-") as directory:
            generated_path = Path(directory) / "alpha_foundry_overview.svg"
            _render(executable, generated_path)
            generated = _normalize_svg(generated_path.read_text(encoding="utf-8"))
    except (CheckerError, OSError, UnicodeError) as error:
        print(f"Mermaid checker could not run: {error}", file=sys.stderr)
        return 2

    if generated != committed:
        print(
            "Mermaid SVG is out of date; regenerate docs/diagrams/alpha_foundry_overview.svg.",
            file=sys.stderr,
        )
        return 1

    print("Committed Mermaid SVG matches the locked local renderer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
