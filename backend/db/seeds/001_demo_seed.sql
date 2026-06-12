insert into schools (
  id,
  name,
  approaching_threshold,
  default_inter_student_minutes
) values (
  '11111111-1111-1111-1111-111111111111',
  'Greenfield Academy',
  2,
  6
) on conflict (id) do update set
  name = excluded.name,
  approaching_threshold = excluded.approaching_threshold,
  default_inter_student_minutes = excluded.default_inter_student_minutes;

insert into buses (
  id,
  school_id,
  label,
  registration_number,
  active
) values
  (
    '22222222-2222-2222-2222-222222222221',
    '11111111-1111-1111-1111-111111111111',
    'Kifaru Bus',
    'KCF 912T',
    true
  ),
  (
    '22222222-2222-2222-2222-222222222222',
    '11111111-1111-1111-1111-111111111111',
    'Ndege Shuttle',
    'KDA 556S',
    true
  ),
  (
    '22222222-2222-2222-2222-222222222223',
    '11111111-1111-1111-1111-111111111111',
    'Ngong',
    'KCA 678F',
    true
  ),
  (
    '22222222-2222-2222-2222-222222222224',
    '11111111-1111-1111-1111-111111111111',
    'Safari Express',
    'KCA 201Z',
    true
  ),
  (
    '22222222-2222-2222-2222-222222222225',
    '11111111-1111-1111-1111-111111111111',
    'Simba Coach',
    'KDD 678R',
    true
  ),
  (
    '22222222-2222-2222-2222-222222222226',
    '11111111-1111-1111-1111-111111111111',
    'Twiga Shuttle',
    'KBZ 445P',
    true
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  label = excluded.label,
  registration_number = excluded.registration_number,
  active = excluded.active;

insert into drivers (
  id,
  school_id,
  full_name,
  phone,
  default_bus_id,
  pin_hash,
  active
) values
  (
    '33333333-3333-3333-3333-333333333331',
    '11111111-1111-1111-1111-111111111111',
    'Francis Ochieng',
    '+254787878000',
    '22222222-2222-2222-2222-222222222221',
    'pbkdf2_sha256$200000$demo-1234-salt$ky4cRYebfgpImLHMweOtPswsvQ/su3GqU49a+lA+Xho=',
    true
  ),
  (
    '33333333-3333-3333-3333-333333333332',
    '11111111-1111-1111-1111-111111111111',
    'Michael Otieno',
    '+254767890123',
    '22222222-2222-2222-2222-222222222222',
    'pbkdf2_sha256$200000$demo-2468-salt$m+SqOGCoZX+irY5KuSK42kw79Ohl0hytO6fjtli4kI8=',
    true
  ),
  (
    '33333333-3333-3333-3333-333333333333',
    '11111111-1111-1111-1111-111111111111',
    'Frank Nwangi',
    '+254778787878',
    '22222222-2222-2222-2222-222222222223',
    'pbkdf2_sha256$200000$demo-1357-salt$6bCWB4PuzFo8y5YRXwAgFxUpJ/vDvvCTLb88e72/7PI=',
    true
  ),
  (
    '33333333-3333-3333-3333-333333333334',
    '11111111-1111-1111-1111-111111111111',
    'James Mwangi',
    '+254712345678',
    '22222222-2222-2222-2222-222222222224',
    'pbkdf2_sha256$200000$demo-8642-salt$wlYTzH5jNYPsHoPnMKSJHrYUXEsw3Y15gO0tOTrkmFE=',
    true
  ),
  (
    '33333333-3333-3333-3333-333333333335',
    '11111111-1111-1111-1111-111111111111',
    'David Kamau',
    '+254734567890',
    '22222222-2222-2222-2222-222222222225',
    'pbkdf2_sha256$200000$demo-9753-salt$2tyiuNgOorHmR04JEdt3JkTFsU4v9+vrjtrWf1gaFkg=',
    true
  ),
  (
    '33333333-3333-3333-3333-333333333336',
    '11111111-1111-1111-1111-111111111111',
    'Peter Ochieng',
    '+254723456789',
    '22222222-2222-2222-2222-222222222226',
    'pbkdf2_sha256$200000$demo-1122-salt$4ZlBZjYqN1VDlwad8nBc3m7d6wORNs/3pnnw3318jvU=',
    true
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  full_name = excluded.full_name,
  phone = excluded.phone,
  default_bus_id = excluded.default_bus_id,
  pin_hash = excluded.pin_hash,
  active = excluded.active;

insert into students (
  id,
  school_id,
  full_name,
  grade_level,
  home_address,
  home_location_note,
  active
) values
  (
    '44444444-4444-4444-4444-444444444441',
    '11111111-1111-1111-1111-111111111111',
    'Faith Achieng',
    '2',
    '56 Karen Road',
    'Karen Road stop',
    true
  ),
  (
    '44444444-4444-4444-4444-444444444442',
    '11111111-1111-1111-1111-111111111111',
    'Happiness Kenesa',
    '5',
    '23 Langata Road',
    'Langata Road stop',
    true
  ),
  (
    '44444444-4444-4444-4444-444444444443',
    '11111111-1111-1111-1111-111111111111',
    'James Mwangi',
    '1',
    'Karen road 23',
    'Karen road stop',
    true
  ),
  (
    '44444444-4444-4444-4444-444444444444',
    '11111111-1111-1111-1111-111111111111',
    'Michael Otieno',
    '6',
    '45 Langata road',
    'Langata road stop',
    true
  ),
  (
    '44444444-4444-4444-4444-444444444445',
    '11111111-1111-1111-1111-111111111111',
    'Roy Otieno',
    '5',
    '1 Hillcrest Rd',
    'Hillcrest Road stop',
    true
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  full_name = excluded.full_name,
  grade_level = excluded.grade_level,
  home_address = excluded.home_address,
  home_location_note = excluded.home_location_note,
  active = excluded.active;

insert into parent_contacts (
  id,
  school_id,
  student_id,
  contact_1_name,
  contact_1_phone,
  contact_1_relationship,
  contact_2_name,
  contact_2_phone,
  contact_2_relationship
) values
  (
    '55555555-5555-5555-5555-555555555551',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444441',
    'Grace Achieng',
    '+254787878000',
    'Mother',
    null,
    null,
    null
  ),
  (
    '55555555-5555-5555-5555-555555555552',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444442',
    'Jimmy Kenesa',
    '+254787878001',
    'Father',
    null,
    null,
    null
  ),
  (
    '55555555-5555-5555-5555-555555555553',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444443',
    'George Mwangi',
    '+254787878002',
    'Father',
    null,
    null,
    null
  ),
  (
    '55555555-5555-5555-5555-555555555554',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444444',
    'John Otieno',
    '+254787878003',
    'Father',
    null,
    null,
    null
  ),
  (
    '55555555-5555-5555-5555-555555555555',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444445',
    'Jessica Otieno',
    '+254787878004',
    'Mother',
    null,
    null,
    null
  )
on conflict (student_id) do update set
  school_id = excluded.school_id,
  contact_1_name = excluded.contact_1_name,
  contact_1_phone = excluded.contact_1_phone,
  contact_1_relationship = excluded.contact_1_relationship,
  contact_2_name = excluded.contact_2_name,
  contact_2_phone = excluded.contact_2_phone,
  contact_2_relationship = excluded.contact_2_relationship;

insert into parent_links (
  id,
  school_id,
  student_id,
  token,
  revoked_at
) values
  (
    '66666666-6666-6666-6666-666666666661',
    '11111111-1111-1111-1111-111111111111',
    '44444444-4444-4444-4444-444444444441',
    'demo-parent-token-00000000000000000001',
    null
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  student_id = excluded.student_id,
  token = excluded.token,
  revoked_at = excluded.revoked_at;

insert into trips (
  id,
  school_id,
  bus_id,
  driver_id,
  name,
  session,
  service_date,
  scheduled_start,
  status,
  started_at,
  ended_at
) values
  (
    '77777777-7777-7777-7777-777777777771',
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222221',
    '33333333-3333-3333-3333-333333333331',
    'Express 2 (AM)',
    'morning',
    current_date,
    '07:00',
    'scheduled',
    null,
    null
  ),
  (
    '77777777-7777-7777-7777-777777777772',
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222221',
    '33333333-3333-3333-3333-333333333331',
    'Express 2 Return (PM)',
    'afternoon',
    current_date,
    '15:30',
    'scheduled',
    null,
    null
  ),
  (
    '77777777-7777-7777-7777-777777777773',
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222226',
    '33333333-3333-3333-3333-333333333336',
    'Express 1 (AM)',
    'morning',
    current_date,
    '07:00',
    'scheduled',
    null,
    null
  ),
  (
    '77777777-7777-7777-7777-777777777774',
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222226',
    '33333333-3333-3333-3333-333333333336',
    'Express 1 Return (PM)',
    'afternoon',
    current_date,
    '15:30',
    'scheduled',
    null,
    null
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  bus_id = excluded.bus_id,
  driver_id = excluded.driver_id,
  name = excluded.name,
  session = excluded.session,
  service_date = excluded.service_date,
  scheduled_start = excluded.scheduled_start,
  status = excluded.status,
  started_at = excluded.started_at,
  ended_at = excluded.ended_at;

insert into trip_passengers (
  id,
  school_id,
  trip_id,
  passenger_type,
  student_id,
  staff_passenger_id,
  sequence_position,
  estimated_minutes_from_start,
  actual_pickup_time,
  actual_dropoff_time,
  status
) values
  (
    '88888888-8888-8888-8888-888888888881',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    'student',
    '44444444-4444-4444-4444-444444444443',
    null,
    1,
    45,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888882',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    'student',
    '44444444-4444-4444-4444-444444444441',
    null,
    2,
    50,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888883',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777772',
    'student',
    '44444444-4444-4444-4444-444444444443',
    null,
    1,
    10,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888884',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777772',
    'student',
    '44444444-4444-4444-4444-444444444441',
    null,
    2,
    20,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888885',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777773',
    'student',
    '44444444-4444-4444-4444-444444444442',
    null,
    1,
    5,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888886',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777773',
    'student',
    '44444444-4444-4444-4444-444444444444',
    null,
    2,
    10,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888887',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777773',
    'student',
    '44444444-4444-4444-4444-444444444445',
    null,
    3,
    15,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888888',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777774',
    'student',
    '44444444-4444-4444-4444-444444444442',
    null,
    1,
    5,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888889',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777774',
    'student',
    '44444444-4444-4444-4444-444444444444',
    null,
    2,
    10,
    null,
    null,
    'pending'
  ),
  (
    '88888888-8888-8888-8888-888888888890',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777774',
    'student',
    '44444444-4444-4444-4444-444444444445',
    null,
    3,
    15,
    null,
    null,
    'pending'
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  trip_id = excluded.trip_id,
  passenger_type = excluded.passenger_type,
  student_id = excluded.student_id,
  staff_passenger_id = excluded.staff_passenger_id,
  sequence_position = excluded.sequence_position,
  estimated_minutes_from_start = excluded.estimated_minutes_from_start,
  actual_pickup_time = excluded.actual_pickup_time,
  actual_dropoff_time = excluded.actual_dropoff_time,
  status = excluded.status;

insert into trip_events (
  id,
  school_id,
  trip_id,
  trip_passenger_id,
  event_type,
  created_by_role,
  created_by_id,
  occurred_at,
  metadata
) values
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '12 days' + interval '5 minutes',
    '{"admin_alert":"true","title":"Heavy Traffic / Delay","message":"Heavy traffic on Hillcrest Road, we will delay by 5 minutes","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '12 days',
    '{"admin_alert":"true","title":"arrival","message":"Kifaru Bus has arrived at School Gates.","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '29 days',
    '{"admin_alert":"true","title":"arrival","message":"Kifaru Bus has arrived at School Gates.","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '29 days' + interval '8 minutes',
    '{"admin_alert":"true","title":"arrival","message":"Kifaru Bus has arrived at School Gates.","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa5',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '36 days',
    '{"admin_alert":"true","title":"arrival","message":"Kifaru Bus has arrived at School Gates.","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa6',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '36 days' + interval '11 minutes',
    '{"admin_alert":"true","title":"arrival","message":"Kifaru Bus has arrived at School Gates.","badge":"New"}'::jsonb
  ),
  (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa7',
    '11111111-1111-1111-1111-111111111111',
    '77777777-7777-7777-7777-777777777771',
    null,
    'issue_reported',
    'driver',
    '33333333-3333-3333-3333-333333333331',
    now() - interval '37 days',
    '{"admin_alert":"true","title":"Road Accident","message":"man","badge":"New"}'::jsonb
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  trip_id = excluded.trip_id,
  trip_passenger_id = excluded.trip_passenger_id,
  event_type = excluded.event_type,
  created_by_role = excluded.created_by_role,
  created_by_id = excluded.created_by_id,
  occurred_at = excluded.occurred_at,
  metadata = excluded.metadata;

insert into notification_outbox (
  id,
  school_id,
  trip_event_id,
  recipient_kind,
  recipient_phone,
  push_subscription_id,
  channel,
  template_key,
  payload,
  status,
  attempts,
  last_error,
  claimed_at,
  sent_at
) values
  (
    '99999999-9999-9999-9999-999999999991',
    '11111111-1111-1111-1111-111111111111',
    null,
    'parent',
    '+254787878000',
    null,
    'sms',
    'child_confirmed_on_van',
    '{"body":"SafeRide demo SMS message."}'::jsonb,
    'pending',
    0,
    null,
    null,
    null
  ),
  (
    '99999999-9999-9999-9999-999999999992',
    '11111111-1111-1111-1111-111111111111',
    null,
    'parent',
    null,
    null,
    'push',
    'child_confirmed_on_van',
    '{"body":"SafeRide demo push message."}'::jsonb,
    'pending',
    0,
    null,
    null,
    null
  )
on conflict (id) do update set
  school_id = excluded.school_id,
  trip_event_id = excluded.trip_event_id,
  recipient_kind = excluded.recipient_kind,
  recipient_phone = excluded.recipient_phone,
  push_subscription_id = excluded.push_subscription_id,
  channel = excluded.channel,
  template_key = excluded.template_key,
  payload = excluded.payload,
  status = excluded.status,
  attempts = excluded.attempts,
  last_error = excluded.last_error,
  claimed_at = excluded.claimed_at,
  sent_at = excluded.sent_at;
