"""Per-run, per-child participation — what actually happened on a run (U2).

Boarding used to exist only in ``live_students.status``: a single mutable column
with no history, so a run could not reconstruct who was on it. That is why
ending a run had to sweep every roster child to a terminal status, why the
run-end path snapshotted boarded children into memory before the sweep just to
address notifications, and why a read-time staleness derivation exists at all.

Every function here takes the caller's connection and never opens its own — the
run write paths hold multi-statement invariants inside one transaction, and a
participation row must land or roll back with the status write beside it.

Timestamps carry the facts; ``boarded_presumed`` is the one flag, marking the
afternoon auto-board as an assumption rather than an observation.
"""

from typing import Any


def record_boarding(
    conn, run_id: str, student_id: str, student_name: str, driver_id: str | None,
    *, presumed: bool = False,
) -> None:
    """Record that a child boarded. Idempotent: a repeated tap is the same fact.

    A confirmed boarding never downgrades to presumed — the afternoon auto-board
    runs first and the driver may confirm afterwards, not the other way round.
    """
    conn.execute(
        """
        insert into run_participation
            (run_id, student_id, student_name, boarded_at, boarded_presumed, acting_driver_id)
        values (%s, %s, %s, now(), %s, %s)
        on conflict (run_id, student_id) do update set
            boarded_at = coalesce(run_participation.boarded_at, excluded.boarded_at),
            boarded_presumed = run_participation.boarded_presumed and excluded.boarded_presumed,
            acting_driver_id = coalesce(excluded.acting_driver_id, run_participation.acting_driver_id)
        """,
        (run_id, student_id, student_name, presumed, driver_id),
    )


def confirm_boarding(conn, run_id: str, student_id: str, driver_id: str | None) -> None:
    """Turn a presumed board into an observed one, recording who confirmed it."""
    conn.execute(
        """
        update run_participation
        set boarded_presumed = false,
            boarded_at = coalesce(boarded_at, now()),
            acting_driver_id = coalesce(%s, acting_driver_id)
        where run_id = %s and student_id = %s
        """,
        (driver_id, run_id, student_id),
    )


def record_dropoff(conn, run_id: str, student_id: str, driver_id: str | None) -> None:
    """Confirm a drop-off at the child's own stop."""
    conn.execute(
        """
        update run_participation
        set dropped_off_at = coalesce(dropped_off_at, now()),
            boarded_presumed = false,
            acting_driver_id = coalesce(%s, acting_driver_id)
        where run_id = %s and student_id = %s
        """,
        (driver_id, run_id, student_id),
    )


def record_handover(
    conn, run_id: str, student_id: str, driver_id: str | None, note: str
) -> None:
    """Record a hand-over away from the child's stop — a breakdown, a closed
    road, a guardian collecting at the roadside.

    Accounted for, with the driver's note, and no absence row: the child was on
    the bus, so asserting otherwise would be the manufactured claim this work
    exists to remove.
    """
    conn.execute(
        """
        update run_participation
        set handover_at = coalesce(handover_at, now()),
            handover_note = %s,
            boarded_presumed = false,
            acting_driver_id = coalesce(%s, acting_driver_id)
        where run_id = %s and student_id = %s
        """,
        (note, driver_id, run_id, student_id),
    )


def reverse_outcome(conn, run_id: str, student_id: str) -> None:
    """Undo a confirmed drop-off or hand-over, leaving the boarding intact (U5).

    The child is back in the blocking set: they were on the bus and their real
    outcome has not been recorded yet, which is exactly what the closure gate
    should refuse to close over.
    """
    conn.execute(
        """
        update run_participation
        set dropped_off_at = null, handover_at = null, handover_note = null
        where run_id = %s and student_id = %s
        """,
        (run_id, student_id),
    )


def clear_for_student(conn, run_id: str, student_id: str) -> None:
    """Drop a child's participation on this run — used when an absence removes
    them from the roster's expectations."""
    conn.execute(
        "delete from run_participation where run_id = %s and student_id = %s",
        (run_id, student_id),
    )


def count_boarded(conn, run_id: str) -> int:
    """Children recorded as aboard this run, presumed or confirmed.

    Recomputed from participation inside the caller's transaction rather than
    incremented — mobile retry is the documented threat model, and the old
    counter read a status column that no longer tracks boarding.
    """
    row = conn.execute(
        "select count(*) as n from run_participation "
        "where run_id = %s and boarded_at is not null and student_id is not null",
        (run_id,),
    ).fetchone()
    return row["n"] if row else 0


def count_dropped_off(conn, run_id: str) -> int:
    """Children with a confirmed drop-off or a recorded hand-over."""
    row = conn.execute(
        "select count(*) as n from run_participation "
        "where run_id = %s and student_id is not null "
        "  and (dropped_off_at is not null or handover_at is not null)",
        (run_id,),
    ).fetchone()
    return row["n"] if row else 0


def confirmed_boarded_ids(conn, run_id: str) -> list[str]:
    """Children the driver actually observed boarding.

    The arrival notification keys on this: a presumed board is not evidence a
    child rode, and asserting arrival for one would be a false safety claim.
    """
    rows = conn.execute(
        "select student_id from run_participation "
        "where run_id = %s and boarded_at is not null and boarded_presumed = false "
        "  and student_id is not null",
        (run_id,),
    ).fetchall()
    return [str(r["student_id"]) for r in rows]


def unaccounted_on_run(conn, run_id: str, run_type: str) -> list[dict[str, Any]]:
    """Roster children with no recorded outcome on this run (U4).

    Accounted for means one of five things, and the fifth is easy to miss:

    - boarded, on a morning run;
    - drop-off confirmed, on an afternoon run;
    - an off-route hand-over recorded;
    - covered by an absence today whose scope covers this run's period;
    - **confirmed aboard a different run of the same period today.** Without
      that condition a cross-bus rider — a child whose afternoon route rides
      another bus — has no truthful release on their first bus: they were not
      boarded here, were not dropped off here, and marking them absent would be
      a lie. The run could never close, and the office force-close would then
      record them unaccounted while another run holds a confirmed boarding.

    The roster is the run's own snapshot (run_stops), never the derived bus
    roster, which drifts for exactly those cross-bus riders.

    Returns the blocking children with their names, so the driver and the office
    alert can both name them rather than showing a generic failure.
    """
    outcome = (
        "(p.dropped_off_at is not null or p.handover_at is not null)"
        if run_type == "afternoon"
        else "p.boarded_at is not null"
    )
    rows = conn.execute(
        f"""
        select distinct s.id, s.name
        from run_stops rs
        join live_students s on s.id = rs.student_id
        left join run_participation p
            on p.run_id = rs.run_id and p.student_id = rs.student_id
        where rs.run_id = %(run_id)s
          and rs.student_id is not null
          and not ({outcome})
          and not exists (
              select 1 from live_student_absences a
              where a.student_id = s.id
                and a.absence_date = (now() at time zone 'Africa/Nairobi')::date
                and (a.scope = 'day' or a.scope = %(run_type)s)
          )
          and not exists (
              select 1
              from run_participation op
              join live_runs orun on orun.id = op.run_id
              where op.student_id = s.id
                and op.run_id <> rs.run_id
                and orun.date = (now() at time zone 'Africa/Nairobi')::date
                and orun.type = %(run_type)s
                and (
                    (op.boarded_at is not null and op.boarded_presumed = false)
                    or op.dropped_off_at is not null
                    or op.handover_at is not null
                )
          )
        order by s.name asc
        """,
        {"run_id": run_id, "run_type": run_type},
    ).fetchall()
    return [dict(r) for r in rows]


def get_for_student(conn, run_id: str, student_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select * from run_participation where run_id = %s and student_id = %s",
        (run_id, student_id),
    ).fetchone()
    return dict(row) if row else None
