"""Shared SQL fragments: the derived student and bus status, run progress, and
the scope-covers-run-type predicate.

Leaf module imported across ``app.dao``. It must import nothing from
``app.dao``: the student_live_dao↔fleet_dao pair already needs a lazy import
to dodge a cycle, and this module must never grow another.

The student derivation reads participation and absence (U3), day-scoped to
Africa/Nairobi and computed at read time. It replaces four staleness branches
that existed only to compensate for a status column with no provenance — a
column that could not say which run a boarding belonged to, or whether anyone
observed it.

Branches, in order:

- a whole-day today-absence, or a driver-sourced period-scoped one → 'absent'.
  Driver marks write status despite their partial scope; a parent's own partial
  cancellation gates rosters per run type but never the displayed status;
- an unaccounted participation row on any of today's runs → 'unaccounted'.
  Recorded by the office force-close: the app saying plainly that it does not
  know where the child is, rather than guessing. This branch sits above the
  rest because a completed run must not decay it to 'at-home';
- a confirmed drop-off or hand-over today → 'dropped-off';
- a boarding today on a run still open → 'on-bus' if the driver observed it,
  'expected-on-bus' if the afternoon auto-board presumed it. The presumption is
  never rendered as a confirmed fact;
- a confirmed boarding on a completed morning run today → 'at-school';
- everything else → 'at-home'. No participation and no absence today means the
  child is not in the system's care, which also covers the child left at school
  on an earlier day and the newly created student — the two cases the old
  derivation got wrong because it fell through to the raw column.

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
                                 and (a.scope = 'day' or a.source = 'driver')
                           ) then 'absent'
                           when exists (
                               select 1 from run_participation p
                               join live_runs r on r.id = p.run_id
                               where p.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and p.unaccounted_at is not null
                           ) then 'unaccounted'
                           when exists (
                               select 1 from run_participation p
                               join live_runs r on r.id = p.run_id
                               where p.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and (p.dropped_off_at is not null or p.handover_at is not null)
                           ) then 'dropped-off'
                           when exists (
                               select 1 from run_participation p
                               join live_runs r on r.id = p.run_id
                               where p.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and r.status <> 'completed'
                                 and p.boarded_at is not null
                                 and p.boarded_presumed = false
                           ) then 'on-bus'
                           when exists (
                               select 1 from run_participation p
                               join live_runs r on r.id = p.run_id
                               where p.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and r.status <> 'completed'
                                 and p.boarded_at is not null
                                 and p.boarded_presumed = true
                           ) then 'expected-on-bus'
                           when exists (
                               select 1 from run_participation p
                               join live_runs r on r.id = p.run_id
                               where p.student_id = {student}.id
                                 and r.date = (now() at time zone 'Africa/Nairobi')::date
                                 and r.type = 'morning'
                                 and r.status = 'completed'
                                 and p.boarded_at is not null
                           ) then 'at-school'
                           else 'at-home'
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
