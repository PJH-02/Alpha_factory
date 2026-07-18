"""Statically enforce the Alpha Foundry dependency boundaries without importing product code."""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "alpha_foundry"
DOMAIN_ROOT = PACKAGE_ROOT / "domain"
LABS_ROOT = PACKAGE_ROOT / "labs"
API_PATH = PACKAGE_ROOT / "interfaces" / "api.py"
REPORT_BOUNDARY_PATHS = (
    PACKAGE_ROOT / "reporting.py",
    PACKAGE_ROOT / "validation" / "disclosure.py",
)
ALLOWED_DOMAIN_EXTERNAL_ROOTS = frozenset({"pydantic"})
FORBIDDEN_RESEARCH_PREFIXES = (
    "alpha_foundry.application",
    "alpha_foundry.knowledge",
    "alpha_foundry.generation",
    "alpha_foundry.failure_memory",
    "alpha_foundry.search",
    "alpha_foundry.validation.holdout",
    "alpha_foundry.validation.pbo",
)
_COMPOSITION_TOKEN = re.compile(
    r"(?:^|[_-])(?:composition|meta[_-]?portfolio)(?:$|[_-])", re.IGNORECASE
)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_parts(path: Path) -> tuple[str, ...]:
    parts = path.relative_to(SOURCE_ROOT).with_suffix("").parts
    return parts if path.name == "__init__.py" else parts[:-1]


def _resolve_from(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = _package_parts(path)
    levels_above_package = node.level - 1
    if levels_above_package > len(package_parts):
        return None
    target_parts = package_parts[: len(package_parts) - levels_above_package]
    if node.module is not None:
        target_parts += tuple(node.module.split("."))
    return ".".join(target_parts)


def _call_is_dynamic_import(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in {"__import__", "import_module"}
    return isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"


def _module_references(path: Path, tree: ast.AST) -> Iterator[tuple[int, str | None]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from(path, node)
            yield node.lineno, module
            if module == "alpha_foundry.labs" or module in {
                "alpha_foundry",
                "alpha_foundry.application",
                "alpha_foundry.validation",
            }:
                yield from (
                    (node.lineno, f"{module}.{alias.name}")
                    for alias in node.names
                    if alias.name != "*"
                )
        elif isinstance(node, ast.Call) and _call_is_dynamic_import(node):
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                yield node.lineno, node.args[0].value


def _read_tree(path: Path, errors: list[str]) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as error:
        errors.append(f"cannot parse {path.relative_to(ROOT).as_posix()}: {error}")
        return None


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _check_domain_dependencies(errors: list[str]) -> None:
    if not DOMAIN_ROOT.is_dir():
        errors.append(f"missing domain package: {_relative(DOMAIN_ROOT)}")
        return

    standard_library = sys.stdlib_module_names | {"__future__"}
    for path in sorted(DOMAIN_ROOT.rglob("*.py")):
        tree = _read_tree(path, errors)
        if tree is None:
            continue
        for line_number, module in _module_references(path, tree):
            location = f"{_relative(path)}:{line_number}"
            if module is None:
                errors.append(f"{location}: unresolved relative import in domain core")
                continue
            if module == "alpha_foundry" or (
                module.startswith("alpha_foundry.")
                and not module.startswith("alpha_foundry.domain")
            ):
                errors.append(f"{location}: domain core imports outer layer {module}")
                continue
            if module.startswith("alpha_foundry."):
                continue
            external_root = module.split(".", maxsplit=1)[0]
            if (
                external_root not in standard_library
                and external_root not in ALLOWED_DOMAIN_EXTERNAL_ROOTS
            ):
                errors.append(f"{location}: domain core imports non-value dependency {module}")


def _check_lab_imports(errors: list[str]) -> None:
    if not LABS_ROOT.is_dir():
        errors.append(f"missing labs package: {_relative(LABS_ROOT)}")
        return

    for path in sorted(LABS_ROOT.rglob("*.py")):
        if path.name in {"__init__.py", "registry.py"}:
            continue
        tree = _read_tree(path, errors)
        if tree is None:
            continue
        own_module = _module_name(path)
        own_lab = own_module.split(".")[2] if own_module.count(".") >= 2 else ""
        for line_number, module in _module_references(path, tree):
            if not module or not module.startswith("alpha_foundry.labs."):
                continue
            target_parts = module.split(".")
            target_lab = target_parts[2] if len(target_parts) > 2 else ""
            if target_lab and target_lab != own_lab:
                errors.append(
                    f"{_relative(path)}:{line_number}: Lab {own_lab} imports Lab internal {module}"
                )


def _is_composition_name(value: str) -> bool:
    normalized = value.casefold().replace(" ", "_").replace("/", "_")
    return bool(_COMPOSITION_TOKEN.search(normalized))


def _check_composition_modules(errors: list[str]) -> None:
    if not PACKAGE_ROOT.is_dir():
        errors.append(f"missing application package: {_relative(PACKAGE_ROOT)}")
        return
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        parts = (*path.relative_to(PACKAGE_ROOT).parent.parts, path.stem)
        if any(_is_composition_name(part) for part in parts):
            errors.append(f"forbidden composition module: {_relative(path)}")


def _route_literals(tree: ast.AST) -> Iterable[tuple[int, str]]:
    route_methods = {"get", "post", "put", "patch", "delete", "api_route"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in route_methods:
            continue
        candidate: ast.expr | None = node.args[0] if node.args else None
        if candidate is None:
            candidate = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "path"), None
            )
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            yield node.lineno, candidate.value


def _check_composition_routes(errors: list[str]) -> None:
    if not API_PATH.is_file():
        errors.append(f"missing API adapter: {_relative(API_PATH)}")
        return
    tree = _read_tree(API_PATH, errors)
    if tree is None:
        return
    for line_number, route in _route_literals(tree):
        if _is_composition_name(route) or "meta-portfolio" in route.casefold():
            errors.append(
                f"forbidden composition route: {_relative(API_PATH)}:{line_number}: {route}"
            )


def _is_forbidden_research_import(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_RESEARCH_PREFIXES
    )


def _check_report_research_boundary(errors: list[str]) -> None:
    for path in REPORT_BOUNDARY_PATHS:
        if not path.is_file():
            errors.append(f"missing report/disclosure boundary module: {_relative(path)}")
            continue
        tree = _read_tree(path, errors)
        if tree is None:
            continue
        for line_number, module in _module_references(path, tree):
            if module and _is_forbidden_research_import(module):
                errors.append(
                    f"{_relative(path)}:{line_number}: report/disclosure imports research module {module}"
                )


def main() -> int:
    errors: list[str] = []
    _check_domain_dependencies(errors)
    _check_lab_imports(errors)
    _check_composition_modules(errors)
    _check_composition_routes(errors)
    _check_report_research_boundary(errors)

    if errors:
        print("Architecture check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Architecture boundaries are satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
