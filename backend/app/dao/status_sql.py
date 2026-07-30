"""Shared SQL fragments: the derived student ``display_status`` (R1–R4) and
the scope-covers-run-type predicate (U4).

Leaf module imported across ``app.dao``. It must import nothing from
``app.dao``: the student_live_dao↔fleet_dao pair already needs a lazy import
to dodge a cycle, and this module must never grow another.

The derivation is day-scoped to Africa/Nairobi, computed at read time and
never stored (the raw ``status`` column stays untouched in every payload).
Branches, in order:

- a whole-day today-absence exists (live_student_absences, scope='day') →
  'absent', whatever the raw status says. Partial-scope rows ('morning'/
  'afternoon' — parent Cancel-a-Ride) gate rosters per run type but never
  the displayed status (U4);
- raw 'absent' with no today-absence → 'at-home' (stale absent);
- raw 'on-bus' with no active run today whose run_stops contain the student
  → 'at-home' (stale on-bus). "Active" is the codebase's
  status <> 'completed' convention, so 'delayed' keeps counting, and
  membership goes through run_stops — never live_students.bus_id, which is
  derived, morning-preferring, and drifts;
- raw 'dropped-off' with no afternoon run today containing the student
  (same run_stops membership) → 'at-home' (stale dropped-off);
- anything else → the raw status.

The admin students list wraps this expression with its own 'unassigned' rule
(no live_student_routes rows → 'unassigned', overriding everything); that
wrap is admin-side only and lives in student_live_dao.
"""

def scope_covers(scope_sql: str, run_type_sql: str) -> str:
    """SQL predicate: the absence ``scope`` covers a run type (U4) — 'day'
    covers every run, a partial scope only its own type. Both arguments are
    SQL fragments (a column reference or a placeholder), so the one covering
    rule reads identically whether the scope or the run type is the column:
    ``scope_covers("a.scope", "%s")`` or ``scope_covers("%(scope)s", "r.type")``.
    """
    return f"({scope_sql} = 'day' or {scope_sql} = {run_type_sql})"


_DISPLAY_STATUS_CASE = """case
                           when exists (
                               select 1 from live_student_absences a
                               where a.student_id = {student}.id
                                 and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                                 and a.scope = 'day'
                           ) then 'absent'
                           when {student}.status = 'absent' then 'at-home'
                           when {student}.status = 'on-bus' and not exists (
                               select 1
                               from live_runs r
                               join run_stops rs on rs.run_id = r.id
                               where rs.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and r.status <> 'completed'
                           ) then 'at-home'
                           when {student}.status = 'dropped-off' and not exists (
                               select 1
                               from live_runs r
                               join run_stops rs on rs.run_id = r.id
                               where rs.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and r.type = 'afternoon'
                           ) then 'at-home'
                           else {student}.status
                       end"""


def display_status_case(student: str) -> str:
    """The display_status CASE expression (bare — the consumer adds its own
    ``as`` alias), parameterized by the consuming query's ``live_students``
    table alias. Subquery aliases (a, r, rs) are fragment-local."""
    return _DISPLAY_STATUS_CASE.format(student=student)


# --- bus status (U9) ---------------------------------------------------------
# The stored live_buses.status was never written by any run event — only by the
# admin form — so a bus mid-route read 'idle' until someone remembered to change
# it, and the Dashboard's fleet tiles counted that hand-maintained field. The
# derivation replaces it. The column itself stays for now (no column has ever
# been dropped here and there is no rollback convention); nothing writes it.
#
# Availability is the one thing no derivation can produce: whether a bus is in
# the workshop is not a function of its runs. It stays office-set and overrides
# everything, carrying the retired column's 'offline' meaning forward.
#
# Branch order matters: a delayed run is also a non-completed run, so the
# delayed test has to come first. 'delayed' is the single manually maintained
# status value in the system, set by the office on the run; the bus inherits it
# and loses it when the run ends, with nobody clearing anything by hand.

_BUS_STATUS_CASE = """case
                        when {bus}.availability = 'out-of-service' then 'out-of-service'
                        when exists (
                            select 1 from live_runs r
                            where r.bus_id = {bus}.id
                              and r.date = (now() at time zone 'Africa/Nairobi')::date
                              and r.status = 'delayed'
                        ) then 'delayed'
                        when exists (
                            select 1 from live_runs r
                            where r.bus_id = {bus}.id
                              and r.date = (now() at time zone 'Africa/Nairobi')::date
                              and r.status <> 'completed'
                        ) then 'active'
                        else 'idle'
                    end"""


def bus_status_case(bus: str) -> str:
    """The derived bus status CASE expression (bare — the consumer adds its own
    ``as`` alias), parameterized by the consuming query's ``live_buses`` table
    alias. The subquery alias (r) is fragment-local.

    Values: 'out-of-service' | 'delayed' | 'active' | 'idle'. The first comes
    from the office-set availability attribute; the rest are derived from the
    bus's run today.
    """
    return _BUS_STATUS_CASE.format(bus=bus)


# --- run progress (U10) ------------------------------------------------------
# A run that has stopped recording arrivals. Derived, and distinct from the
# office-set 'delayed' status: delay is a human judgement about schedule, this
# is the absence of taps.
#
# Anchored at the run's creation as well as at each arrival, so a driver whose
# phone dies before the first stop is caught — that case is the trigger the
# office force-close exists for, and measuring only between consecutive
# arrivals would never fire on it.
#
# Suppressed once every stop carries a timestamp. The closure gate deliberately
# lengthens the window after the final arrival, while the driver resolves
# blocking children before the run can close; a flag that fires on that normal
# path is a flag the office learns to ignore.

NO_PROGRESS_MINUTES = 15

_NO_PROGRESS_CASE = """case
                         when {run}.status = 'completed' then false
                         when not exists (
                             select 1 from run_stops rs
                             where rs.run_id = {run}.id and rs.arrived_at is null
                         ) then false
                         else coalesce(
                             (select max(rs.arrived_at) from run_stops rs where rs.run_id = {run}.id),
                             {run}.created_at
                         ) < now() - interval '%d minutes'
                     end""" % NO_PROGRESS_MINUTES


def no_progress_case(run: str) -> str:
    """The derived no-progress boolean (bare — the consumer adds its own ``as``
    alias), parameterized by the consuming query's ``live_runs`` table alias.
    The subquery alias (rs) is fragment-local.
    """
    return _NO_PROGRESS_CASE.format(run=run)
