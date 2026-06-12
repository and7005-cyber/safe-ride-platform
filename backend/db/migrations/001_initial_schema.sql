create extension if not exists pgcrypto;

do $$ begin create type trip_session as enum ('morning', 'afternoon', 'adhoc', 'staff'); exception when duplicate_object then null; end $$;
do $$ begin create type trip_status as enum ('scheduled', 'active', 'delayed', 'issue_reported', 'completed', 'cancelled'); exception when duplicate_object then null; end $$;
do $$ begin create type passenger_type as enum ('student', 'staff'); exception when duplicate_object then null; end $$;
do $$ begin create type trip_passenger_status as enum ('pending', 'boarded', 'dropped', 'absent_admin', 'absent_driver', 'alternative_transport'); exception when duplicate_object then null; end $$;
do $$ begin create type attendance_status as enum ('riding', 'absent', 'alternative_transport'); exception when duplicate_object then null; end $$;
do $$ begin create type event_type as enum ('trip_started', 'passenger_boarded', 'passenger_not_present', 'passenger_dropped', 'trip_ended', 'issue_reported', 'missed_tap', 'admin_correction'); exception when duplicate_object then null; end $$;
do $$ begin create type notification_status as enum ('pending', 'processing', 'sent', 'failed', 'skipped'); exception when duplicate_object then null; end $$;

create table if not exists schools (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  approaching_threshold integer not null default 2 check (approaching_threshold >= 0),
  default_inter_student_minutes integer not null default 6 check (default_inter_student_minutes >= 0),
  created_at timestamptz not null default now()
);

create table if not exists buses (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  label text not null,
  registration_number text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (school_id, label),
  unique (id, school_id)
);

create table if not exists drivers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  default_bus_id uuid,
  pin_hash text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id),
  foreign key (default_bus_id, school_id) references buses(id, school_id) on delete set null (default_bus_id)
);

create table if not exists driver_sessions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  driver_id uuid not null,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, school_id),
  foreign key (driver_id, school_id) references drivers(id, school_id) on delete cascade
);

create table if not exists students (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  home_address text not null,
  home_location_note text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists parent_contacts (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  contact_1_name text not null,
  contact_1_phone text not null check (contact_1_phone ~ '^\+254[0-9]{9}$'),
  contact_1_relationship text not null,
  contact_2_name text,
  contact_2_phone text check (contact_2_phone is null or contact_2_phone ~ '^\+254[0-9]{9}$'),
  contact_2_relationship text,
  created_at timestamptz not null default now(),
  unique (student_id),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists staff_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  home_address text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, school_id)
);

create table if not exists trips (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  bus_id uuid not null,
  driver_id uuid,
  name text not null,
  session trip_session not null,
  service_date date not null,
  scheduled_start time not null,
  status trip_status not null default 'scheduled',
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  unique (school_id, bus_id, service_date, name),
  unique (id, school_id),
  foreign key (bus_id, school_id) references buses(id, school_id) on delete restrict,
  foreign key (driver_id, school_id) references drivers(id, school_id) on delete set null (driver_id)
);

create table if not exists trip_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null,
  passenger_type passenger_type not null,
  student_id uuid,
  staff_passenger_id uuid,
  sequence_position integer not null check (sequence_position > 0),
  estimated_minutes_from_start integer not null check (estimated_minutes_from_start >= 0),
  actual_pickup_time timestamptz,
  actual_dropoff_time timestamptz,
  status trip_passenger_status not null default 'pending',
  created_at timestamptz not null default now(),
  check (
    (passenger_type = 'student' and student_id is not null and staff_passenger_id is null)
    or
    (passenger_type = 'staff' and staff_passenger_id is not null and student_id is null)
  ),
  unique (trip_id, sequence_position),
  unique (id, school_id),
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade,
  foreign key (staff_passenger_id, school_id) references staff_passengers(id, school_id) on delete cascade
);

create table if not exists daily_attendance (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  attendance_date date not null,
  status attendance_status not null,
  marked_by text,
  marked_at timestamptz not null default now(),
  note text,
  unique (student_id, attendance_date),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists parent_links (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null,
  token text not null unique check (length(token) >= 32),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (id, school_id),
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade
);

create table if not exists trip_events (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null,
  trip_passenger_id uuid,
  event_type event_type not null,
  created_by_role text not null check (created_by_role in ('admin', 'driver', 'system')),
  created_by_id uuid,
  occurred_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  unique (id, school_id),
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  foreign key (trip_passenger_id, school_id) references trip_passengers(id, school_id) on delete set null (trip_passenger_id)
);

create table if not exists audit_log (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  entity_table text not null,
  entity_id uuid not null,
  admin_actor text not null default 'local-admin',
  original_value jsonb not null,
  corrected_value jsonb not null,
  reason text not null,
  created_at timestamptz not null default now()
);

create table if not exists notification_outbox (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_event_id uuid,
  recipient_kind text not null check (recipient_kind in ('parent', 'admin')),
  recipient_phone text,
  push_subscription_id uuid,
  channel text not null check (channel in ('sms', 'push', 'email')),
  template_key text not null,
  payload jsonb not null default '{}'::jsonb,
  status notification_status not null default 'pending',
  attempts integer not null default 0 check (attempts >= 0),
  last_error text,
  claimed_at timestamptz,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  foreign key (trip_event_id, school_id) references trip_events(id, school_id) on delete cascade
);

create table if not exists push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  parent_link_id uuid not null,
  endpoint text not null,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now(),
  unique (parent_link_id, endpoint),
  unique (id, school_id),
  foreign key (parent_link_id, school_id) references parent_links(id, school_id) on delete cascade
);

do $$
begin
  alter table notification_outbox
    add constraint notification_outbox_push_subscription_id_fkey
    foreign key (push_subscription_id) references push_subscriptions(id) on delete set null;
exception when duplicate_object then null;
end $$;

do $$
begin
  alter table notification_outbox
    add constraint notification_outbox_push_subscription_school_fkey
    foreign key (push_subscription_id, school_id) references push_subscriptions(id, school_id) on delete set null (push_subscription_id);
exception when duplicate_object then null;
end $$;

create index if not exists buses_school_id_idx on buses (school_id);
create index if not exists drivers_school_id_idx on drivers (school_id);
create index if not exists drivers_default_bus_id_idx on drivers (default_bus_id) where default_bus_id is not null;
create index if not exists driver_sessions_token_hash_idx on driver_sessions (token_hash);
create index if not exists driver_sessions_expires_at_idx on driver_sessions (expires_at);
create index if not exists students_school_id_idx on students (school_id);
create index if not exists parent_contacts_student_id_idx on parent_contacts (student_id);
create index if not exists trips_school_id_service_date_idx on trips (school_id, service_date);
create index if not exists trips_driver_service_date_idx on trips (driver_id, service_date) where driver_id is not null;
create index if not exists trip_passengers_trip_id_idx on trip_passengers (trip_id);
create index if not exists daily_attendance_school_date_idx on daily_attendance (school_id, attendance_date);
create index if not exists parent_links_token_idx on parent_links (token);
create index if not exists trip_events_school_trip_occurred_idx on trip_events (school_id, trip_id, occurred_at desc);
create index if not exists notification_outbox_school_status_idx on notification_outbox (school_id, status, created_at);
create index if not exists push_subscriptions_parent_link_id_idx on push_subscriptions (parent_link_id);
