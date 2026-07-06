"""Shared SQL fragment for the derived student ``display_status`` (R1–R4).

Leaf module imported by both ``parent_live_dao`` (parent-portal children) and
``student_live_dao`` (admin students list). It must import nothing from
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
