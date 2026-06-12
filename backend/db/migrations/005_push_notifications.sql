-- Push notifications: FCM device tokens and a per-parent notification feed.
-- The feed is the source of truth for what parents were told; FCM / web-push
-- delivery is best-effort on top of it.

create table if not exists live_fcm_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  token text not null unique,
  user_agent text,
  created_at timestamptz not null default now()
);

create index if not exists live_fcm_tokens_user_idx on live_fcm_tokens (user_id);

create table if not exists live_notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references app_users (id) on delete cascade,
  student_id uuid references live_students (id) on delete cascade,
  run_id uuid references live_runs (id) on delete set null,
  bus_id uuid references live_buses (id) on delete set null,
  type text not null check (
    type in (
      'run-started',
      'student-boarded',
      'bus-approaching',
      'reached-school',
      'on-way-home',
      'dropped-off',
      'incident',
      'custom'
    )
  ),
  title text not null,
  body text not null default '',
  read boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists live_notifications_user_idx
  on live_notifications (user_id, created_at desc);

-- Run-scoped notification types fire at most once per (parent, run, student,
-- type): GPS spam, double boarding toggles, and arrival retries dedup here.
create unique index if not exists live_notifications_run_dedup
  on live_notifications (user_id, run_id, student_id, type)
  where run_id is not null and student_id is not null;
