create or replace function apply_daily_attendance_to_trip_passengers()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  update trip_passengers
  set status = case new.status
    when 'absent' then 'absent_admin'::trip_passenger_status
    when 'alternative_transport' then 'alternative_transport'::trip_passenger_status
    when 'riding' then 'pending'::trip_passenger_status
  end
  from trips
  where trips.id = trip_passengers.trip_id
    and trips.school_id = trip_passengers.school_id
    and trip_passengers.school_id = new.school_id
    and trip_passengers.student_id = new.student_id
    and trips.service_date = new.attendance_date
    and trips.status in ('scheduled', 'active')
    and trip_passengers.status in (
      'pending',
      'absent_admin',
      'alternative_transport'
    );

  return new;
end;
$$;

create or replace function get_parent_trip_progress(link_token text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  parent_link public.parent_links%rowtype;
  active_trip public.trips%rowtype;
  nairobi_today date := (now() at time zone 'Africa/Nairobi')::date;
  trip_payload jsonb;
  passengers_payload jsonb;
begin
  select *
  into parent_link
  from public.parent_links
  where token = link_token
    and revoked_at is null
  limit 1;

  if not found then
    raise exception 'Parent link is invalid or revoked';
  end if;

  select t.*
  into active_trip
  from public.trips t
  join public.trip_passengers tp
    on tp.trip_id = t.id
    and tp.school_id = t.school_id
    and tp.student_id = parent_link.student_id
  where t.school_id = parent_link.school_id
    and t.service_date = nairobi_today
    and t.status in (
      'scheduled',
      'active',
      'delayed',
      'issue_reported'
    )
  order by case t.status
    when 'active' then 1
    when 'delayed' then 2
    when 'issue_reported' then 3
    when 'scheduled' then 4
    else 5
  end,
  t.scheduled_start desc
  limit 1;

  if active_trip.id is null then
    trip_payload := null;
    passengers_payload := '[]'::jsonb;
  else
    trip_payload := jsonb_build_object(
      'id', active_trip.id,
      'name', active_trip.name,
      'session', active_trip.session,
      'serviceDate', active_trip.service_date,
      'scheduledStart', active_trip.scheduled_start,
      'status', active_trip.status
    );

    select coalesce(
      jsonb_agg(
        jsonb_build_object(
          'id', tp.id,
          'studentId', case
            when tp.student_id = parent_link.student_id then tp.student_id
            else null
          end,
          'studentName', case
            when tp.student_id = parent_link.student_id then s.full_name
            else null
          end,
          'locationLabel', case
            when tp.student_id = parent_link.student_id then coalesce(
              s.home_location_note,
              s.home_address,
              'Stop ' || tp.sequence_position
            )
            else 'Stop ' || tp.sequence_position
          end,
          'sequencePosition', tp.sequence_position,
          'estimatedMinutesFromStart', tp.estimated_minutes_from_start,
          'status', tp.status
        )
        order by tp.sequence_position
      ),
      '[]'::jsonb
    )
    into passengers_payload
    from public.trip_passengers tp
    left join public.students s
      on s.id = tp.student_id
      and s.school_id = tp.school_id
    where tp.trip_id = active_trip.id
      and tp.school_id = parent_link.school_id;
  end if;

  return jsonb_build_object(
    'ownStudentId', parent_link.student_id,
    'trip', trip_payload,
    'passengers', passengers_payload
  );
end;
$$;

revoke all on function public.get_parent_trip_progress(text) from public;
grant execute on function public.get_parent_trip_progress(text) to anon;
grant execute on function public.get_parent_trip_progress(text) to authenticated;

create or replace function create_driver_with_pin(
  p_school_id uuid,
  p_full_name text,
  p_phone text,
  p_pin text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  created_driver public.drivers%rowtype;
begin
  if p_school_id is distinct from public.current_school_id() then
    raise exception 'Driver school is not available to this admin';
  end if;

  if p_full_name is null or length(trim(p_full_name)) = 0 then
    raise exception 'Driver name is required';
  end if;

  if p_pin is null or p_pin !~ '^[0-9]{4,6}$' then
    raise exception 'Driver PIN must be 4 to 6 digits';
  end if;

  insert into public.drivers (
    school_id,
    full_name,
    phone,
    pin_hash
  )
  values (
    p_school_id,
    trim(p_full_name),
    nullif(trim(coalesce(p_phone, '')), ''),
    crypt(p_pin, gen_salt('bf'))
  )
  returning * into created_driver;

  return jsonb_build_object(
    'id', created_driver.id,
    'schoolId', created_driver.school_id,
    'fullName', created_driver.full_name,
    'phone', created_driver.phone
  );
end;
$$;

revoke all on function public.create_driver_with_pin(uuid, text, text, text) from public;
grant execute on function public.create_driver_with_pin(uuid, text, text, text) to authenticated;

create or replace function verify_driver_pin(p_pin text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  matching_count integer;
  matching_driver public.drivers%rowtype;
  session_token text;
begin
  if p_pin is null or p_pin !~ '^[0-9]{4,6}$' then
    raise exception 'Invalid driver PIN';
  end if;

  select count(*)
  into matching_count
  from public.drivers d
  where d.active = true
    and d.pin_hash ~ '^\$'
    and d.pin_hash = crypt(p_pin, d.pin_hash);

  if matching_count = 0 then
    raise exception 'Invalid driver PIN';
  end if;

  if matching_count > 1 then
    raise exception 'Invalid driver PIN';
  end if;

  select d.*
  into matching_driver
  from public.drivers d
  where d.active = true
    and d.pin_hash ~ '^\$'
    and d.pin_hash = crypt(p_pin, d.pin_hash)
  limit 1;

  session_token := encode(gen_random_bytes(32), 'hex');

  insert into public.driver_sessions (
    school_id,
    driver_id,
    token_hash,
    expires_at
  )
  values (
    matching_driver.school_id,
    matching_driver.id,
    encode(digest(session_token, 'sha256'), 'hex'),
    now() + interval '16 hours'
  );

  return jsonb_build_object(
    'id', matching_driver.id,
    'driverId', matching_driver.id,
    'schoolId', matching_driver.school_id,
    'fullName', matching_driver.full_name,
    'sessionToken', session_token
  );
end;
$$;

revoke all on function public.verify_driver_pin(text) from public;
grant execute on function public.verify_driver_pin(text) to anon;
grant execute on function public.verify_driver_pin(text) to authenticated;

create or replace function get_driver_trips_for_today(
  p_session_token text,
  p_service_date date
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  session_driver_id uuid;
  driver_school_id uuid;
  trips_payload jsonb;
begin
  select ds.driver_id, ds.school_id
  into session_driver_id, driver_school_id
  from public.driver_sessions ds
  join public.drivers d
    on d.id = ds.driver_id
    and d.school_id = ds.school_id
  where d.active = true
    and ds.token_hash = encode(digest(p_session_token, 'sha256'), 'hex')
    and ds.revoked_at is null
    and ds.expires_at > now();

  if driver_school_id is null then
    raise exception 'Driver session is invalid or expired';
  end if;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id', t.id,
        'name', t.name,
        'scheduledStart', t.scheduled_start,
        'session', t.session,
        'status', t.status,
        'busLabel', b.label
      )
      order by t.scheduled_start
    ),
    '[]'::jsonb
  )
  into trips_payload
  from public.trips t
  left join public.buses b
    on b.id = t.bus_id
    and b.school_id = t.school_id
  where t.driver_id = session_driver_id
    and t.school_id = driver_school_id
    and t.service_date = p_service_date;

  return trips_payload;
end;
$$;

revoke all on function public.get_driver_trips_for_today(text, date) from public;
grant execute on function public.get_driver_trips_for_today(text, date) to anon;
grant execute on function public.get_driver_trips_for_today(text, date) to authenticated;

create or replace function get_driver_trip_passengers(
  p_session_token text,
  p_trip_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  session_driver_id uuid;
  session_school_id uuid;
  passengers_payload jsonb;
begin
  select ds.driver_id, ds.school_id
  into session_driver_id, session_school_id
  from public.driver_sessions ds
  join public.drivers d
    on d.id = ds.driver_id
    and d.school_id = ds.school_id
  where d.active = true
    and ds.token_hash = encode(digest(p_session_token, 'sha256'), 'hex')
    and ds.revoked_at is null
    and ds.expires_at > now();

  if session_driver_id is null then
    raise exception 'Driver session is invalid or expired';
  end if;

  if not exists (
    select 1
    from public.trips t
    where t.id = p_trip_id
      and t.school_id = session_school_id
      and t.driver_id = session_driver_id
  ) then
    raise exception 'Trip is not assigned to this driver';
  end if;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id', tp.id,
        'name', coalesce(s.full_name, sp.full_name, 'Passenger ' || tp.sequence_position),
        'sequencePosition', tp.sequence_position,
        'status', tp.status
      )
      order by tp.sequence_position
    ),
    '[]'::jsonb
  )
  into passengers_payload
  from public.trip_passengers tp
  left join public.students s
    on s.id = tp.student_id
    and s.school_id = tp.school_id
  left join public.staff_passengers sp
    on sp.id = tp.staff_passenger_id
    and sp.school_id = tp.school_id
  where tp.trip_id = p_trip_id
    and tp.school_id = session_school_id
    and tp.status not in ('absent_admin', 'alternative_transport');

  return passengers_payload;
end;
$$;

revoke all on function public.get_driver_trip_passengers(text, uuid) from public;
grant execute on function public.get_driver_trip_passengers(text, uuid) to anon;
grant execute on function public.get_driver_trip_passengers(text, uuid) to authenticated;

create or replace function record_driver_event(
  p_session_token text,
  p_trip_id uuid,
  p_trip_passenger_id uuid,
  p_event_type event_type,
  p_occurred_at timestamptz,
  p_metadata jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  session_driver_id uuid;
  session_school_id uuid;
  active_driver public.drivers%rowtype;
  assigned_trip public.trips%rowtype;
  passenger_event_types event_type[] := array[
    'passenger_boarded',
    'passenger_not_present',
    'passenger_dropped'
  ]::event_type[];
  event_id uuid;
begin
  if p_event_type not in (
    'trip_started',
    'passenger_boarded',
    'passenger_not_present',
    'passenger_dropped',
    'trip_ended',
    'issue_reported'
  ) then
    raise exception 'Unsupported driver event type';
  end if;

  select ds.driver_id, ds.school_id
  into session_driver_id, session_school_id
  from public.driver_sessions ds
  join public.drivers d
    on d.id = ds.driver_id
    and d.school_id = ds.school_id
  where d.active = true
    and ds.token_hash = encode(digest(p_session_token, 'sha256'), 'hex')
    and ds.revoked_at is null
    and ds.expires_at > now();

  if session_driver_id is null then
    raise exception 'Driver session is invalid or expired';
  end if;

  select d.*
  into active_driver
  from public.drivers d
  where d.id = session_driver_id
    and d.active = true;

  if not found or active_driver.school_id is distinct from session_school_id then
    raise exception 'Driver is invalid or inactive';
  end if;

  select t.*
  into assigned_trip
  from public.trips t
  where t.id = p_trip_id
    and t.school_id = session_school_id
    and t.driver_id = session_driver_id;

  if not found then
    raise exception 'Trip is not assigned to this driver';
  end if;

  if p_event_type = 'trip_started' and assigned_trip.status <> 'scheduled' then
    raise exception 'Only scheduled trips can be started';
  elsif p_event_type in ('passenger_boarded', 'passenger_not_present', 'passenger_dropped', 'issue_reported') and assigned_trip.status not in ('active', 'delayed', 'issue_reported') then
    raise exception 'Driver events can only be recorded for active trips';
  elsif p_event_type = 'trip_ended' and assigned_trip.status not in ('active', 'delayed', 'issue_reported') then
    raise exception 'Only active trips can be completed';
  end if;

  if p_event_type = any(passenger_event_types) then
    if p_trip_passenger_id is null then
      raise exception 'tripPassengerId is required for passenger driver events';
    end if;

    if not exists (
      select 1
      from public.trip_passengers tp
      where tp.id = p_trip_passenger_id
        and tp.trip_id = p_trip_id
        and tp.school_id = session_school_id
        and (
          (
            p_event_type in ('passenger_boarded', 'passenger_not_present')
            and tp.status = 'pending'
          )
          or (
            p_event_type = 'passenger_dropped'
            and tp.status = 'boarded'
          )
        )
    ) then
      raise exception 'Trip passenger cannot be updated for this event';
    end if;
  end if;

  insert into public.trip_events (
    school_id,
    trip_id,
    trip_passenger_id,
    event_type,
    created_by_role,
    created_by_id,
    occurred_at,
    metadata
  )
  values (
    session_school_id,
    p_trip_id,
    p_trip_passenger_id,
    p_event_type,
    'driver',
    session_driver_id,
    coalesce(p_occurred_at, now()),
    coalesce(p_metadata, '{}'::jsonb)
  )
  returning id into event_id;

  return event_id;
end;
$$;

revoke all on function public.record_driver_event(
  text,
  uuid,
  uuid,
  event_type,
  timestamptz,
  jsonb
) from public;
grant execute on function public.record_driver_event(
  text,
  uuid,
  uuid,
  event_type,
  timestamptz,
  jsonb
) to anon;
grant execute on function public.record_driver_event(
  text,
  uuid,
  uuid,
  event_type,
  timestamptz,
  jsonb
) to authenticated;

create or replace function correct_trip_passenger_status(
  p_school_id uuid,
  p_trip_passenger_id uuid,
  p_original_value jsonb,
  p_corrected_status trip_passenger_status,
  p_reason text
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  admin_school_id uuid;
  audit_id uuid;
  existing_row trip_passengers%rowtype;
  server_original_value jsonb;
begin
  admin_school_id := current_school_id();

  if admin_school_id is null or p_school_id is distinct from admin_school_id then
    raise exception 'Cannot correct records outside your school';
  end if;

  select tp.*
  into existing_row
  from trip_passengers tp
  where tp.id = p_trip_passenger_id
    and tp.school_id = p_school_id
  for update;

  if not found then
    raise exception 'Trip passenger record not found';
  end if;

  if not exists (
    select 1
    from trip_passengers tp
    join trips t
      on t.id = tp.trip_id
      and t.school_id = tp.school_id
    where tp.id = p_trip_passenger_id
      and tp.school_id = p_school_id
      and t.status = 'completed'
  ) then
    raise exception 'Only completed trip records can be corrected';
  end if;

  server_original_value := jsonb_build_object(
    'status', existing_row.status,
    'actual_pickup_time', existing_row.actual_pickup_time,
    'actual_dropoff_time', existing_row.actual_dropoff_time
  );

  update trip_passengers
  set status = p_corrected_status
  where id = p_trip_passenger_id
    and school_id = p_school_id;

  insert into audit_log (
    school_id,
    entity_table,
    entity_id,
    admin_user_id,
    original_value,
    corrected_value,
    reason
  )
  values (
    p_school_id,
    'trip_passengers',
    p_trip_passenger_id,
    auth.uid(),
    server_original_value,
    jsonb_build_object('status', p_corrected_status),
    p_reason
  )
  returning id into audit_id;

  return audit_id;
end;
$$;

revoke all on function public.correct_trip_passenger_status(
  uuid,
  uuid,
  jsonb,
  trip_passenger_status,
  text
) from public;
grant execute on function public.correct_trip_passenger_status(
  uuid,
  uuid,
  jsonb,
  trip_passenger_status,
  text
) to authenticated;

do $$
begin
  if not exists (
    select 1
    from pg_trigger
    where tgname = 'daily_attendance_apply_to_trip_passengers'
      and tgrelid = 'daily_attendance'::regclass
  ) then
    create trigger daily_attendance_apply_to_trip_passengers
    after insert or update on daily_attendance
    for each row
    execute function apply_daily_attendance_to_trip_passengers();
  end if;
end;
$$;

create or replace function enqueue_parent_notifications()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  notification_template_key text;
begin
  if new.trip_passenger_id is null then
    return new;
  end if;

  notification_template_key := case new.event_type
    when 'passenger_boarded' then 'child_confirmed_on_van'
    when 'passenger_dropped' then 'child_dropped_off_home'
    when 'passenger_not_present' then 'child_not_boarded'
    else null
  end;

  if notification_template_key is null then
    return new;
  end if;

  insert into notification_outbox (
    school_id,
    trip_event_id,
    recipient_kind,
    recipient_phone,
    channel,
    template_key,
    payload
  )
  select
    new.school_id,
    new.id,
    'parent',
    parent_phones.recipient_phone,
    'sms',
    notification_template_key,
    coalesce(new.metadata, '{}'::jsonb) || jsonb_build_object(
      'body',
      case notification_template_key
        when 'child_confirmed_on_van' then 'SafeRide: Your child has been confirmed on the van.'
        when 'child_dropped_off_home' then 'SafeRide: Your child has been confirmed dropped off at home.'
        when 'child_not_boarded' then 'SafeRide: Your child was expected but was not confirmed by the driver. Please contact the school.'
      end
    )
  from (
    select distinct recipient_phone
    from (
      select pc.contact_1_phone as recipient_phone
      from trip_passengers tp
      join parent_contacts pc on pc.student_id = tp.student_id
      where tp.id = new.trip_passenger_id
        and tp.school_id = new.school_id
        and tp.passenger_type = 'student'
        and pc.school_id = new.school_id
        and (
          (
            new.event_type = 'passenger_boarded'
            and tp.status = 'boarded'
            and tp.actual_pickup_time = new.occurred_at
          )
          or (
            new.event_type = 'passenger_dropped'
            and tp.status = 'dropped'
            and tp.actual_dropoff_time = new.occurred_at
          )
          or (
            new.event_type = 'passenger_not_present'
            and tp.status = 'absent_driver'
          )
        )

      union all

      select pc.contact_2_phone as recipient_phone
      from trip_passengers tp
      join parent_contacts pc on pc.student_id = tp.student_id
      where tp.id = new.trip_passenger_id
        and tp.school_id = new.school_id
        and tp.passenger_type = 'student'
        and pc.school_id = new.school_id
        and (
          (
            new.event_type = 'passenger_boarded'
            and tp.status = 'boarded'
            and tp.actual_pickup_time = new.occurred_at
          )
          or (
            new.event_type = 'passenger_dropped'
            and tp.status = 'dropped'
            and tp.actual_dropoff_time = new.occurred_at
          )
          or (
            new.event_type = 'passenger_not_present'
            and tp.status = 'absent_driver'
          )
        )
    ) contact_phones
    where recipient_phone is not null
  ) parent_phones
  where not exists (
    select 1
    from notification_outbox existing_outbox
    join trip_events prior_trip_events
      on prior_trip_events.id = existing_outbox.trip_event_id
      and prior_trip_events.school_id = existing_outbox.school_id
    where existing_outbox.school_id = new.school_id
      and prior_trip_events.school_id = new.school_id
      and prior_trip_events.trip_passenger_id = new.trip_passenger_id
      and prior_trip_events.id <> new.id
      and existing_outbox.template_key = notification_template_key
      and existing_outbox.recipient_kind = 'parent'
      and existing_outbox.channel = 'sms'
      and existing_outbox.recipient_phone = parent_phones.recipient_phone
  );

  return new;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_trigger
    where tgname = 'trip_events_enqueue_parent_notifications'
      and tgrelid = 'trip_events'::regclass
  ) then
    create trigger trip_events_enqueue_parent_notifications
    after insert on trip_events
    for each row
    execute function enqueue_parent_notifications();
  end if;
end;
$$;

create or replace function apply_trip_event()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.event_type = 'passenger_boarded' then
    update trip_passengers
    set
      status = 'boarded'::trip_passenger_status,
      actual_pickup_time = new.occurred_at
    where id = new.trip_passenger_id
      and school_id = new.school_id
      and trip_id = new.trip_id
      and status = 'pending';
  elsif new.event_type = 'passenger_dropped' then
    update trip_passengers
    set
      status = 'dropped'::trip_passenger_status,
      actual_dropoff_time = new.occurred_at
    where id = new.trip_passenger_id
      and school_id = new.school_id
      and trip_id = new.trip_id
      and status = 'boarded';
  elsif new.event_type = 'passenger_not_present' then
    update trip_passengers
    set status = 'absent_driver'::trip_passenger_status
    where id = new.trip_passenger_id
      and school_id = new.school_id
      and trip_id = new.trip_id
      and status = 'pending';
  elsif new.event_type = 'trip_started' then
    update trips
    set
      status = 'active'::trip_status,
      started_at = new.occurred_at
    where id = new.trip_id
      and school_id = new.school_id
      and status = 'scheduled';
  elsif new.event_type = 'trip_ended' then
    update trips
    set
      status = 'completed'::trip_status,
      ended_at = new.occurred_at
    where id = new.trip_id
      and school_id = new.school_id
      and status in (
        'active',
        'issue_reported',
        'delayed'
      );
  elsif new.event_type = 'issue_reported' then
    update trips
    set status = 'issue_reported'::trip_status
    where id = new.trip_id
      and school_id = new.school_id
      and status in (
        'active',
        'delayed'
      );
  end if;

  return new;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_trigger
    where tgname = 'trip_events_apply_to_trip_state'
      and tgrelid = 'trip_events'::regclass
  ) then
    create trigger trip_events_apply_to_trip_state
    after insert on trip_events
    for each row
    execute function apply_trip_event();
  end if;
end;
$$;
