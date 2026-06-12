create extension if not exists pgcrypto;

create type trip_session as enum ('morning', 'afternoon', 'adhoc', 'staff');
create type trip_status as enum ('scheduled', 'active', 'delayed', 'issue_reported', 'completed', 'cancelled');
create type passenger_type as enum ('student', 'staff');
create type trip_passenger_status as enum ('pending', 'boarded', 'dropped', 'absent_admin', 'absent_driver', 'alternative_transport');
create type attendance_status as enum ('riding', 'absent', 'alternative_transport');
create type event_type as enum ('trip_started', 'passenger_boarded', 'passenger_not_present', 'passenger_dropped', 'trip_ended', 'issue_reported', 'missed_tap', 'admin_correction');
create type notification_status as enum ('pending', 'processing', 'sent', 'failed', 'skipped');

create table schools (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  approaching_threshold integer not null default 2 check (approaching_threshold >= 0),
  default_inter_student_minutes integer not null default 6 check (default_inter_student_minutes >= 0),
  created_at timestamptz not null default now()
);

create table admin_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  role text not null check (role in ('owner', 'admin')),
  created_at timestamptz not null default now()
);

create table buses (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  label text not null,
  registration_number text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (school_id, label)
);

create table drivers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  pin_hash text not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table driver_sessions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  driver_id uuid not null references drivers(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table students (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  home_address text not null,
  home_location_note text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table parent_contacts (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null references students(id) on delete cascade,
  contact_1_name text not null,
  contact_1_phone text not null check (contact_1_phone ~ '^\+254[0-9]{9}$'),
  contact_1_relationship text not null,
  contact_2_name text,
  contact_2_phone text check (contact_2_phone is null or contact_2_phone ~ '^\+254[0-9]{9}$'),
  contact_2_relationship text,
  created_at timestamptz not null default now(),
  unique (student_id)
);

create table staff_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  full_name text not null,
  phone text,
  home_address text not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table trips (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  bus_id uuid not null references buses(id) on delete restrict,
  driver_id uuid references drivers(id) on delete set null,
  name text not null,
  session trip_session not null,
  service_date date not null,
  scheduled_start time not null,
  status trip_status not null default 'scheduled',
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  unique (school_id, bus_id, service_date, name)
);

create table trip_passengers (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null references trips(id) on delete cascade,
  passenger_type passenger_type not null,
  student_id uuid references students(id) on delete cascade,
  staff_passenger_id uuid references staff_passengers(id) on delete cascade,
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
  unique (trip_id, sequence_position)
);

create table daily_attendance (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null references students(id) on delete cascade,
  attendance_date date not null,
  status attendance_status not null,
  marked_by uuid references auth.users(id),
  marked_at timestamptz not null default now(),
  note text,
  unique (student_id, attendance_date)
);

create table parent_links (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  student_id uuid not null references students(id) on delete cascade,
  token text not null unique check (length(token) >= 32),
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table trip_events (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_id uuid not null references trips(id) on delete cascade,
  trip_passenger_id uuid references trip_passengers(id) on delete set null,
  event_type event_type not null,
  created_by_role text not null check (created_by_role in ('admin', 'driver', 'system')),
  created_by_id uuid,
  occurred_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table audit_log (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  entity_table text not null,
  entity_id uuid not null,
  admin_user_id uuid not null references auth.users(id),
  original_value jsonb not null,
  corrected_value jsonb not null,
  reason text not null,
  created_at timestamptz not null default now()
);

create table notification_outbox (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  trip_event_id uuid references trip_events(id) on delete cascade,
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
  sent_at timestamptz
);

create table push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  school_id uuid not null references schools(id) on delete cascade,
  parent_link_id uuid not null references parent_links(id) on delete cascade,
  endpoint text not null,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now(),
  unique (parent_link_id, endpoint)
);

alter table buses add constraint buses_id_school_id_key unique (id, school_id);
alter table drivers add constraint drivers_id_school_id_key unique (id, school_id);
alter table driver_sessions add constraint driver_sessions_id_school_id_key unique (id, school_id);
alter table students add constraint students_id_school_id_key unique (id, school_id);
alter table staff_passengers add constraint staff_passengers_id_school_id_key unique (id, school_id);
alter table trips add constraint trips_id_school_id_key unique (id, school_id);
alter table trip_passengers add constraint trip_passengers_id_school_id_key unique (id, school_id);
alter table parent_links add constraint parent_links_id_school_id_key unique (id, school_id);
alter table trip_events add constraint trip_events_id_school_id_key unique (id, school_id);
alter table push_subscriptions add constraint push_subscriptions_id_school_id_key unique (id, school_id);

alter table parent_contacts
  add constraint parent_contacts_student_school_fkey
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade;

alter table trips
  add constraint trips_bus_school_fkey
  foreign key (bus_id, school_id) references buses(id, school_id) on delete restrict,
  add constraint trips_driver_school_fkey
  foreign key (driver_id, school_id) references drivers(id, school_id) on delete set null (driver_id);

alter table trip_passengers
  add constraint trip_passengers_trip_school_fkey
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  add constraint trip_passengers_student_school_fkey
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade,
  add constraint trip_passengers_staff_school_fkey
  foreign key (staff_passenger_id, school_id) references staff_passengers(id, school_id) on delete cascade;

alter table daily_attendance
  add constraint daily_attendance_student_school_fkey
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade;

alter table parent_links
  add constraint parent_links_student_school_fkey
  foreign key (student_id, school_id) references students(id, school_id) on delete cascade;

alter table trip_events
  add constraint trip_events_trip_school_fkey
  foreign key (trip_id, school_id) references trips(id, school_id) on delete cascade,
  add constraint trip_events_trip_passenger_school_fkey
  foreign key (trip_passenger_id, school_id) references trip_passengers(id, school_id) on delete set null (trip_passenger_id);

alter table notification_outbox
  add constraint notification_outbox_push_subscription_id_fkey
  foreign key (push_subscription_id) references push_subscriptions(id) on delete set null,
  add constraint notification_outbox_trip_event_school_fkey
  foreign key (trip_event_id, school_id) references trip_events(id, school_id) on delete cascade,
  add constraint notification_outbox_push_subscription_school_fkey
  foreign key (push_subscription_id, school_id) references push_subscriptions(id, school_id) on delete set null (push_subscription_id);

alter table push_subscriptions
  add constraint push_subscriptions_parent_link_school_fkey
  foreign key (parent_link_id, school_id) references parent_links(id, school_id) on delete cascade;


create index admin_profiles_school_id_idx on admin_profiles (school_id);
create index buses_school_id_idx on buses (school_id);
create index drivers_school_id_idx on drivers (school_id);
create index driver_sessions_driver_id_idx on driver_sessions (driver_id);
create index driver_sessions_token_hash_idx on driver_sessions (token_hash);
create index driver_sessions_expires_at_idx on driver_sessions (expires_at);
create index students_school_id_idx on students (school_id);
create index parent_contacts_school_id_idx on parent_contacts (school_id);
create index parent_contacts_student_id_idx on parent_contacts (student_id);
create index staff_passengers_school_id_idx on staff_passengers (school_id);
create index trips_bus_id_idx on trips (bus_id);
create index trips_school_id_service_date_idx on trips (school_id, service_date);
create index trips_driver_service_date_idx on trips (driver_id, service_date) where driver_id is not null;
create index trip_passengers_school_id_idx on trip_passengers (school_id);
create index trip_passengers_trip_id_idx on trip_passengers (trip_id);
create index trip_passengers_student_id_idx on trip_passengers (student_id) where student_id is not null;
create index trip_passengers_staff_passenger_id_idx on trip_passengers (staff_passenger_id) where staff_passenger_id is not null;
create index daily_attendance_school_date_idx on daily_attendance (school_id, attendance_date);
create index parent_links_school_id_idx on parent_links (school_id);
create index parent_links_student_id_idx on parent_links (student_id);
create index trip_events_school_trip_occurred_idx on trip_events (school_id, trip_id, occurred_at desc);
create index trip_events_trip_passenger_id_idx on trip_events (trip_passenger_id) where trip_passenger_id is not null;
create index audit_log_school_created_idx on audit_log (school_id, created_at desc);
create index audit_log_admin_user_id_idx on audit_log (admin_user_id);
create index notification_outbox_school_status_idx on notification_outbox (school_id, status, created_at);
create index notification_outbox_trip_event_id_idx on notification_outbox (trip_event_id) where trip_event_id is not null;
create index notification_outbox_push_subscription_id_idx on notification_outbox (push_subscription_id) where push_subscription_id is not null;
create index push_subscriptions_school_id_idx on push_subscriptions (school_id);
create index push_subscriptions_parent_link_id_idx on push_subscriptions (parent_link_id);

create or replace function current_school_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select school_id
  from admin_profiles
  where id = auth.uid()
$$;

create or replace function current_admin_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select role
  from admin_profiles
  where id = auth.uid()
$$;

alter table schools enable row level security;
alter table admin_profiles enable row level security;
alter table buses enable row level security;
alter table drivers enable row level security;
alter table driver_sessions enable row level security;
alter table students enable row level security;
alter table parent_contacts enable row level security;
alter table staff_passengers enable row level security;
alter table trips enable row level security;
alter table trip_passengers enable row level security;
alter table daily_attendance enable row level security;
alter table parent_links enable row level security;
alter table trip_events enable row level security;
alter table audit_log enable row level security;
alter table notification_outbox enable row level security;
alter table push_subscriptions enable row level security;

create policy "authenticated admins see own school" on schools
for select
to authenticated
using (id = current_school_id());

create policy "authenticated admins select own school profiles" on admin_profiles
for select
to authenticated
using (school_id = current_school_id());

create policy "authenticated owners insert own school profiles" on admin_profiles
for insert
to authenticated
with check (
  school_id = current_school_id()
  and current_admin_role() = 'owner'
);

create policy "authenticated owners update own school profiles" on admin_profiles
for update
to authenticated
using (
  school_id = current_school_id()
  and current_admin_role() = 'owner'
)
with check (
  school_id = current_school_id()
  and current_admin_role() = 'owner'
);

create policy "authenticated owners delete own school profiles" on admin_profiles
for delete
to authenticated
using (
  school_id = current_school_id()
  and current_admin_role() = 'owner'
);

create policy "authenticated admins manage own school buses" on buses
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school drivers" on drivers
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins view own school driver sessions" on driver_sessions
for select
to authenticated
using (school_id = current_school_id());

create policy "authenticated admins manage own school students" on students
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school parent contacts" on parent_contacts
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school staff passengers" on staff_passengers
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school trips" on trips
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school trip passengers" on trip_passengers
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school attendance" on daily_attendance
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school parent links" on parent_links
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school trip events" on trip_events
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins select own school audit log" on audit_log
for select
to authenticated
using (school_id = current_school_id());

create policy "authenticated admins manage own school notification outbox" on notification_outbox
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "authenticated admins manage own school push subscriptions" on push_subscriptions
for all
to authenticated
using (school_id = current_school_id())
with check (school_id = current_school_id());
