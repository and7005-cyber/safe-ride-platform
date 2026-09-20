"""The connection seam (U5/U7): the transaction-local ``saferide.school_ids`` GUC.

Proves against the real local database that: an explicit scope arms the GUC,
global connections never arm it, a plain mid-connection commit silently drops
it (the hazard), ``scoped_transaction`` re-arms on both sides of its commit —
and, since U7's strict flip, that an implicitly-scoped checkout inside a
SchoolScope request is a programming error by DEFAULT (the fully-converted
staff/driver/provider surfaces), while ParentScope contexts keep the
context-var fallback until U11 converts the parent portal. The no-GUC
negative tests pin the early-return paths: recipient lookups, the plan
geometry refresh and the background services must raise (or fail closed)
rather than silently succeed when called without their scope threaded.
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


def test_context_var_is_the_fallback_carrier_for_parent_scopes_only():
    # ParentScope stays on the fallback until U11 converts the parent portal.
    set_current_scope(ParentScope(user_id="u", school_ids=(SCHOOL_A_ID,)))
    with get_connection() as conn:
        assert read_guc(conn) == SCHOOL_A_ID
    # A SchoolScope context without an explicit scope is a missed conversion
    # (U7's strict default), never a fallback.
    set_current_scope(SCOPE_B)
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        with get_connection():
            pass  # pragma: no cover


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
    with get_connection(SCOPE_B) as conn:
        conn.commit()
        with scoped_transaction(conn):
            assert read_guc(conn) == SCHOOL_B_ID
        conn.rollback()


def test_strict_mode_refuses_an_implicitly_scoped_checkout():
    # The DEFAULT since U7's flip — no monkeypatching: the flag ships True.
    assert db.STRICT_EXPLICIT_SCOPE is True
    set_current_scope(SCOPE_A)
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        with get_connection():
            pass  # pragma: no cover
    # explicit scopes and global checkouts stay allowed
    with get_connection(SCOPE_A) as conn:
        assert read_guc(conn) == SCHOOL_A_ID
    with get_global_connection() as conn:
        assert read_guc(conn) in (None, "")


# --- U7 no-GUC negatives: unthreaded calls must not silently succeed ---------


def test_unthreaded_school_scoped_callees_raise_under_the_strict_default():
    """The early-return paths (recipient lookups, the plan geometry refresh)
    called inside a school-scoped request WITHOUT their scope threaded must
    raise — never quietly open an implicitly-scoped connection."""
    from app.dao.fleet_plan_dao import FleetPlanDao
    from app.dao.push_dao import PushDao

    set_current_scope(SCOPE_A)
    ghost = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        PushDao().parents_of_students([ghost])
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        PushDao().parents_of_bus(ghost)
    with pytest.raises(RuntimeError, match="without an explicit scope"):
        FleetPlanDao()._refresh_route_geometry(ghost)
    # Threaded explicitly, the same early-return paths answer normally.
    assert PushDao().parents_of_students([ghost], scope=SCOPE_A) == []
    assert FleetPlanDao()._refresh_route_geometry(ghost, scope=SCOPE_A) is False


def test_unthreaded_background_services_fail_closed_not_silently():
    """The best-effort background entry points swallow errors by contract, so
    an unthreaded dispatch inside a school-scoped request returns their
    failure signal (None) with NOTHING written — while the threaded call
    works. Regeneration's callees are conn-threaded and covered above via
    the checkout seam they all share."""
    from app.services.push_service import notify_route_changes
    from app.services.slot_in_service import propose_slot_ins

    ghost = "00000000-0000-0000-0000-000000000000"
    set_current_scope(SCOPE_A)
    assert propose_slot_ins([ghost]) is None
    assert notify_route_changes(route_ids=[ghost]) is None
    # Threaded: the same calls answer their normal empty-world results.
    assert propose_slot_ins([ghost], scope=SCOPE_A) == {"students": 0, "records": 0}
    assert notify_route_changes(route_ids=[ghost], scope=SCOPE_A) is None  # no members


def test_alternating_scopes_never_leak_across_checkouts():
    for i in range(50):
        scope = SCOPE_A if i % 2 == 0 else SCOPE_B
        with get_connection(scope) as conn:
            assert read_guc(conn) == scope.school_id
