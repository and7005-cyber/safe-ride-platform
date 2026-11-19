-- 017: account hardening — the email-verification flag, the consent record,
-- the token purpose, the per-school driver code, the PIN-failure window and
-- the job-run witness (U2, Release 1a of
-- docs/plans/2026-11-16-001-feat-production-hardening-plan.md, as amended by
-- the execution decisions D1–D5 recorded in the Release 1a validation record).
--
-- Schema only, one release ahead of the code that reads it (Release 1b).
-- Ordered so the most-referenced tables are locked last: on the migrate
-- Lambda's path every lock is held until the single transaction commits, so
-- the hot parents are held for the shortest time and are taken after the
-- lesser tables, never before them. (1) the job-run witness, a new table that
-- references nothing; (2) the notification-feed index and the audit CHECK
-- widening, the verbatim union of the CURRENT list plus the new values;
-- (3) the token purpose on password_reset_tokens; (4) the verification and
-- consent columns on app_users; (5) live_schools, the parent of nearly every
-- school-owned table, LAST: the driver-code generator, then its column, fill,
-- default, NOT NULL and unique index, then the PIN-failure window, whose
-- foreign key takes SHARE ROW EXCLUSIVE on live_schools. The ALTER comes
-- first inside that group so the foreign key finds ACCESS EXCLUSIVE already
-- held and never upgrades a weaker lock. One ALTER per existing table (two on
-- live_schools, either side of the fill); the two new tables carry no row
-- level security, like password_reset_tokens.
--
-- The migrate runner executes this file as ONE implicit transaction
-- (pgconn.exec_), so SET LOCAL is correct here and every statement is
-- idempotent: local psql autocommits per statement, and a failed local apply
-- is half-applied and already marked — reset, never resume.
--
-- No empty-database guard: nothing here depends on data (the driver-code fill
-- is a no-op on an empty live_schools, and the column default covers every
-- school inserted afterwards — seeds included).
--
-- No backfill (D1): "unverified" means email_verification_required = true
-- and email_verified_at is null. Every account that exists today — and every
-- account the old code creates during the coexistence week — keeps the
-- default false and is grandfathered; only Release 1b's sign-up and office
-- email change set the flag.
--
-- Rollback-neutral for older code by construction: the app_users columns are
-- nullable or default false; purpose defaults to 'reset', the only kind the
-- old code writes; driver_code is filled by its own default on every insert
-- path, including those that do not name it; the new tables are unread by
-- the old code.
--
-- Double-apply is a no-op end to end.

set local lock_timeout = '5s';
set local statement_timeout = '90s';

-- Refused before any lock is taken: the grants below name the runtime role.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'saferide_app') then
    raise exception '017: the saferide_app role is missing — run the role sync first';
  end if;
end
$$;

-- Runtime-role privileges, for both new tables: the blanket role sync (013's
-- default privileges and migrate_handler._sync_app_role) already gives
-- saferide_app select, insert, update and delete on every table the master
-- role creates. The explicit grants below keep this file self-sufficient and
-- name what 1b uses; they narrow nothing.

-- (1) The job-run witness ------------------------------------------------------------

-- The retention job's witness (D4): one row per nightly run, written by the
-- migrate Lambda (master role) and read by the verify Lambda's hardening set.
-- A budget stop is 'stopped', which is success; counts carries the per-table
-- deletions. No RLS: operational, not school data.
create table if not exists ops_job_runs (
  id bigserial primary key,
  job text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null check (status in ('running', 'ok', 'stopped', 'failed')),
  counts jsonb not null default '{}'
);
-- The API reads the witness. The role sync's DML means saferide_app could
-- write it too; only the migrate Lambda does, by convention.
grant select on public.ops_job_runs to saferide_app;

-- (2) The notification-feed index and the audit CHECK widening ----------------------

-- The nightly feed purge deletes rows older than 90 days across all users;
-- live_notifications_user_idx leads with user_id and cannot serve that range.
-- A plain (non-concurrent) build: SHARE lock for the seconds it takes, inside
-- the 90 s statement timeout. No index on auth_sessions (its purge is small).
create index if not exists live_notifications_created_idx
  on live_notifications (created_at);

-- Recreated in ONE statement as the verbatim union of its CURRENT list plus
-- the new values. Never copy the list from an older migration: the local
-- seeds do not hold every type, so a stale list passes local rehearsal and
-- fails only on live.
--
-- Audit: 016's forty-eight values (013's forty-seven plus the exception
-- review) plus the office parent reset, the office sign-up confirmation, the
-- office email change and the driver-code rotation.
alter table live_admin_audit
  drop constraint if exists live_admin_audit_action_check,
  add constraint live_admin_audit_action_check check (
    action in (
      'plan-applied', 'plan-restored', 'pin-map-viewed',
      'staff-created', 'staff-role-offered', 'staff-offer-accepted',
      'staff-offer-declined', 'staff-offer-cancelled', 'staff-role-removed',
      'staff-password-reset',
      'school-created', 'school-updated',
      'bus-created', 'bus-updated', 'bus-deleted',
      'driver-created', 'driver-updated', 'driver-deleted',
      'student-created', 'student-updated', 'student-deleted', 'students-imported',
      'route-created', 'route-updated', 'route-deleted',
      'run-created', 'run-updated', 'run-deleted', 'run-force-closed',
      'absence-marked', 'absence-cleared',
      'incident-acknowledged', 'incident-deleted',
      'plan-drafted', 'plan-updated', 'plan-discarded',
      'slot-in-accepted', 'slot-in-dismissed',
      'broadcast-sent',
      'parent-updated', 'parent-deleted', 'parent-link-declined',
      'provider-step-in', 'provider-step-out',
      'provider-account-created', 'provider-account-removed', 'provider-totp-reset',
      'exception-reviewed',
      'parent-password-reset', 'parent-email-confirmed', 'parent-email-changed',
      'driver-code-regenerated'
    )
  );

-- (3) The token purpose ----------------------------------------------------------------

-- One token table, two purposes: reset (30 minutes) and verify (24 hours).
-- Consumption is purpose-scoped in 1b, so a verify token never resets a
-- password. Every existing row is a reset token and reads 'reset'.
alter table password_reset_tokens
  add column if not exists purpose text not null default 'reset'
    check (purpose in ('reset', 'verify'));
create index if not exists password_reset_tokens_user_purpose_idx
  on password_reset_tokens (user_id, purpose);

-- (4) Verification and consent on app_users ------------------------------------------

-- Verification and consent (D1, R32). Constant defaults and nullable columns:
-- catalogue-only changes, no table rewrite.
alter table app_users
  add column if not exists email_verification_required boolean not null default false,
  add column if not exists email_verified_at timestamptz,
  add column if not exists consent_version text,
  add column if not exists consented_at timestamptz;

-- (5) live_schools last: the driver code and the PIN-failure window ------------------

-- The driver code (D2): eight characters of Crockford base32 — 40 bits from
-- the CSPRNG behind gen_random_uuid() (core since PG13), five bits per
-- character, so every symbol is equally likely (no modulo bias). Bytes 0–4 of
-- a version-4 uuid are all random (the version nibble is in byte 6, the
-- variant bits in byte 8). Stored uppercase with no hyphen; the 1b code
-- displays it XXXX-XXXX and normalises input (uppercase, strip hyphens and
-- spaces, O->0, I/L->1) before matching. VOLATILE, so the column default
-- below draws a fresh code per row.
create or replace function saferide_new_driver_code()
returns text
language plpgsql
volatile
as $$
declare
  alphabet constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  raw constant bytea := substring(uuid_send(gen_random_uuid()) from 1 for 5);
  bits bigint := 0;
  code text := '';
begin
  for i in 0..4 loop
    bits := (bits << 8) | get_byte(raw, i);
  end loop;
  for i in reverse 7..0 loop
    code := code || substr(alphabet, ((bits >> (5 * i)) & 31)::integer + 1, 1);
  end loop;
  return code;
end
$$;
-- Every school insert path evaluates the column default as saferide_app.
grant execute on function public.saferide_new_driver_code() to saferide_app;

-- Added nullable, every existing school filled, then the default and NOT
-- NULL so every later insert — old code and seeds that do not name the
-- column included — draws its own. The unique index refuses a (40-bit,
-- practically impossible) collision; the transaction then rolls back and a
-- re-run draws fresh codes. The public AAA-NNN value stays the "school
-- code"; this one appears only on the school's staff pages (and is typed
-- once by each driver).
alter table live_schools
  add column if not exists driver_code text;
update live_schools
  set driver_code = saferide_new_driver_code()
  where driver_code is null;
alter table live_schools
  alter column driver_code set default saferide_new_driver_code(),
  alter column driver_code set not null;
create unique index if not exists live_schools_driver_code_key
  on live_schools (driver_code);

-- Failed PIN sign-ins (D3): one row per refused attempt, keyed by school and
-- the client address so 1b can lock on (school, source) and alarm on the
-- per-school aggregate. User-owned like password_reset_tokens: no RLS, never
-- in SCHOOL_OWNED_TABLES (the sign-in path runs before any school scope
-- exists). Purged nightly after a day; the school delete cascades.
create table if not exists pin_login_failures (
  school_id uuid not null references live_schools (id) on delete cascade,
  source text not null,
  attempted_at timestamptz not null default now()
);
create index if not exists pin_login_failures_school_attempted_idx
  on pin_login_failures (school_id, attempted_at);
-- The sign-in path writes, counts and clears failures.
grant select, insert, delete on public.pin_login_failures to saferide_app;
