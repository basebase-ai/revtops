from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
BASELINE_MIGRATION_NUMBER = 125
MAX_REVISION_ID_LENGTH = 32
INCOMPATIBLE_OPERATION_PREFIXES = ("drop_", "rename_")


def _migration_files() -> list[Path]:
    return sorted(VERSIONS_DIR.glob("*.py"))


def _migration_number(path: Path) -> int | None:
    prefix = path.stem.split("_", 1)[0]
    return int(prefix) if prefix.isdigit() else None


def _future_migration_files() -> list[Path]:
    return [
        path
        for path in _migration_files()
        if (_migration_number(path) or -1) > BASELINE_MIGRATION_NUMBER
    ]


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _assigned_name_values(module: ast.Module) -> dict[str, Any]:
    values: dict[str, Any] = {}

    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = _literal_value(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            values[node.target.id] = _literal_value(node.value) if node.value else None

    return values


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _call_attribute_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _function_call_names(function_node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(function_node):
        if isinstance(node, ast.Call):
            attr = _call_attribute_name(node)
            if attr:
                names.add(attr)
    return names


def _assert_revision_lengths(path: Path, values: dict[str, Any]) -> None:
    revision = values.get("revision")
    assert isinstance(revision, str), f"{path.name}: revision must be a string literal"
    assert len(revision) <= MAX_REVISION_ID_LENGTH, (
        f"{path.name}: revision '{revision}' is {len(revision)} chars; "
        f"max is {MAX_REVISION_ID_LENGTH}"
    )

    down_revision = values.get("down_revision")
    if isinstance(down_revision, str):
        assert len(down_revision) <= MAX_REVISION_ID_LENGTH, (
            f"{path.name}: down_revision '{down_revision}' is {len(down_revision)} chars; "
            f"max is {MAX_REVISION_ID_LENGTH}"
        )


def _assert_reversible(path: Path, module: ast.Module) -> None:
    downgrade = _find_function(module, "downgrade")
    assert downgrade is not None, f"{path.name}: missing downgrade()"

    downgrade_calls = _function_call_names(downgrade)
    assert downgrade_calls, f"{path.name}: downgrade() appears empty and is not reversible"


def _assert_backwards_compatible(path: Path, module: ast.Module) -> None:
    upgrade = _find_function(module, "upgrade")
    assert upgrade is not None, f"{path.name}: missing upgrade()"

    call_names = _function_call_names(upgrade)
    incompatible_calls = sorted(
        call_name
        for call_name in call_names
        if call_name.startswith(INCOMPATIBLE_OPERATION_PREFIXES)
    )

    assert not incompatible_calls, (
        f"{path.name}: upgrade() uses backwards-incompatible operations: "
        f"{incompatible_calls}. Avoid drop/rename operations in single-step migrations."
    )


def test_future_migrations_are_reversible_and_backwards_compatible() -> None:
    """Guardrail for new migrations added after current baseline.

    Existing historical migrations are excluded because some were intentionally
    irreversible or performed phased cleanup. New migrations should be reversible
    and avoid destructive one-step changes.
    """

    for path in _future_migration_files():
        module = _parse_module(path)
        values = _assigned_name_values(module)

        _assert_revision_lengths(path, values)
        _assert_reversible(path, module)
        _assert_backwards_compatible(path, module)
