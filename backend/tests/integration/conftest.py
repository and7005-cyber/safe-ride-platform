"""Shared integration-test helpers.

Kept deliberately small: these tests drive the product through its own API on
purpose, and anything that goes around it hides a contract rather than checking
one. `purge_run` is the one sanctioned exception, and only for teardown — see
its docstring.
"""

import os

import psycopg

DSN = os.environ.get("DATABASE_URL", "postgresql://saferide:saferide@localhost:5432/saferide")


def purge_run(run_id: str | None) -> None:
    """Remove a run out-of-band. **Teardown only** — never inside an assertion.

    The suite's hygiene model is to delete a run once a test is done with it,
    because a completed run blocks the same route from starting again that day
    and the next test would be gated by the last one's leftovers.

    Since U7 the product refuses to delete a completed run dated today: its
    participation rows are the only evidence of who was on that bus, and
    cascading them would flip a whole roster from at school to at home in the
    middle of the day. That refusal is a real guarantee and tests must not have
    a product-level backdoor around it, so cleanup drops to SQL instead.

    run_stops, run_absences and run_participation all cascade on the run row.
    """
    if not run_id:
        return
    with psycopg.connect(DSN, autocommit=True) as pg:
        pg.execute("delete from live_runs where id = %s", (run_id,))
