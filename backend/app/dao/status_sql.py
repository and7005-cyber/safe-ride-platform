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

- a whole-day today-absence, or one where someone individually marked a period
  (``marked_period``) → 'absent'. A period marking is a driver saying they were
  at the stop and the child was not; that is evidence about the child, so it
  shows even though its coverage is partial. A parent's own partial cancellation
  is a statement of intent — it gates that run's roster and nothing else.

  Keyed on ``marked_period`` rather than ``source = 'driver'`` because since U8
  the driver no longer takes over the row: widening a parent's morning
  cancellation with an afternoon mark leaves the source 'parent' (R20), and a
  source test would have silently stopped showing those children as absent;
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

from app.core.config import get_settings


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
                                 and (a.scope = 'day' or a.marked_period is not null)
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


# --- bus position (GPS plan U8) ------------------------------------------------
# The served position is live_buses.current_lat/lng; position_source,
# position_at and position_accuracy_m describe that pair (U7 writes all five in
# one statement). Everything a reader says ABOUT the position — how old it is,
# whether it is stale, whether the run's phone has its GPS off, whether the run
# has produced no fix at all — is derived here, once, and read by the staff bus
# list, the parent Track query and the parent children/profile queries. Three
# readers with their own rules would drift the moment the position stopped
# being a planned stop.
#
# Rules:
# - a bus has a served position only when the pair is non-null; the qualifiers
#   are never read while it is null (they come back null too);
# - a non-null pair with a NULL source is a checkpoint of unknown age — the
#   legacy-writer and rollback shape — and is never stale;
# - `stale` = the source is known and the position is older than the staleness
#   threshold (Settings.gps_stale_after_s — a system default with no per-school
#   column, read at call time so an env override is never baked in at import;
#   U11). It applies to checkpoints and fixes alike: a stop reached four
#   minutes ago IS four minutes old, and R27 wants that said rather than hidden;
# - `gps_off` = the bus's in-progress run's LATEST action row inside retention
#   carries no fix for a device reason (denied / unavailable / timeout /
#   invalid). A fix on the next tap clears it with no write (F5, AE2);
# - `no_gps_for_run` = the in-progress run has action rows inside retention and
#   none of them carries coordinates (R20's derived "no GPS for this run");
# - trail reads filter by the school's retention (`position_retention_days`,
#   default Settings.gps_position_retention_days) by `received_at`, the purge's
#   own cutoff expression, so a lagging purge is invisible (R12);
# - the label is one rule for every surface: a fix reads "Phone GPS"; a
#   checkpoint reads where the run is — starting at the school, at the school,
#   or at the stop it last reached (the retired Python loop's wording).
#
# "In-progress run" is the bus's non-completed run dated today (Africa/Nairobi)
# — the same predicate the bus-status derivation uses; a prior-day run left open
# (R15) never feeds these columns.

# Ping rows join later (U14); the served position already carries the source.
POSITION_SOURCES = ("checkpoint", "action", "ping")
_NO_FIX_REASONS = "('denied', 'unavailable', 'timeout', 'invalid')"

_RUN_TODAY_SQL = """(
        select r.id from live_runs r
        where r.bus_id = {bus}.id
          and r.date = (now() at time zone 'Africa/Nairobi')::date
          and r.status <> 'completed'
        order by r.created_at desc limit 1
    )"""

# `now()` minus retention in SECONDS (position_dao.RETENTION_CUTOFF_SQL's
# form, restated here because this module imports nothing from app.dao): an
# interval's day field is calendar arithmetic in the session time zone.
# {retention_days} is the system default, filled per call from Settings.
_RETENTION_CUTOFF_SQL = (
    "now() - make_interval(secs => (select coalesce(sc.position_retention_days, "
    "{retention_days}) from live_schools sc where sc.id = {bus}.school_id) * 86400)"
)

# {stale_s} and {no_fix_reasons} are filled per call alongside the aliases.
_BUS_POSITION_COLUMNS = """{bus}.current_lat as pos_lat,
    {bus}.current_lng as pos_lng,
    case when {served} then {bus}.position_source end as pos_source,
    case when {served} then {bus}.position_at end as pos_at,
    case when {served} then {bus}.position_accuracy_m end as pos_accuracy_m,
    case when {served} and {bus}.position_at is not null
         then greatest(0, floor(extract(epoch from now() - {bus}.position_at)))::int
    end as pos_age_s,
    coalesce(
        {served}
        and {bus}.position_source is not null
        and {bus}.position_at < now() - make_interval(secs => {stale_s}),
        false
    ) as pos_stale,
    coalesce((
        select p.fix_reason in {no_fix_reasons}
        from run_positions p
        where p.run_id = {run}
          and p.source = 'action'
          and p.received_at >= {cutoff}
        order by p.received_at desc, p.id desc
        limit 1
    ), false) as pos_gps_off,
    coalesce((
        select count(*) filter (where p.lat is not null and p.lng is not null) = 0
        from run_positions p
        where p.run_id = {run}
          and p.source = 'action'
          and p.received_at >= {cutoff}
        having count(*) > 0
    ), false) as pos_no_gps_for_run,
    case
        when not {served} then null
        when {bus}.position_source in ('action', 'ping') then 'Phone GPS'
        else (
            select case
                     when coalesce(r.stops_completed, 0) <= 0 then 'Starting — at school'
                     when s.is_school_gate then 'At school'
                     when s.name is not null then 'At ' || s.name || case when exists (
                         select 1 from run_stops n
                         where n.run_id = r.id and n.stop_order = r.stops_completed + 1
                     ) then ' · en route to next' else '' end
                   end
            from live_runs r
            left join lateral (
                select rs.name, rs.is_school_gate from run_stops rs
                where rs.run_id = r.id and rs.stop_order = r.stops_completed
                order by rs.is_school_gate desc limit 1
            ) s on true
            where r.bus_id = {bus}.id
              and r.date = (now() at time zone 'Africa/Nairobi')::date
              and r.status <> 'completed'
            order by r.created_at desc limit 1
        )
    end as pos_label"""


def bus_position_columns(bus: str) -> str:
    """The position fragment: a comma-separated SELECT list (no leading
    comma) of ``pos_*`` columns, parameterized by the consuming query's
    ``live_buses`` table alias. Subquery aliases (r, rs, n, s, p, sc) are
    fragment-local. Consumers hand the fetched row to :func:`pop_position`.

    The staleness threshold and the retention default are read from Settings
    on every call (U11): both are ints from validated fields, interpolated as
    literals because the fragment is spliced into queries that bind their own
    parameters.
    """
    settings = get_settings()
    served = f"({bus}.current_lat is not null and {bus}.current_lng is not null)"
    return _BUS_POSITION_COLUMNS.format(
        bus=bus,
        served=served,
        run=_RUN_TODAY_SQL.format(bus=bus),
        cutoff=_RETENTION_CUTOFF_SQL.format(
            bus=bus, retention_days=int(settings.gps_position_retention_days)
        ),
        stale_s=int(settings.gps_stale_after_s),
        no_fix_reasons=_NO_FIX_REASONS,
    )


# Payload field -> fragment column. The staff payload carries every field; the
# parent allowlist (R37, R22) is lat, lng, position_at, stale — never source,
# accuracy, age or the run's GPS state.
_POSITION_FIELD_COLUMNS = {
    "lat": "pos_lat",
    "lng": "pos_lng",
    "source": "pos_source",
    "position_at": "pos_at",
    "accuracy_m": "pos_accuracy_m",
    "age_s": "pos_age_s",
    "stale": "pos_stale",
    "gps_off": "pos_gps_off",
    "no_gps_for_run": "pos_no_gps_for_run",
    "label": "pos_label",
}
STAFF_POSITION_FIELDS = tuple(_POSITION_FIELD_COLUMNS)
PARENT_POSITION_FIELDS = ("lat", "lng", "position_at", "stale")

# The five write-side columns (U7). Readers detach them from a `select b.*`
# row so the nested `position` object is the only shape a client can render.
RAW_POSITION_COLUMNS = (
    "current_lat", "current_lng", "position_source", "position_at", "position_accuracy_m",
)


def pop_position(row: dict, fields: tuple[str, ...] = STAFF_POSITION_FIELDS) -> dict | None:
    """Detach the fragment's ``pos_*`` columns from a fetched row (mutating
    it) and return the position object with exactly ``fields`` — or None when
    the pair is null, i.e. the bus has no served position."""
    values = {field: row.pop(column, None) for field, column in _POSITION_FIELD_COLUMNS.items()}
    if values["lat"] is None or values["lng"] is None:
        return None
    return {field: values[field] for field in fields}
