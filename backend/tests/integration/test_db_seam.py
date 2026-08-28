"""The connection seam (U5): the transaction-local ``saferide.school_ids`` GUC.

Proves against the real local database that: an explicit scope arms the GUC,
the request context var is the fallback carrier, global connections never arm
it, a plain mid-connection commit silently drops it (the hazard),
``scoped_transaction`` re-arms on both sides of its commit, and the strict
flag turns an implicitly-scoped checkout into a programming error.
"""

import os

import pytest

from conftest import SCHOOL_A_ID, SCHOOL_B_ID

from app.core import db
from app.core.db import get_connection, get_global_connection, scoped_transaction
from app.core.scope import ParentScope, SchoolScope, set_current_scope

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

SCOPE_A = SchoolScope(user_id="u", school_id=SCHOOL_A_ID, role="director")
SCOPE_B = SchoolScope(user_id="u", school_id=SCHOOL_B_ID, role="director")


@pytest.fixture(autouse=True)
def _clean_context(in_process_db):
    set_current_scope(None)
    yield
    set_current_scope(None)


def read_guc(conn) -> str | None:
    return conn.execute(
        "select current_setting('saferide.school_ids', true) as v"
    ).fetchone()["v"]


def test_explicit_scope_arms_the_guc():
    with get_connection(SCOPE_A) as conn:
        assert read_guc(conn) == SCHOOL_A_ID


def test_parent_scope_carries_the_full_school_set():
    scope = ParentScope(user_id="u", school_ids=(SCHOOL_A_ID, SCHOOL_B_ID))
    with get_connection(scope) as conn:
        assert read_guc(conn) == f"{SCHOOL_A_ID},{SCHOOL_B_ID}"


def test_context_var_is_the_fallback_carrier():
    set_current_scope(SCOPE_B)
    with get_connection() as conn:
        assert read_guc(conn) == SCHOOL_B_ID


def test_global_connection_never_arms_even_inside_a_scoped_request():
    set_current_scope(SCOPE_A)
    with get_global_connection() as conn:
        assert read_guc(conn) in (None, "")


def test_a_fresh_checkout_after_a_scoped_one_starts_clean():
    with get_connection(SCOPE_A) as conn:
        assert read_guc(conn) == SCHOOL_A_ID
    with get_global_connection() as conn:
        assert read_guc(conn) in (None, "")


def test_plain_commit_drops_the_guc_and_scoped_transaction_re_arms():
    with get_connection(SCOPE_A) as conn:
        conn.commit()  # the hazard: transaction-local GUC dies with the commit
        assert read_guc(conn) in (None, "")
        with scoped_transaction(conn, SCOPE_A):
            assert read_guc(conn) == SCHOOL_A_ID
        # re-armed for follow-up statements after the inner commit
        assert read_guc(conn) == SCHOOL_A_ID
        conn.rollback()  # leave the pooled connection idle, not in-transaction


def test_scoped_transaction_reads_the_context_var_when_no_scope_is_passed():
    set_current_scope(SCOPE_B)
    with get_connection() as conn:
        conn.commit()
        with scoped_transaction(conn):
            assert read_guc(conn) == SCHOOL_B_ID
        conn.rollback()


def test_strict_mode_refuses_an_implicitly_scoped_checkout(monkeypatch):
    monkeypatch.setattr(db, "STRICT_EXPLICIT_SCOPE", True)
    set_current_scope(SCOPE_A)
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        with get_connection():
            pass  # pragma: no cover
    # explicit scopes and global checkouts stay allowed
    with get_connection(SCOPE_A) as conn:
        assert read_guc(conn) == SCHOOL_A_ID
    with get_global_connection() as conn:
        assert read_guc(conn) in (None, "")


def test_alternating_scopes_never_leak_across_checkouts():
    for i in range(50):
        scope = SCOPE_A if i % 2 == 0 else SCOPE_B
        with get_connection(scope) as conn:
            assert read_guc(conn) == scope.school_id
