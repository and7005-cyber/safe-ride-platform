-- 012: rotate the seeded admin credential before the apply-capable release.
--
-- The demo seed's admin (admin@test.com) carried a password published in this
-- repository and authenticated against PRODUCTION — flagged in the
-- status-lifecycle validation record and gated as a blocker for the fleet-plan
-- apply release (docs/work/validation/2026-08-20-fleet-plan-drafting.md).
-- The replacement password lives in SSM SecureString /saferide/admin-password
-- (af-south-1); the PBKDF2 hash below is self-contained (embedded salt, no
-- pepper) and was generated with app.core.security.hash_password.
--
-- Forward-only and double-apply idempotent: a plain UPDATE to the same value.
-- LOCAL stacks are unaffected in practice: the local-only demo seed runs after
-- migrations and restores the published test password via its
-- on-conflict-do-update, so the integration suite's login contract holds.

update app_users
set password_hash = 'pbkdf2_sha256$200000$f4744f71eb2b6704a58097145c8b384d$G2GbJjRIOtRdfbbFbDfqN/Q8f31XmFq2lB+JeTxqteQ='
where email = 'admin@test.com';
