"""The provider DAO's boundary, pinned statically (U10 / AE9, R24).

``ProviderDao`` must never read a school's data directly: no school DAO
imports (the shared audit writer excepted) and no school-owned table names in
its SQL — cross-school aggregates go through migration 013's SECURITY DEFINER
functions only. Pure source inspection: no database, no RUN_INTEGRATION gate,
so the boundary fails every build the moment someone reaches around it.
"""

import ast
import re
from pathlib import Path

import app.dao.provider_dao as provider_dao_module

SOURCE_PATH = Path(provider_dao_module.__file__)
ALLOWED_DAO_IMPORTS = {"app.dao.audit_dao"}
FORBIDDEN_TABLES = re.compile(r"\blive_(students|buses|routes|runs)\b")


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_provider_dao_imports_no_other_dao():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    dao_imports = {
        module
        for module in _imported_modules(tree)
        if module == "app.dao" or module.startswith("app.dao.")
    }
    offending = dao_imports - ALLOWED_DAO_IMPORTS - {"app.dao"}
    assert not offending, (
        f"ProviderDao may not import school DAOs; found {sorted(offending)}"
    )


def test_provider_dao_sql_never_names_a_school_owned_table():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            match = FORBIDDEN_TABLES.search(node.value)
            if match:
                hits.append(f"line {node.lineno}: {match.group(0)}")
    assert not hits, (
        "ProviderDao SQL/doc strings reference school-owned tables directly "
        f"(use the SECURITY DEFINER aggregates): {hits}"
    )


def test_provider_dao_uses_the_sanctioned_aggregates():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "provider_school_health()" in source
    assert "provider_audit_rows(" in source
