# Admin Setup Fixes Design

Date: 2026-06-10

## Goal

Fix the admin setup workflows that currently prevent school staff from reliably creating and maintaining student, parent, driver, bus, trip, and stop data in the local FastAPI/Postgres application.

The current app already models a route as a scheduled trip. A student is assigned to a route by adding that student to a trip as an ordered `trip_passengers` stop. This design keeps that model and improves the setup workflow around it.

## Problems To Fix

1. Admin users cannot edit student address details after creation.
2. Admin users cannot easily assign routes to students.
3. Creating a new student does not save the full setup needed for route use in one flow.
4. Creating a new student does not save parent details at the same time.
5. Creating a new driver does not support assigning a default bus, and phone saving must be preserved and visible.

## Scope

In scope:

- Edit an existing student's name, home address, and location note.
- Create a student, parent contacts, optional parent link, and optional trip stop assignment from one screen.
- Treat route assignment as adding the student to a selected trip with stop number and estimated minutes from start.
- Add optional `default_bus_id` to drivers.
- Save and return driver phone and default bus values.
- Replace raw bus, driver, trip, and student ID entry with dropdowns where the data is available.

Out of scope:

- Authentication.
- Separate route-template tables.
- Map or geocoding support.
- Recurring weekly route generation.
- Parent account login.
- Major visual redesign beyond making the setup forms usable.

## Data Model

Add a nullable `default_bus_id` column to `drivers`.

- `drivers.default_bus_id` references `buses(id)`.
- The default bus must belong to the same school as the driver.
- Existing drivers remain valid with no default bus.
- Trips continue to store their own `bus_id` and `driver_id`; the driver's default bus is only a convenience for setup.

No new route tables are added. The current route model remains:

- `trips` represents a scheduled route/run.
- `trip_passengers` represents ordered stops on that route.
- Student address and location note remain on `students`.
- Parent contact details remain in `parent_contacts`.
- Parent links remain in `parent_links`.

## Backend API

### Student Update

Add:

`PATCH /api/admin/students/{student_id}`

Request fields:

- `schoolId`
- `fullName`
- `homeAddress`
- `homeLocationNote`

Behavior:

- Update only the matching active student in the same school.
- Return the updated student row.
- Reject missing or empty required fields.
- Return not found when the student does not belong to the school.

### Combined Student Setup

Add:

`POST /api/admin/student-setups`

Request fields:

- `schoolId`
- `student.fullName`
- `student.homeAddress`
- `student.homeLocationNote`
- optional `parentContact`
- optional `createParentLink`
- optional `tripAssignment`

`parentContact` contains:

- `contact1Name`
- `contact1Phone`
- `contact1Relationship`
- optional `contact2Name`
- optional `contact2Phone`
- optional `contact2Relationship`

`tripAssignment` contains:

- `tripId`
- `sequencePosition`
- `estimatedMinutesFromStart`

Behavior:

- Run inside one database transaction.
- Create the student first.
- If parent contact fields are provided, create or update `parent_contacts` for that student.
- If `createParentLink` is true, generate a secure token in the backend and create `parent_links`.
- If trip assignment is provided, create a `trip_passengers` row for the selected trip.
- Validate that the trip belongs to the same school.
- Return created student data, parent contact status, optional parent link token, and optional trip passenger data.

### Driver Create And List

Update existing driver create/list behavior:

- `CreateDriverRequest` accepts optional `defaultBusId`.
- `AdminDao.create_driver` inserts `phone` and `default_bus_id`.
- `AdminDao.list_drivers` returns `id`, `full_name`, `phone`, `default_bus_id`, and default bus label.
- Reject a default bus from another school.

### Existing List Endpoints

Keep existing list endpoints and make frontend types explicit:

- `GET /api/admin/students`
- `GET /api/admin/buses`
- `GET /api/admin/drivers`
- `GET /api/admin/trips`

These lists power dropdowns and remove most manual ID entry from the setup screen.

## Frontend Flow

Keep the current admin setup page, but make the Students tab the main combined setup flow.

### Students Tab

The student creation form includes:

- Student full name.
- Home address.
- Location note.
- Parent contact fields.
- Optional "Create parent link" checkbox.
- Optional "Assign to trip" section.
- Trip dropdown.
- Stop number.
- Minutes from start.

Saving calls `POST /api/admin/student-setups`.

After save:

- Clear the form.
- Show the created student ID.
- Show the generated parent link if the checkbox was selected.
- Show confirmation when the student was added to the trip.

### Edit Student Address

Add a compact edit area on the Students tab:

- Student dropdown.
- Full name.
- Home address.
- Location note.
- Save button.

Selecting a student fills the fields. Saving calls `PATCH /api/admin/students/{student_id}`.

### Drivers Tab

The driver form includes:

- Full name.
- Phone.
- PIN.
- Default bus dropdown.

Saving calls the existing driver create endpoint with `defaultBusId`. The response should include phone and default bus data.

### Trips Tab

Replace raw ID inputs with:

- Bus dropdown.
- Driver dropdown.

The driver dropdown can show the default bus label as context, but trip creation still stores the selected bus and selected driver explicitly.

## Error Handling

Use existing FastAPI error mapping patterns.

- Duplicate rows return conflict.
- Invalid IDs or cross-school references return not found or bad request.
- Invalid phone formats return bad request.
- Failed combined student setup rolls back all writes.

Frontend forms show the backend error message when available and keep the user's entered values so they can correct the problem.

## Testing

Backend tests:

- Combined student setup creates student, parent contacts, optional parent link, and trip passenger in one transaction.
- Combined student setup without parent or trip data still creates the student.
- Combined student setup rejects trip assignment to another school.
- Student address update changes only allowed fields.
- Student address update rejects a student outside the school.
- Driver creation stores phone and default bus.
- Driver creation rejects default bus outside the school.

Frontend tests:

- Student setup form sends combined payload with parent details and trip assignment.
- Student setup form can save without optional parent link or trip assignment.
- Edit student form loads selected student values and sends update payload.
- Driver form sends phone and default bus.
- Trip form uses selected bus and driver IDs from dropdowns.

Regression checks:

- Backend pytest suite passes.
- Frontend unit tests pass.
- Frontend build passes.

## Success Criteria

- A student can be created with address, parent details, optional parent link, and trip stop assignment from one screen.
- Student address details can be edited afterward.
- A student assigned to a trip appears as an ordered trip passenger stop.
- Driver phone is saved and returned from list/create responses.
- A driver can have an optional default bus.
- Trips can still explicitly choose a bus and driver.
- No authentication or route-template system is added in this phase.
