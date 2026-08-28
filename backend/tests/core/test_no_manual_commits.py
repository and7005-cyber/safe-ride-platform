"""No hand-rolled transaction control in DAOs or services (U7).

A bare ``conn.commit()`` silently drops the transaction-local
``saferide.school_ids`` GUC, so every statement after it runs unscoped —
the exact hazard the slot-in service carried until U7 moved its phase
boundaries into ``scoped_transaction`` (which re-arms the GUC on both sides
of its commit). This grep pins the rule: ``app/dao`` and ``app/services``
never call ``.commit()`` or ``.rollback()`` directly. ``scoped_transaction``
itself lives in ``app/core/db.py``, outside the swept tree; a module with a
legitimate need must be allowlisted here with a comment saying why.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Relative paths (from app/) allowed to keep a direct commit/rollback call.
# Empty by design after U7: the slot-in service — the one prior holder —
# now runs its phase boundaries through scoped_transaction.
ALLOWLIST: set[str] = set()


def _offenders() -> list[str]:
    # AST, not a text grep: docstrings and comments may legitimately NAME
    # the calls while explaining why they are banned.
    out: list[str] = []
    for root in (APP / "dao", APP / "services"):
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(APP))
            if rel in ALLOWLIST:
                continue
            tree = ast.parse(path.read_text(), filename=rel)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("commit", "rollback")
                ):
                    out.append(f"{rel}:{node.lineno}: .{node.func.attr}()")
    return out


def test_no_manual_commit_or_rollback_in_daos_and_services():
    offenders = _offenders()
    assert not offenders, (
        "direct .commit()/.rollback() calls found — route them through "
        "scoped_transaction (app/core/db.py) or allowlist with a reason:\n"
        + "\n".join(offenders)
    )
