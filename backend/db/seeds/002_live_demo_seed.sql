-- Live-model demo seed. Idempotent (fixed UUIDs + ON CONFLICT upserts).
-- Doubles as the edge-case fixture set (siblings sharing a stop, a student
-- with no coordinates, a bus-less child, a parked GPS bus, completed-only
-- runs for the demo driver so Start Run works in e2e).
--
-- Test credentials (LOCAL DEV ONLY — rotate before any repo visibility change):
--   admin@test.com     / test1234.   (role admin)
--   and7005@gmail.com  / Test1234    (role parent)
--   and7005@yahoo.it   / Test1234    (role driver, PIN 1234)
-- Password/PIN hashes below were generated with app.core.security helpers and
-- the local PIN_PEPPER 'saferide-local-pin-pepper'.

-- DEMO SEED — LOCAL DEV ONLY. Guarded so it can never run against a database
-- unless the session explicitly opts in (the local scripts do this for you):
--   set saferide.allow_demo_seed = 'yes';
do $$
begin
  if coalesce(current_setting('saferide.allow_demo_seed', true), '') <> 'yes' then
    raise exception 'Demo seed blocked: local development only. Set saferide.allow_demo_seed = ''yes'' in this session to apply it.';
  end if;
end $$;

-- Identity -----------------------------------------------------------------

insert into app_users (id, email, password_hash, full_name, phone, pin_hash) values
  ('a0000000-0000-0000-0000-000000000001', 'admin@test.com',    'pbkdf2_sha256$200000$seedsaltadmin000$KE0yqk+lLnLKBtBTxl0WeQ++LBE9jr5S+ROr8LBrmeg=', 'Greenfield Admin',  '+254700000001', null),
  ('a0000000-0000-0000-0000-000000000002', 'and7005@gmail.com', 'pbkdf2_sha256$200000$seedsaltparent00$fq8xMN0hAmQISgkN5BnsPiz5JjH4rczW1YKlEZZwlfo=', 'Amina Parent',      '+254700000002', null),
  ('a0000000-0000-0000-0000-000000000003', 'and7005@yahoo.it',  'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Daniel Kamau',      '+254700000003', 'hmac_sha256$c357520dbffb5c590c0248a2916c8f9703486ada1e5c7d79e85fb312fdff4e55'),
  ('a0000000-0000-0000-0000-000000000004', 'francis@saferide.test', 'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Francis Ochieng', '+254700000004', 'hmac_sha256$4c6a0f51f37ab8eceb0147bddc2ab2349f675c5746bc75a34dcbab558eb8112c'),
  ('a0000000-0000-0000-0000-000000000005', 'mary@saferide.test',    'pbkdf2_sha256$200000$seedsaltdriver00$GmCr4H2kX/E4aT6whJEI/LDTB2xgfHaMtKXTTQlJ86w=', 'Mary Wanjiru',    '+254700000005', 'hmac_sha256$4d09f578c0d3bfd445c3477650aa4c68ed6db13f27eae306e60eaf9c06c36efd')
on conflict (id) do update set
  email = excluded.email, password_hash = excluded.password_hash,
  full_name = excluded.full_name, phone = excluded.phone, pin_hash = excluded.pin_hash;

insert into app_user_roles (user_id, role) values
  ('a0000000-0000-0000-0000-000000000001', 'admin'),
  ('a0000000-0000-0000-0000-000000000002', 'parent'),
  ('a0000000-0000-0000-0000-000000000003', 'driver'),
  ('a0000000-0000-0000-0000-000000000004', 'driver'),
  ('a0000000-0000-0000-0000-000000000005', 'driver')
on conflict (user_id) do update set role = excluded.role;

-- School -------------------------------------------------------------------

insert into live_schools (id, name, address, phone, lat, lng) values
  ('5cae0000-0000-0000-0000-000000000001', 'Greenfield Academy', 'Ngong Road, Nairobi', '+254709000000', -1.300100, 36.760800)
on conflict (id) do update set
  name = excluded.name, address = excluded.address, phone = excluded.phone,
  lat = excluded.lat, lng = excluded.lng;

-- Buses (bus 6 is the dedicated always-parked fleet-map bus with GPS) -------

insert into live_buses (id, name, plate_number, driver_id, driver_name, driver_phone, capacity, status, current_lat, current_lng) values
  ('b0000000-0000-0000-0000-000000000001', 'Express 1', 'KDA 101A', 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau',   '+254700000003', 33, 'active',  null, null),
  ('b0000000-0000-0000-0000-000000000002', 'Express 2', 'KDA 102B', 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru',   '+254700000005', 45, 'delayed', null, null),
  ('b0000000-0000-0000-0000-000000000003', 'Express 3', 'KDA 103C', 'a0000000-0000-0000-0000-000000000004', 'Francis Ochieng','+254700000004', 45, 'idle',    null, null),
  ('b0000000-0000-0000-0000-000000000004', 'Shuttle A', 'KDA 104D', null, 'John Otieno', '+254700000006', 25, 'idle', null, null),
  ('b0000000-0000-0000-0000-000000000005', 'Shuttle B', 'KDA 105E', null, null, null, 25, 'offline', null, null),
  ('b0000000-0000-0000-0000-000000000006', 'Reserve 1', 'KDA 106F', null, 'Standby Driver', '+254700000007', 30, 'active', -1.292100, 36.821900)
on conflict (id) do update set
  name = excluded.name, plate_number = excluded.plate_number, driver_id = excluded.driver_id,
  driver_name = excluded.driver_name, driver_phone = excluded.driver_phone,
  capacity = excluded.capacity, status = excluded.status,
  current_lat = excluded.current_lat, current_lng = excluded.current_lng;

-- Routes (morning + afternoon for the demo driver's Express 1) --------------

insert into live_routes (id, name, type, bus_id, school_id) values
  ('40000000-0000-0000-0000-000000000001', 'Express 1 — Morning',   'morning',   'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001'),
  ('40000000-0000-0000-0000-000000000002', 'Express 1 — Afternoon', 'afternoon', 'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001'),
  ('40000000-0000-0000-0000-000000000003', 'Express 2 — Morning',   'morning',   'b0000000-0000-0000-0000-000000000002', '5cae0000-0000-0000-0000-000000000001')
on conflict (id) do update set
  name = excluded.name, type = excluded.type, bus_id = excluded.bus_id, school_id = excluded.school_id;

-- Students (Faith + sibling share a stop; Kevin has no coords; Grace is bus-less)

insert into live_students (id, name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, status, bus_id, school_id, boarding_stop_name) values
  ('50000000-0000-0000-0000-000000000001', 'Faith Achieng',   'Grade 4', 'Amina Parent', '+254700000002', null, 'and7005@gmail.com', 'Kilimani, Nairobi',    -1.290200, 36.782300, '06:40', 'at-school', 'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'Kilimani Stop'),
  ('50000000-0000-0000-0000-000000000002', 'Brian Achieng',   'Grade 2', 'Amina Parent', '+254700000002', null, 'and7005@gmail.com', 'Kilimani, Nairobi',    -1.290200, 36.782300, '06:40', 'at-school', 'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'Kilimani Stop'),
  ('50000000-0000-0000-0000-000000000003', 'Happiness Kenesa','Grade 5', 'Joseph Kenesa','+254700000010', null, 'kenesa@example.com','Lavington, Nairobi',   -1.278900, 36.768500, '06:48', 'at-school', 'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'Lavington Stop'),
  ('50000000-0000-0000-0000-000000000004', 'Kevin Mwangi',    'Grade 3', 'Lucy Mwangi',  '+254700000011', null, 'lucy@example.com',  'Address pending',       null,      null,      '06:55', 'at-school', 'b0000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', null),
  ('50000000-0000-0000-0000-000000000005', 'Grace Njeri',     'Grade 1', 'Amina Parent', '+254700000002', null, 'and7005@gmail.com', 'Karen, Nairobi',        -1.319400, 36.706800, '07:00', 'at-school', null, '5cae0000-0000-0000-0000-000000000001', null)
on conflict (id) do update set
  name = excluded.name, grade = excluded.grade, parent_name = excluded.parent_name,
  parent_phone = excluded.parent_phone, parent_email = excluded.parent_email,
  home_address = excluded.home_address, home_lat = excluded.home_lat, home_lng = excluded.home_lng,
  pickup_time = excluded.pickup_time, status = excluded.status, bus_id = excluded.bus_id,
  school_id = excluded.school_id, boarding_stop_name = excluded.boarding_stop_name;

-- Student ↔ route assignments (Faith, Brian, Happiness, Kevin on both Express 1 routes)

insert into live_student_routes (id, student_id, route_id) values
  ('51000000-0000-0000-0000-000000000001', '50000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001'),
  ('51000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000001'),
  ('51000000-0000-0000-0000-000000000003', '50000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000001'),
  ('51000000-0000-0000-0000-000000000004', '50000000-0000-0000-0000-000000000004', '40000000-0000-0000-0000-000000000001'),
  ('51000000-0000-0000-0000-000000000005', '50000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000002'),
  ('51000000-0000-0000-0000-000000000006', '50000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000002'),
  ('51000000-0000-0000-0000-000000000007', '50000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000002'),
  ('51000000-0000-0000-0000-000000000008', '50000000-0000-0000-0000-000000000004', '40000000-0000-0000-0000-000000000002')
on conflict (student_id, route_id) do nothing;

-- Route stops (server-generated shape: per-student rows; siblings share an
-- order; no-coords student stops at the school; gate last).

delete from live_route_stops where route_id in
  ('40000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000002');

insert into live_route_stops (route_id, name, stop_order, scheduled_time, lat, lng, is_school_gate, student_id) values
  -- Morning
  ('40000000-0000-0000-0000-000000000001', 'Kilimani Stop',  1, '06:40', -1.290200, 36.782300, false, '50000000-0000-0000-0000-000000000001'),
  ('40000000-0000-0000-0000-000000000001', 'Kilimani Stop',  1, '06:40', -1.290200, 36.782300, false, '50000000-0000-0000-0000-000000000002'),
  ('40000000-0000-0000-0000-000000000001', 'Lavington Stop', 2, '06:48', -1.278900, 36.768500, false, '50000000-0000-0000-0000-000000000003'),
  ('40000000-0000-0000-0000-000000000001', 'School Pickup',  3, '06:55', -1.300100, 36.760800, false, '50000000-0000-0000-0000-000000000004'),
  ('40000000-0000-0000-0000-000000000001', 'Greenfield Academy', 4, '07:10', -1.300100, 36.760800, true, null),
  -- Afternoon
  ('40000000-0000-0000-0000-000000000002', 'Kilimani Stop',  1, '16:10', -1.290200, 36.782300, false, '50000000-0000-0000-0000-000000000001'),
  ('40000000-0000-0000-0000-000000000002', 'Kilimani Stop',  1, '16:10', -1.290200, 36.782300, false, '50000000-0000-0000-0000-000000000002'),
  ('40000000-0000-0000-0000-000000000002', 'Lavington Stop', 2, '16:18', -1.278900, 36.768500, false, '50000000-0000-0000-0000-000000000003'),
  ('40000000-0000-0000-0000-000000000002', 'School Pickup',  3, '16:25', -1.300100, 36.760800, false, '50000000-0000-0000-0000-000000000004'),
  ('40000000-0000-0000-0000-000000000002', 'Greenfield Academy', 4, '16:00', -1.300100, 36.760800, true, null);

-- Parent ↔ student links (Faith, Brian, and bus-less Grace for the null-guard case)

insert into live_parent_students (id, parent_id, student_id) values
  ('52000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000001'),
  ('52000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000002'),
  ('52000000-0000-0000-0000-000000000003', 'a0000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000005')
on conflict (parent_id, student_id) do nothing;

-- Runs: COMPLETED only for the demo driver's bus, so Start Run works in e2e.

insert into live_runs (id, bus_id, route_id, school_id, driver_id, type, date, start_time, end_time, status, total_stops, stops_completed, total_students, students_boarded, incidents) values
  ('60000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000003', 'morning',   (now() at time zone 'Africa/Nairobi')::date - 1, '06:35', '07:12', 'completed', 4, 4, 4, 4, 0),
  ('60000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000003', '5cae0000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000005', 'morning',   (now() at time zone 'Africa/Nairobi')::date,     '06:30', '07:20', 'completed', 3, 3, 3, 3, 1)
on conflict (id) do update set
  status = excluded.status, stops_completed = excluded.stops_completed,
  start_time = excluded.start_time, end_time = excluded.end_time;

-- Incidents / alerts (7 unacknowledged, matching the live demo feed) --------

insert into live_incidents (id, run_id, driver_id, driver_name, bus_id, bus_name, type, description, acknowledged, created_at) values
  ('70000000-0000-0000-0000-000000000001', null, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau',    'b0000000-0000-0000-0000-000000000001', 'Express 1', 'traffic',   'Heavy traffic on Ngong Road, approximately 15 minute delay.', false, now() - interval '8 minutes'),
  ('70000000-0000-0000-0000-000000000002', null, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau',    'b0000000-0000-0000-0000-000000000001', 'Express 1', 'arrival',   'Express 1 has arrived at Greenfield Academy.', false, now() - interval '20 minutes'),
  ('70000000-0000-0000-0000-000000000003', null, 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru',    'b0000000-0000-0000-0000-000000000002', 'Express 2', 'arrival',   'Express 2 has arrived at Greenfield Academy.', false, now() - interval '25 minutes'),
  ('70000000-0000-0000-0000-000000000004', null, 'a0000000-0000-0000-0000-000000000004', 'Francis Ochieng', 'b0000000-0000-0000-0000-000000000003', 'Express 3', 'accident',  'Minor road accident reported near Yaya Centre. All students safe.', false, now() - interval '35 minutes'),
  ('70000000-0000-0000-0000-000000000005', null, 'a0000000-0000-0000-0000-000000000003', 'Daniel Kamau',    'b0000000-0000-0000-0000-000000000001', 'Express 1', 'student',   'A student left a bag at the stop; returning briefly.', false, now() - interval '42 minutes'),
  ('70000000-0000-0000-0000-000000000006', null, null, 'John Otieno', 'b0000000-0000-0000-0000-000000000004', 'Shuttle A', 'breakdown', 'Engine warning light on; pulling over to inspect.', false, now() - interval '55 minutes'),
  ('70000000-0000-0000-0000-000000000007', null, 'a0000000-0000-0000-0000-000000000005', 'Mary Wanjiru',    'b0000000-0000-0000-0000-000000000002', 'Express 2', 'other',     'Route adjusted due to a road closure on Argwings Kodhek.', false, now() - interval '70 minutes')
on conflict (id) do update set
  type = excluded.type, description = excluded.description, acknowledged = excluded.acknowledged;
