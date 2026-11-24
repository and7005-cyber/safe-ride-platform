"""Email verification state (hardening Release 1b, decision D1) — one predicate.

Migration 017 added the flag instead of a backfill: an account is UNVERIFIED
exactly when ``email_verification_required`` is true and ``email_verified_at``
is null. Only Release 1b's sign-up and the office's email change set the flag;
every account made before 1b keeps the default false and is treated as
verified everywhere (grandfathered — never asked to verify).

Every reader of the state goes through these helpers: the session lookup
(``/me``'s ``emailVerified``), the parent-link rules (sign-up matching and the
office-side student sync), and the Parents page's "Awaiting signup" slots. A
second spelling of the rule would let one surface treat an account as
verified while another still waits for the click.
"""

from __future__ import annotations


def verified_sql(alias: str = "u") -> str:
    """True for a verified or grandfathered ``app_users`` row."""
    return (
        f"(not {alias}.email_verification_required"
        f" or {alias}.email_verified_at is not null)"
    )


def unverified_sql(alias: str = "u") -> str:
    """True for an account still waiting for its verification click."""
    return (
        f"({alias}.email_verification_required"
        f" and {alias}.email_verified_at is null)"
    )
