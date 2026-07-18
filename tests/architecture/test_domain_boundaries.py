"""Static dependency checks for the framework-independent domain package."""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
DOMAIN_ROOT = SOURCE_ROOT / "alpha_foundry" / "domain"
ALLOWED_EXTERNAL_IMPORT_ROOTS = frozenset({"pydantic"})


def _domain_python_files() -> list[Path]:
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def _resolved_import_module(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    module_parts = path.relative_to(SOURCE_ROOT).with_suffix("").parts
    package_parts = module_parts[:-1]
    levels_above_package = node.level - 1
    if levels_above_package > len(package_parts):
        return None

    target_parts = package_parts[: len(package_parts) - levels_above_package]
    if node.module is not None:
        target_parts += tuple(node.module.split("."))
    return ".".join(target_parts)


def _import_references(path: Path) -> Iterator[tuple[int, str | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, _resolved_import_module(path, node)


def test_domain_imports_do_not_cross_into_outer_application_layers() -> None:
    violations: list[str] = []

    for path in _domain_python_files():
        for line_number, module in _import_references(path):
            if module is None:
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{line_number}: unresolved import"
                )
            elif module == "alpha_foundry" or (
                module.startswith("alpha_foundry.")
                and not module.startswith("alpha_foundry.domain")
            ):
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {module}")

    assert not violations, "domain imports must not depend on outer layers:\n" + "\n".join(
        violations
    )


def test_domain_imports_remain_stdlib_or_pydantic_value_contracts() -> None:
    violations: list[str] = []
    standard_library = sys.stdlib_module_names | {"__future__"}

    for path in _domain_python_files():
        for line_number, module in _import_references(path):
            if module is None or module.startswith("alpha_foundry."):
                continue
            root = module.split(".", maxsplit=1)[0]
            if root not in standard_library and root not in ALLOWED_EXTERNAL_IMPORT_ROOTS:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {module}")

    assert not violations, (
        "domain imports must not depend on frameworks or infrastructure:\n" + "\n".join(violations)
    )
