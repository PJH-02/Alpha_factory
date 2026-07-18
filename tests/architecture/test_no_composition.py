"""Static and contract checks that keep strategy research single-domain."""

import ast
from collections.abc import Iterator
from pathlib import Path

import alpha_foundry
import alpha_foundry.compiler as compiler
import alpha_foundry.labs as labs
from alpha_foundry.domain.models import StrategySpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
LAB_ROOT = SOURCE_ROOT / "alpha_foundry" / "labs"
_LAB_PACKAGE = "alpha_foundry.labs"
_EXPECTED_DOMAIN_LABS = frozenset(
    {
        "factor",
        "statarb",
        "market_making",
        "structural_flow",
        "cross_venue",
        "derivatives",
        "event_fundamental",
        "time_series",
    }
)
_STRATEGY_PROPERTIES = frozenset(
    {
        "strategy_id",
        "version",
        "domain",
        "operator_set_version",
        "operator_set_hash",
        "schema_hash",
        "ast",
    }
)


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


def _imports(path: Path) -> Iterator[tuple[int, str | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, _resolved_import_module(path, node)


def _composition_declarations(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "composition" in target.id.casefold():
                    yield node.lineno, target.id
        if name is not None and "composition" in name.casefold():
            yield node.lineno, name


def _schema_strings(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key)
            yield from _schema_strings(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            yield from _schema_strings(nested_value)
    elif isinstance(value, str):
        yield value


def test_arch_comp_001_only_the_eight_domain_labs_exist() -> None:
    domain_labs = {
        path.stem
        for path in LAB_ROOT.glob("*.py")
        if path.name not in {"__init__.py", "registry.py"}
    }

    assert domain_labs == _EXPECTED_DOMAIN_LABS
    assert not tuple(LAB_ROOT.rglob("*composition*"))


def test_arch_comp_002_no_composition_api_or_schema_is_declared() -> None:
    public_names = set(alpha_foundry.__all__)
    public_names.update(name for name in dir(compiler) if not name.startswith("_"))
    public_names.update(name for name in labs.__all__ if not name.startswith("_"))
    strategy_schema = StrategySpec.model_json_schema()
    violations = [
        f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {name}"
        for path in SOURCE_ROOT.rglob("*.py")
        for line_number, name in _composition_declarations(path)
    ]

    assert set(strategy_schema["properties"]) == _STRATEGY_PROPERTIES
    assert not [name for name in public_names if "composition" in name.casefold()]
    assert not [
        value for value in _schema_strings(strategy_schema) if "composition" in value.casefold()
    ]
    assert not violations, "composition declarations are prohibited:\n" + "\n".join(violations)


def test_arch_comp_003_domain_labs_do_not_import_each_other() -> None:
    violations: list[str] = []

    for module_name in _EXPECTED_DOMAIN_LABS:
        path = LAB_ROOT / f"{module_name}.py"
        for line_number, imported_module in _imports(path):
            if imported_module == _LAB_PACKAGE or (
                imported_module is not None and imported_module.startswith(f"{_LAB_PACKAGE}.")
            ):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{line_number}: {imported_module}"
                )

    assert not violations, "domain labs must not import another lab:\n" + "\n".join(violations)
