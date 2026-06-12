# Admin Setup Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the admin setup flows so a school can create students with address, parent details, optional parent link, and trip stop assignment, edit student address details, and create drivers with phone and default bus.

**Architecture:** Keep the existing FastAPI/Postgres layered backend and React/Vite frontend. Add focused backend API methods for combined student setup and student update, add a nullable driver default bus field, and update the existing `SchoolSetup` screen to use dropdown-backed forms instead of raw ID entry where possible.

**Tech Stack:** FastAPI, Pydantic, psycopg, Postgres, React 18, TypeScript, Vite, Vitest, Testing Library.

---

## File Structure

- Modify `backend/db/migrations/001_initial_schema.sql`: include `drivers.default_bus_id` for clean database resets.
- Create `backend/db/migrations/002_admin_setup_fixes.sql`: migrate existing local databases that already ran migration 001.
- Modify `backend/db/seeds/001_demo_seed.sql`: seed the demo driver's default bus.
- Modify `scripts/start-local.sh`: apply all migration files in order and record each migration id.
- Modify `scripts/reset-local-db.sh`: reset and apply all migration files in order.
- Modify `backend/app/schemas/admin.py`: add request schemas for student update, combined student setup, and driver default bus.
- Modify `backend/app/services/admin_service.py`: add validation, backend parent-link token generation, and service methods.
- Modify `backend/app/dao/admin_dao.py`: add transaction-based combined setup, student update, default bus persistence, and richer list rows.
- Modify `backend/app/api/admin.py`: expose `PATCH /api/admin/students/{student_id}` and `POST /api/admin/student-setups`.
- Modify `backend/tests/services/test_admin_service.py`: cover service validation and orchestration.
- Modify `frontend/src/services/httpClient.ts`: add `apiPatch`.
- Modify `frontend/src/services/adminApi.ts`: add typed list responses and new admin API calls.
- Modify `frontend/src/features/admin/SchoolSetup.tsx`: add setup-data loading, dropdowns, combined student setup, student address edit, driver default bus.
- Create `frontend/tests/unit/SchoolSetup.test.tsx`: cover the admin setup user flows.

## Task 1: Database Migration And Local Migration Runner

**Files:**
- Modify: `backend/db/migrations/001_initial_schema.sql`
- Create: `backend/db/migrations/002_admin_setup_fixes.sql`
- Modify: `backend/db/seeds/001_demo_seed.sql`
- Modify: `scripts/start-local.sh`
- Modify: `scripts/reset-local-db.sh`

- [ ] **Step 1: Add migration 002**

Create `backend/db/migrations/002_admin_setup_fixes.sql`:

```sql
alter table drivers
  add column if not exists default_bus_id uuid;

do $$
begin
  alter table drivers
    add constraint drivers_default_bus_school_fkey
    foreign key (default_bus_id, school_id)
    references buses(id, school_id)
    on delete set null (default_bus_id);
exception when duplicate_object then null;
end $$;

create index if not exists drivers_default_bus_id_idx
  on drivers (default_bus_id)
  where default_bus_id is not null;
```

- [ ] **Step 2: Update initial schema for fresh resets**

In `backend/db/migrations/001_initial_schema.sql`, replace the `drivers` table definition with:

```sql
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
```

Then add this index after `create index if not exists drivers_school_id_idx on drivers (school_id);`:

```sql
create index if not exists drivers_default_bus_id_idx on drivers (default_bus_id) where default_bus_id is not null;
```

- [ ] **Step 3: Update demo seed driver**

In `backend/db/seeds/001_demo_seed.sql`, update the demo driver insert to include `default_bus_id`:

```sql
insert into drivers (
  id,
  school_id,
  full_name,
  phone,
  default_bus_id,
  pin_hash,
  active
) values (
  '33333333-3333-3333-3333-333333333333',
  '11111111-1111-1111-1111-111111111111',
  'Peter Mwangi',
  '+254700000001',
  '22222222-2222-2222-2222-222222222222',
  'pbkdf2_sha256$200000$demo-driver-salt$ooEh79F7IwGlxeLQ4G000PzDJkAtL1EHMqH7/qj6jb0=',
  true
) on conflict (id) do update set
  school_id = excluded.school_id,
  full_name = excluded.full_name,
  phone = excluded.phone,
  default_bus_id = excluded.default_bus_id,
  pin_hash = excluded.pin_hash,
  active = excluded.active;
```

- [ ] **Step 4: Update `scripts/start-local.sh` migration constants**

Replace:

```bash
MIGRATION_FILE="$ROOT_DIR/backend/db/migrations/001_initial_schema.sql"
```

with:

```bash
MIGRATIONS_DIR="$ROOT_DIR/backend/db/migrations"
```

- [ ] **Step 5: Replace `apply_migration_and_seed` in `scripts/start-local.sh`**

Replace the whole `apply_migration_and_seed()` function with:

```bash
apply_migrations() {
  if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo "Cannot initialize local database: migrations directory is missing at $MIGRATIONS_DIR." >&2
    exit 1
  fi

  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
create table if not exists ${MIGRATION_MARKER_TABLE} (id text primary key, applied_at timestamptz not null default now());
SQL

  for migration_path in "$MIGRATIONS_DIR"/*.sql; do
    if [ ! -f "$migration_path" ]; then
      echo "No migration files found in $MIGRATIONS_DIR." >&2
      exit 1
    fi

    migration_id="$(basename "$migration_path" .sql)"
    already_applied="$(
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
        "select exists (select 1 from ${MIGRATION_MARKER_TABLE} where id = '${migration_id}');"
    )"

    if [ "$already_applied" = "t" ]; then
      echo "Migration $migration_id already applied."
    else
      echo "Applying local database migration $migration_id..."
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$migration_path"
      docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
        "insert into ${MIGRATION_MARKER_TABLE} (id) values ('${migration_id}') on conflict (id) do nothing;"
    fi
  done
}

apply_seed() {
  if [ ! -f "$SEED_FILE" ]; then
    echo "Cannot initialize local database: seed file is missing at $SEED_FILE." >&2
    exit 1
  fi

  echo "Applying local database seed..."
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$SEED_FILE"
}

apply_migration_and_seed() {
  apply_migrations
  apply_seed
}
```

- [ ] **Step 6: Update `scripts/reset-local-db.sh` constants and migration logic**

Replace:

```bash
MIGRATION_FILE="$ROOT_DIR/backend/db/migrations/001_initial_schema.sql"
```

with:

```bash
MIGRATIONS_DIR="$ROOT_DIR/backend/db/migrations"
```

Replace the migration file existence check:

```bash
if [ ! -f "$MIGRATION_FILE" ]; then
  echo "Cannot reset local database: migration file is missing at $MIGRATION_FILE." >&2
  exit 1
fi
```

with:

```bash
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "Cannot reset local database: migrations directory is missing at $MIGRATIONS_DIR." >&2
  exit 1
fi
```

Replace:

```bash
docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$MIGRATION_FILE"
```

with:

```bash
for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  if [ ! -f "$migration_path" ]; then
    echo "No migration files found in $MIGRATIONS_DIR." >&2
    exit 1
  fi

  echo "Applying $(basename "$migration_path")..."
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$migration_path"
done
```

Replace the marker insert block with:

```bash
docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL
create table if not exists ${MIGRATION_MARKER_TABLE} (id text primary key, applied_at timestamptz not null default now());
SQL

for migration_path in "$MIGRATIONS_DIR"/*.sql; do
  migration_id="$(basename "$migration_path" .sql)"
  docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "insert into ${MIGRATION_MARKER_TABLE} (id) values ('${migration_id}') on conflict (id) do nothing;"
done
```

- [ ] **Step 7: Run shell syntax checks**

Run:

```bash
bash -n scripts/start-local.sh
bash -n scripts/reset-local-db.sh
```

Expected: both commands exit with code 0 and print no output.

- [ ] **Step 8: Commit database and script changes**

```bash
git add backend/db/migrations/001_initial_schema.sql backend/db/migrations/002_admin_setup_fixes.sql backend/db/seeds/001_demo_seed.sql scripts/start-local.sh scripts/reset-local-db.sh
git commit -m "feat: add admin setup database migration"
```

## Task 2: Backend Service Contract Tests

**Files:**
- Modify: `backend/tests/services/test_admin_service.py`

- [ ] **Step 1: Extend the fake DAO**

Add these attributes and methods to `FakeAdminDao` in `backend/tests/services/test_admin_service.py`:

```python
        self.updated_student = None
        self.created_student_setup = None
        self.parent_link_token = None
        self.bus_exists = True

    def bus_belongs_to_school(self, school_id: str, bus_id: str) -> bool:
        return self.bus_exists

    def update_student(self, student_id: str, input_data):
        self.updated_student = (student_id, input_data)
        return {
            "id": student_id,
            "school_id": input_data.school_id,
            "full_name": input_data.full_name,
            "home_address": input_data.home_address,
            "home_location_note": input_data.home_location_note,
        }

    def create_student_setup(self, input_data, parent_link_token: str | None):
        self.created_student_setup = input_data
        self.parent_link_token = parent_link_token
        return {
            "status": "ok",
            "data": {
                "student": {"id": "student-1", "full_name": input_data.student.full_name},
                "parentContact": {"id": "parent-contact-1"} if input_data.parent_contact else None,
                "parentLink": {"token": parent_link_token} if parent_link_token else None,
                "tripPassenger": {"id": "trip-passenger-1"} if input_data.trip_assignment else None,
            },
        }
```

Update `create_driver` in `FakeAdminDao` to include `defaultBusId`:

```python
    def create_driver(self, input_data, pin_hash: str):
        self.created_driver = (input_data, pin_hash)
        return {
            "id": "driver-1",
            "school_id": input_data.school_id,
            "full_name": input_data.full_name,
            "phone": input_data.phone,
            "default_bus_id": input_data.default_bus_id,
        }
```

- [ ] **Step 2: Add test input classes**

Add these classes below `CorrectionInput`:

```python
class UpdateStudentInput:
    school_id = "school-1"
    full_name = "Amina Otieno"
    home_address = "Updated Kilimani Road"
    home_location_note = "Gate C"


class StudentSetupStudentInput:
    full_name = "Nia Wanjiku"
    home_address = "Ngong Road"
    home_location_note = "Near main gate"


class StudentSetupParentInput:
    contact_1_name = "Mary Wanjiku"
    contact_1_phone = "+254700000010"
    contact_1_relationship = "Mother"
    contact_2_name = "John Wanjiku"
    contact_2_phone = "+254700000011"
    contact_2_relationship = "Father"


class StudentSetupTripInput:
    trip_id = "trip-1"
    sequence_position = 3
    estimated_minutes_from_start = 12


class StudentSetupInput:
    school_id = "school-1"
    student = StudentSetupStudentInput()
    parent_contact = StudentSetupParentInput()
    create_parent_link = True
    trip_assignment = StudentSetupTripInput()
```

- [ ] **Step 3: Add service tests**

Append these tests:

```python
def test_create_driver_stores_phone_and_default_bus() -> None:
    dao = FakeAdminDao()
    input_data = DriverInput()
    input_data.default_bus_id = "bus-1"
    service = AdminService(dao)

    result = service.create_driver(input_data)

    assert result["phone"] == "+254700000001"
    assert result["default_bus_id"] == "bus-1"
    assert dao.created_driver[0].default_bus_id == "bus-1"


def test_create_driver_rejects_cross_school_default_bus() -> None:
    dao = FakeAdminDao()
    dao.bus_exists = False
    input_data = DriverInput()
    input_data.default_bus_id = "bus-from-another-school"
    service = AdminService(dao)

    with pytest.raises(BadRequestError, match="Default bus is invalid"):
        service.create_driver(input_data)


def test_update_student_validates_and_delegates() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.update_student("student-1", UpdateStudentInput())

    assert result["home_address"] == "Updated Kilimani Road"
    assert dao.updated_student[0] == "student-1"


def test_update_student_rejects_empty_address() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    input_data = UpdateStudentInput()
    input_data.home_address = "   "

    with pytest.raises(BadRequestError, match="Student home address is required"):
        service.update_student("student-1", input_data)


def test_create_student_setup_generates_parent_link_token_and_delegates() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.create_student_setup(StudentSetupInput())

    assert result["student"]["id"] == "student-1"
    assert result["parentContact"]["id"] == "parent-contact-1"
    assert result["parentLink"]["token"] == dao.parent_link_token
    assert result["tripPassenger"]["id"] == "trip-passenger-1"
    assert isinstance(dao.parent_link_token, str)
    assert len(dao.parent_link_token) >= 32


def test_create_student_setup_without_optional_sections_creates_student_only() -> None:
    dao = FakeAdminDao()
    input_data = StudentSetupInput()
    input_data.parent_contact = None
    input_data.create_parent_link = False
    input_data.trip_assignment = None
    service = AdminService(dao)

    result = service.create_student_setup(input_data)

    assert result["student"]["id"] == "student-1"
    assert result["parentContact"] is None
    assert result["parentLink"] is None
    assert result["tripPassenger"] is None
    assert dao.parent_link_token is None


def test_create_student_setup_rejects_missing_parent_phone() -> None:
    dao = FakeAdminDao()
    input_data = StudentSetupInput()
    input_data.parent_contact.contact_1_phone = ""
    service = AdminService(dao)

    with pytest.raises(BadRequestError, match="Primary parent phone is required"):
        service.create_student_setup(input_data)
```

- [ ] **Step 4: Run the service tests and confirm they fail**

Run:

```bash
cd backend
../.venv/bin/python -m pytest tests/services/test_admin_service.py -v
```

Expected: tests fail because `default_bus_id`, `update_student`, and `create_student_setup` are not implemented yet.

- [ ] **Step 5: Commit failing tests**

```bash
git add backend/tests/services/test_admin_service.py
git commit -m "test: cover admin setup service contracts"
```

## Task 3: Backend Schemas, Service, API, And DAO

**Files:**
- Modify: `backend/app/schemas/admin.py`
- Modify: `backend/app/services/admin_service.py`
- Modify: `backend/app/dao/admin_dao.py`
- Modify: `backend/app/api/admin.py`
- Test: `backend/tests/services/test_admin_service.py`

- [ ] **Step 1: Add admin schemas**

In `backend/app/schemas/admin.py`, add `default_bus_id` to `CreateDriverRequest`:

```python
class CreateDriverRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    phone: str | None = None
    default_bus_id: str | None = Field(default=None, alias="defaultBusId")
    pin: str
```

Add these schemas after `CreateStudentRequest`:

```python
class UpdateStudentRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class StudentSetupStudentRequest(BaseModel):
    full_name: str = Field(alias="fullName")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class StudentSetupParentContactRequest(BaseModel):
    contact_1_name: str = Field(alias="contact1Name")
    contact_1_phone: str = Field(alias="contact1Phone")
    contact_1_relationship: str = Field(alias="contact1Relationship")
    contact_2_name: str | None = Field(default=None, alias="contact2Name")
    contact_2_phone: str | None = Field(default=None, alias="contact2Phone")
    contact_2_relationship: str | None = Field(default=None, alias="contact2Relationship")


class StudentSetupTripAssignmentRequest(BaseModel):
    trip_id: str = Field(alias="tripId")
    sequence_position: int = Field(alias="sequencePosition")
    estimated_minutes_from_start: int = Field(alias="estimatedMinutesFromStart")


class CreateStudentSetupRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student: StudentSetupStudentRequest
    parent_contact: StudentSetupParentContactRequest | None = Field(default=None, alias="parentContact")
    create_parent_link: bool = Field(default=False, alias="createParentLink")
    trip_assignment: StudentSetupTripAssignmentRequest | None = Field(default=None, alias="tripAssignment")
```

- [ ] **Step 2: Add service methods**

In `backend/app/services/admin_service.py`, add this import:

```python
from secrets import token_urlsafe
```

Add these methods inside `AdminService`:

```python
    def update_student(self, student_id: str, input_data):
        self._require_text(input_data.full_name, "Student full name is required")
        self._require_text(input_data.home_address, "Student home address is required")
        result = self.dao.update_student(student_id, input_data)
        if not result:
            raise NotFoundError("Student not found")
        return result

    def create_student_setup(self, input_data):
        self._require_text(input_data.student.full_name, "Student full name is required")
        self._require_text(input_data.student.home_address, "Student home address is required")

        if input_data.parent_contact:
            self._require_text(input_data.parent_contact.contact_1_name, "Primary parent name is required")
            self._require_text(input_data.parent_contact.contact_1_phone, "Primary parent phone is required")
            self._require_text(
                input_data.parent_contact.contact_1_relationship,
                "Primary parent relationship is required",
            )

        if input_data.trip_assignment:
            if input_data.trip_assignment.sequence_position < 1:
                raise BadRequestError("Stop number must be greater than zero")
            if input_data.trip_assignment.estimated_minutes_from_start < 0:
                raise BadRequestError("Minutes from start must be zero or greater")

        parent_link_token = token_urlsafe(24) if input_data.create_parent_link else None
        result = self.dao.create_student_setup(input_data, parent_link_token)
        if result["status"] == "trip_not_found":
            raise NotFoundError("Trip was not found for this school")
        return result["data"]

    def _require_text(self, value: str | None, message: str) -> None:
        if value is None or not value.strip():
            raise BadRequestError(message)
```

Update `create_driver` to validate default bus before hashing the PIN:

```python
    def create_driver(self, input_data):
        if getattr(input_data, "default_bus_id", None):
            if not self.dao.bus_belongs_to_school(input_data.school_id, input_data.default_bus_id):
                raise BadRequestError("Default bus is invalid")

        try:
            pin_hash = hash_pin(input_data.pin)
        except ValueError as error:
            raise BadRequestError(str(error)) from error
        return self.dao.create_driver(input_data, pin_hash)
```

- [ ] **Step 3: Update DAO list and driver methods**

In `backend/app/dao/admin_dao.py`, change `list_students` query to include location note:

```python
                select id, full_name, home_address, home_location_note
```

Replace `list_drivers` with:

```python
    def list_drivers(self, school_id: str) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                select
                    d.id,
                    d.full_name,
                    d.phone,
                    d.default_bus_id,
                    b.label as default_bus_label
                from drivers d
                left join buses b on b.id = d.default_bus_id and b.school_id = d.school_id
                where d.school_id = %s and d.active = true
                order by d.full_name asc
                """,
                (school_id,),
            ).fetchall()
        return [dict(row) for row in rows]
```

Replace `create_driver` with:

```python
    def create_driver(self, input_data, pin_hash: str) -> dict[str, Any]:
        with get_connection() as conn:
            row = conn.execute(
                """
                insert into drivers (school_id, full_name, phone, default_bus_id, pin_hash)
                values (%s, %s, %s, %s, %s)
                returning id, school_id, full_name, phone, default_bus_id
                """,
                (
                    input_data.school_id,
                    input_data.full_name,
                    input_data.phone,
                    input_data.default_bus_id,
                    pin_hash,
                ),
            ).fetchone()
        return dict(row)
```

Add this method after `list_buses`:

```python
    def bus_belongs_to_school(self, school_id: str, bus_id: str) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                """
                select 1
                from buses
                where id = %s and school_id = %s and active = true
                """,
                (bus_id, school_id),
            ).fetchone()
        return row is not None
```

- [ ] **Step 4: Add DAO student update and combined setup**

Add these methods to `AdminDao` before `upsert_daily_attendance`:

```python
    def update_student(self, student_id: str, input_data) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                update students
                set full_name = %s,
                    home_address = %s,
                    home_location_note = %s
                where id = %s
                    and school_id = %s
                    and active = true
                returning id, school_id, full_name, home_address, home_location_note
                """,
                (
                    input_data.full_name,
                    input_data.home_address,
                    input_data.home_location_note,
                    student_id,
                    input_data.school_id,
                ),
            ).fetchone()
        return dict(row) if row else None

    def create_student_setup(self, input_data, parent_link_token: str | None) -> dict[str, Any]:
        with get_connection() as conn:
            with conn.transaction():
                student = conn.execute(
                    """
                    insert into students (school_id, full_name, home_address, home_location_note)
                    values (%s, %s, %s, %s)
                    returning id, school_id, full_name, home_address, home_location_note
                    """,
                    (
                        input_data.school_id,
                        input_data.student.full_name,
                        input_data.student.home_address,
                        input_data.student.home_location_note,
                    ),
                ).fetchone()

                parent_contact = None
                if input_data.parent_contact:
                    parent_contact = conn.execute(
                        """
                        insert into parent_contacts (
                            school_id,
                            student_id,
                            contact_1_name,
                            contact_1_phone,
                            contact_1_relationship,
                            contact_2_name,
                            contact_2_phone,
                            contact_2_relationship
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (student_id) do update set
                            school_id = excluded.school_id,
                            contact_1_name = excluded.contact_1_name,
                            contact_1_phone = excluded.contact_1_phone,
                            contact_1_relationship = excluded.contact_1_relationship,
                            contact_2_name = excluded.contact_2_name,
                            contact_2_phone = excluded.contact_2_phone,
                            contact_2_relationship = excluded.contact_2_relationship
                        returning *
                        """,
                        (
                            input_data.school_id,
                            student["id"],
                            input_data.parent_contact.contact_1_name,
                            input_data.parent_contact.contact_1_phone,
                            input_data.parent_contact.contact_1_relationship,
                            input_data.parent_contact.contact_2_name,
                            input_data.parent_contact.contact_2_phone,
                            input_data.parent_contact.contact_2_relationship,
                        ),
                    ).fetchone()

                parent_link = None
                if parent_link_token:
                    parent_link = conn.execute(
                        """
                        insert into parent_links (school_id, student_id, token)
                        values (%s, %s, %s)
                        returning id, school_id, student_id, token
                        """,
                        (input_data.school_id, student["id"], parent_link_token),
                    ).fetchone()

                trip_passenger = None
                if input_data.trip_assignment:
                    trip = conn.execute(
                        """
                        select id
                        from trips
                        where id = %s and school_id = %s
                        """,
                        (input_data.trip_assignment.trip_id, input_data.school_id),
                    ).fetchone()
                    if not trip:
                        return {"status": "trip_not_found"}

                    trip_passenger = conn.execute(
                        """
                        insert into trip_passengers (
                            school_id,
                            trip_id,
                            passenger_type,
                            student_id,
                            sequence_position,
                            estimated_minutes_from_start
                        )
                        values (%s, %s, 'student', %s, %s, %s)
                        returning *
                        """,
                        (
                            input_data.school_id,
                            input_data.trip_assignment.trip_id,
                            student["id"],
                            input_data.trip_assignment.sequence_position,
                            input_data.trip_assignment.estimated_minutes_from_start,
                        ),
                    ).fetchone()

                return {
                    "status": "ok",
                    "data": {
                        "student": dict(student),
                        "parentContact": dict(parent_contact) if parent_contact else None,
                        "parentLink": dict(parent_link) if parent_link else None,
                        "tripPassenger": dict(trip_passenger) if trip_passenger else None,
                    },
                }
```

- [ ] **Step 5: Expose API routes**

In `backend/app/api/admin.py`, add imports:

```python
    CreateStudentSetupRequest,
    UpdateStudentRequest,
```

Add routes after `list_students`:

```python
@router.patch("/students/{student_id}")
def update_student(student_id: str, request: UpdateStudentRequest):
    return safe_call(lambda: service.update_student(student_id, request))


@router.post("/student-setups")
def create_student_setup(request: CreateStudentSetupRequest):
    return safe_call(lambda: service.create_student_setup(request))
```

- [ ] **Step 6: Run backend service tests**

Run:

```bash
cd backend
../.venv/bin/python -m pytest tests/services/test_admin_service.py -v
```

Expected: all tests in `test_admin_service.py` pass.

- [ ] **Step 7: Run full backend tests**

Run:

```bash
cd backend
../.venv/bin/python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 8: Commit backend implementation**

```bash
git add backend/app/schemas/admin.py backend/app/services/admin_service.py backend/app/dao/admin_dao.py backend/app/api/admin.py backend/tests/services/test_admin_service.py
git commit -m "feat: add combined student setup backend"
```

## Task 4: Frontend API Client And Types

**Files:**
- Modify: `frontend/src/services/httpClient.ts`
- Modify: `frontend/src/services/adminApi.ts`
- Modify: `frontend/tests/unit/httpClient.test.ts`

- [ ] **Step 1: Add failing `apiPatch` test**

In `frontend/tests/unit/httpClient.test.ts`, append:

```typescript
  it("sends PATCH requests with JSON bodies", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "student-1" }), { status: 200 })
      );

    await expect(
      apiPatch("/api/admin/students/student-1", { homeAddress: "New" })
    ).resolves.toEqual({ id: "student-1" });

    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ homeAddress: "New" })
    });
    fetchMock.mockRestore();
  });
```

Also update the import:

```typescript
import { apiGet, apiPatch, apiPost } from "../../src/services/httpClient";
```

- [ ] **Step 2: Run the HTTP client test and confirm it fails**

Run:

```bash
cd frontend
../.tools/bin/npm exec vitest --run tests/unit/httpClient.test.ts
```

Expected: fail because `apiPatch` is not exported.

- [ ] **Step 3: Implement `apiPatch`**

Add this function to `frontend/src/services/httpClient.ts`:

```typescript
export async function apiPatch(path: string, body?: unknown) {
  const response = await fetch(buildUrl(path), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body === undefined ? {} : body)
  });

  return parseResponse(response);
}
```

- [ ] **Step 4: Update admin API types and calls**

In `frontend/src/services/adminApi.ts`, change the import:

```typescript
import { apiGet, apiPatch, apiPost } from "./httpClient";
```

Add these types after `CreateBusInput`:

```typescript
export type StudentSummary = {
  id: string;
  full_name: string;
  home_address: string;
  home_location_note?: string | null;
};

export type BusSummary = {
  id: string;
  label: string;
  registration_number?: string | null;
};

export type DriverSummary = {
  id: string;
  full_name: string;
  phone?: string | null;
  default_bus_id?: string | null;
  default_bus_label?: string | null;
};
```

Update `CreateDriverInput`:

```typescript
export type CreateDriverInput = {
  schoolId: string;
  fullName: string;
  phone?: string;
  defaultBusId?: string;
  pin: string;
};
```

Add these types after `CreateStudentInput`:

```typescript
export type UpdateStudentInput = CreateStudentInput & {
  studentId: string;
};

export type StudentSetupInput = {
  schoolId: string;
  student: {
    fullName: string;
    homeAddress: string;
    homeLocationNote?: string;
  };
  parentContact?: {
    contact1Name: string;
    contact1Phone: string;
    contact1Relationship: string;
    contact2Name?: string;
    contact2Phone?: string;
    contact2Relationship?: string;
  };
  createParentLink: boolean;
  tripAssignment?: {
    tripId: string;
    sequencePosition: number;
    estimatedMinutesFromStart: number;
  };
};

export type StudentSetupResult = {
  student: StudentSummary;
  parentContact: Record<string, unknown> | null;
  parentLink: { token: string } | null;
  tripPassenger: Record<string, unknown> | null;
};
```

Update list function signatures:

```typescript
export async function listStudents(schoolId: string): Promise<StudentSummary[]> {
  return apiGet("/api/admin/students", { school_id: schoolId });
}

export async function listBuses(schoolId: string): Promise<BusSummary[]> {
  return apiGet("/api/admin/buses", { school_id: schoolId });
}

export async function listDrivers(schoolId: string): Promise<DriverSummary[]> {
  return apiGet("/api/admin/drivers", { school_id: schoolId });
}
```

Add new functions after `createStudent`:

```typescript
export async function updateStudent(input: UpdateStudentInput) {
  const { studentId, ...body } = input;
  return apiPatch(`/api/admin/students/${studentId}`, body);
}

export async function createStudentSetup(
  input: StudentSetupInput
): Promise<StudentSetupResult> {
  return apiPost("/api/admin/student-setups", input);
}
```

- [ ] **Step 5: Run HTTP client test**

Run:

```bash
cd frontend
../.tools/bin/npm exec vitest --run tests/unit/httpClient.test.ts
```

Expected: pass.

- [ ] **Step 6: Run TypeScript check**

Run:

```bash
cd frontend
../.tools/bin/npm run lint
```

Expected: pass.

- [ ] **Step 7: Commit frontend API changes**

```bash
git add frontend/src/services/httpClient.ts frontend/src/services/adminApi.ts frontend/tests/unit/httpClient.test.ts
git commit -m "feat: add admin setup frontend API calls"
```

## Task 5: Frontend School Setup Tests

**Files:**
- Create: `frontend/tests/unit/SchoolSetup.test.tsx`
- Test: `frontend/src/features/admin/SchoolSetup.tsx`

- [ ] **Step 1: Create the test file**

Create `frontend/tests/unit/SchoolSetup.test.tsx`:

```typescript
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SchoolSetup } from "../../src/features/admin/SchoolSetup";
import {
  createDriver,
  createStudentSetup,
  createTrip,
  listBuses,
  listDrivers,
  listStudents,
  listTrips,
  updateStudent
} from "../../src/services/adminApi";

vi.mock("../../src/services/adminApi", () => ({
  createBus: vi.fn(),
  createDriver: vi.fn(),
  createParentContact: vi.fn(),
  createParentLink: vi.fn(),
  createStudent: vi.fn(),
  createStudentSetup: vi.fn(),
  createTrip: vi.fn(),
  createTripPassenger: vi.fn(),
  listBuses: vi.fn(),
  listDrivers: vi.fn(),
  listStudents: vi.fn(),
  listTrips: vi.fn(),
  updateStudent: vi.fn()
}));

const schoolId = "11111111-1111-1111-1111-111111111111";
const mockedListStudents = vi.mocked(listStudents);
const mockedListBuses = vi.mocked(listBuses);
const mockedListDrivers = vi.mocked(listDrivers);
const mockedListTrips = vi.mocked(listTrips);
const mockedCreateStudentSetup = vi.mocked(createStudentSetup);
const mockedUpdateStudent = vi.mocked(updateStudent);
const mockedCreateDriver = vi.mocked(createDriver);
const mockedCreateTrip = vi.mocked(createTrip);

async function renderAndLoadSetup() {
  render(<SchoolSetup />);
  fireEvent.change(screen.getByLabelText("School ID"), {
    target: { value: schoolId }
  });
  fireEvent.click(screen.getByRole("button", { name: "Load setup data" }));
  await waitFor(() => expect(mockedListStudents).toHaveBeenCalledWith(schoolId));
}

describe("SchoolSetup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedListStudents.mockResolvedValue([
      {
        id: "student-1",
        full_name: "Amina Otieno",
        home_address: "Old Road",
        home_location_note: "Old gate"
      }
    ]);
    mockedListBuses.mockResolvedValue([
      { id: "bus-1", label: "Van 1", registration_number: "KDA 123A" }
    ]);
    mockedListDrivers.mockResolvedValue([
      {
        id: "driver-1",
        full_name: "Peter Mwangi",
        phone: "+254700000001",
        default_bus_id: "bus-1",
        default_bus_label: "Van 1"
      }
    ]);
    mockedListTrips.mockResolvedValue([
      {
        id: "trip-1",
        name: "Morning Route A",
        service_date: "2026-06-10",
        scheduled_start: "06:30:00",
        status: "scheduled"
      }
    ]);
  });

  it("creates a student with parent details, parent link, and trip assignment", async () => {
    mockedCreateStudentSetup.mockResolvedValue({
      student: {
        id: "student-2",
        full_name: "Nia Wanjiku",
        home_address: "Ngong Road",
        home_location_note: "Gate C"
      },
      parentContact: { id: "parent-contact-1" },
      parentLink: { token: "parent-token-123456789012345678901234567890" },
      tripPassenger: { id: "trip-passenger-1" }
    });

    await renderAndLoadSetup();
    fireEvent.click(screen.getByRole("button", { name: "Students" }));

    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Nia Wanjiku" }
    });
    fireEvent.change(screen.getByLabelText("Home address"), {
      target: { value: "Ngong Road" }
    });
    fireEvent.change(screen.getByLabelText("Location note"), {
      target: { value: "Gate C" }
    });
    fireEvent.change(screen.getByLabelText("Primary parent name"), {
      target: { value: "Mary Wanjiku" }
    });
    fireEvent.change(screen.getByLabelText("Primary parent phone"), {
      target: { value: "+254700000010" }
    });
    fireEvent.change(screen.getByLabelText("Primary relationship"), {
      target: { value: "Mother" }
    });
    fireEvent.click(screen.getByLabelText("Create parent link"));
    fireEvent.click(screen.getByLabelText("Assign to trip"));
    fireEvent.change(screen.getByLabelText("Trip"), {
      target: { value: "trip-1" }
    });
    fireEvent.change(screen.getByLabelText("Stop number"), {
      target: { value: "3" }
    });
    fireEvent.change(screen.getByLabelText("Minutes from start"), {
      target: { value: "12" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Save student setup" }));

    await waitFor(() => expect(mockedCreateStudentSetup).toHaveBeenCalled());
    expect(mockedCreateStudentSetup).toHaveBeenCalledWith({
      schoolId,
      student: {
        fullName: "Nia Wanjiku",
        homeAddress: "Ngong Road",
        homeLocationNote: "Gate C"
      },
      parentContact: {
        contact1Name: "Mary Wanjiku",
        contact1Phone: "+254700000010",
        contact1Relationship: "Mother"
      },
      createParentLink: true,
      tripAssignment: {
        tripId: "trip-1",
        sequencePosition: 3,
        estimatedMinutesFromStart: 12
      }
    });
    expect(await screen.findByText(/student-2/)).toBeInTheDocument();
    expect(screen.getByText(/parent-token/)).toBeInTheDocument();
  });

  it("edits an existing student address", async () => {
    mockedUpdateStudent.mockResolvedValue({ id: "student-1" });

    await renderAndLoadSetup();
    fireEvent.click(screen.getByRole("button", { name: "Students" }));
    fireEvent.change(screen.getByLabelText("Student to edit"), {
      target: { value: "student-1" }
    });
    fireEvent.change(screen.getByLabelText("Edit home address"), {
      target: { value: "Updated Road" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Save address" }));

    await waitFor(() => expect(mockedUpdateStudent).toHaveBeenCalled());
    expect(mockedUpdateStudent).toHaveBeenCalledWith({
      schoolId,
      studentId: "student-1",
      fullName: "Amina Otieno",
      homeAddress: "Updated Road",
      homeLocationNote: "Old gate"
    });
  });

  it("creates a driver with phone and default bus", async () => {
    mockedCreateDriver.mockResolvedValue({ id: "driver-2" });

    await renderAndLoadSetup();
    fireEvent.click(screen.getByRole("button", { name: "Drivers" }));
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Grace Njeri" }
    });
    fireEvent.change(screen.getByLabelText("Phone"), {
      target: { value: "+254700000020" }
    });
    fireEvent.change(screen.getByLabelText("Default bus"), {
      target: { value: "bus-1" }
    });
    fireEvent.change(screen.getByLabelText("PIN"), {
      target: { value: "2468" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Save driver" }));

    await waitFor(() => expect(mockedCreateDriver).toHaveBeenCalled());
    expect(mockedCreateDriver).toHaveBeenCalledWith({
      schoolId,
      fullName: "Grace Njeri",
      phone: "+254700000020",
      defaultBusId: "bus-1",
      pin: "2468"
    });
  });

  it("creates a trip from bus and driver dropdown selections", async () => {
    mockedCreateTrip.mockResolvedValue({ id: "trip-2" });

    await renderAndLoadSetup();
    fireEvent.click(screen.getByRole("button", { name: "Trips" }));
    fireEvent.change(screen.getByLabelText("Trip name"), {
      target: { value: "Afternoon Route" }
    });
    fireEvent.change(screen.getByLabelText("Bus"), {
      target: { value: "bus-1" }
    });
    fireEvent.change(screen.getByLabelText("Driver"), {
      target: { value: "driver-1" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Save trip" }));

    await waitFor(() => expect(mockedCreateTrip).toHaveBeenCalled());
    expect(mockedCreateTrip.mock.calls[0][0]).toMatchObject({
      schoolId,
      busId: "bus-1",
      driverId: "driver-1",
      name: "Afternoon Route"
    });
  });
});
```

- [ ] **Step 2: Run the SchoolSetup test and confirm it fails**

Run:

```bash
cd frontend
../.tools/bin/npm exec vitest --run tests/unit/SchoolSetup.test.tsx
```

Expected: fail because the combined setup UI and new labels are not implemented.

- [ ] **Step 3: Commit failing frontend tests**

```bash
git add frontend/tests/unit/SchoolSetup.test.tsx
git commit -m "test: cover admin setup form workflows"
```

## Task 6: Frontend School Setup Implementation

**Files:**
- Modify: `frontend/src/features/admin/SchoolSetup.tsx`
- Test: `frontend/tests/unit/SchoolSetup.test.tsx`

- [ ] **Step 1: Update imports**

In `frontend/src/features/admin/SchoolSetup.tsx`, replace the admin API import block with:

```typescript
import {
  createBus,
  createDriver,
  createStudentSetup,
  createTrip,
  createTripPassenger,
  listBuses,
  listDrivers,
  listStudents,
  listTrips,
  updateStudent
} from "../../services/adminApi";
import type {
  BusSummary,
  DriverSummary,
  StudentSummary,
  TripSession,
  AdminTripSummary
} from "../../services/adminApi";
```

Remove these imports from the block if present:

```typescript
  createParentContact,
  createParentLink,
  createStudent,
```

- [ ] **Step 2: Add setup option state**

Inside `SchoolSetup`, after the existing `schoolId` state, add:

```typescript
  const [students, setStudents] = useState<StudentSummary[]>([]);
  const [buses, setBuses] = useState<BusSummary[]>([]);
  const [drivers, setDrivers] = useState<DriverSummary[]>([]);
  const [trips, setTrips] = useState<AdminTripSummary[]>([]);
```

Add these new states near the existing student states:

```typescript
  const [createParentLinkForStudent, setCreateParentLinkForStudent] = useState(false);
  const [assignStudentToTrip, setAssignStudentToTrip] = useState(false);
  const [studentSetupTripId, setStudentSetupTripId] = useState("");
  const [studentSetupSequence, setStudentSetupSequence] = useState("1");
  const [studentSetupMinutes, setStudentSetupMinutes] = useState("0");
  const [createdStudentSummary, setCreatedStudentSummary] = useState("");
  const [createdParentLink, setCreatedParentLink] = useState("");
  const [editStudentId, setEditStudentId] = useState("");
  const [editStudentFullName, setEditStudentFullName] = useState("");
  const [editStudentHomeAddress, setEditStudentHomeAddress] = useState("");
  const [editStudentLocationNote, setEditStudentLocationNote] = useState("");
  const [driverDefaultBusId, setDriverDefaultBusId] = useState("");
```

- [ ] **Step 3: Add setup data loader and student edit selector**

Add these functions before `handleCreateBus`:

```typescript
  async function loadSetupData() {
    if (!schoolId) {
      setErrorMessage("Enter a school ID before loading setup data.");
      return;
    }

    startSaving();
    try {
      const [nextStudents, nextBuses, nextDrivers, nextTrips] = await Promise.all([
        listStudents(schoolId),
        listBuses(schoolId),
        listDrivers(schoolId),
        listTrips(schoolId)
      ]);
      setStudents(nextStudents);
      setBuses(nextBuses);
      setDrivers(nextDrivers);
      setTrips(nextTrips);
      setStatusMessage("Setup data loaded.");
    } catch (error) {
      saveFailed(error, "Could not load setup data.");
    } finally {
      setIsSaving(false);
    }
  }

  function selectStudentToEdit(studentId: string) {
    setEditStudentId(studentId);
    const student = students.find((item) => item.id === studentId);
    setEditStudentFullName(student ? student.full_name : "");
    setEditStudentHomeAddress(student ? student.home_address : "");
    setEditStudentLocationNote(student && student.home_location_note ? student.home_location_note : "");
  }
```

Add a button after the School ID input:

```tsx
      <button disabled={isSaving || !schoolId} onClick={loadSetupData} type="button">
        Load setup data
      </button>
```

- [ ] **Step 4: Replace student submit handler**

Replace `handleCreateStudent` with:

```typescript
  async function handleCreateStudent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    startSaving();
    setCreatedStudentSummary("");
    setCreatedParentLink("");

    try {
      const result = await createStudentSetup({
        schoolId,
        student: {
          fullName: studentFullName,
          homeAddress: studentHomeAddress,
          homeLocationNote: studentLocationNote || undefined
        },
        parentContact:
          contact1Name || contact1Phone || contact1Relationship
            ? {
                contact1Name,
                contact1Phone,
                contact1Relationship,
                contact2Name: contact2Name || undefined,
                contact2Phone: contact2Phone || undefined,
                contact2Relationship: contact2Relationship || undefined
              }
            : undefined,
        createParentLink: createParentLinkForStudent,
        tripAssignment: assignStudentToTrip
          ? {
              tripId: studentSetupTripId,
              sequencePosition: Number(studentSetupSequence),
              estimatedMinutesFromStart: Number(studentSetupMinutes)
            }
          : undefined
      });

      setStudents((current) => [...current, result.student]);
      setStudentFullName("");
      setStudentHomeAddress("");
      setStudentLocationNote("");
      setContact1Name("");
      setContact1Phone("");
      setContact1Relationship("");
      setContact2Name("");
      setContact2Phone("");
      setContact2Relationship("");
      setCreateParentLinkForStudent(false);
      setAssignStudentToTrip(false);
      setStudentSetupTripId("");
      setCreatedStudentSummary(`Student saved: ${result.student.id}`);
      setCreatedParentLink(
        result.parentLink ? `${window.location.origin}/p/${result.parentLink.token}` : ""
      );
      setStatusMessage(
        result.tripPassenger ? "Student saved and added to trip." : "Student saved."
      );
    } catch (error) {
      saveFailed(error, "Could not save student setup.");
    } finally {
      setIsSaving(false);
    }
  }
```

- [ ] **Step 5: Add student update handler**

Add this handler after `handleCreateStudent`:

```typescript
  async function handleUpdateStudent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    startSaving();

    try {
      const updated = await updateStudent({
        schoolId,
        studentId: editStudentId,
        fullName: editStudentFullName,
        homeAddress: editStudentHomeAddress,
        homeLocationNote: editStudentLocationNote || undefined
      });
      setStudents((current) =>
        current.map((student) =>
          student.id === editStudentId
            ? {
                ...student,
                full_name: editStudentFullName,
                home_address: editStudentHomeAddress,
                home_location_note: editStudentLocationNote || null
              }
            : student
        )
      );
      setStatusMessage(`Student address saved: `);
    } catch (error) {
      saveFailed(error, "Could not update student address.");
    } finally {
      setIsSaving(false);
    }
  }
```

- [ ] **Step 6: Update driver submit handler**

In `handleCreateDriver`, add `defaultBusId` to the payload and reset it after success:

```typescript
      await createDriver({
        schoolId,
        fullName: driverFullName,
        phone: driverPhone || undefined,
        defaultBusId: driverDefaultBusId || undefined,
        pin: driverPin
      });
      setDriverFullName("");
      setDriverPhone("");
      setDriverDefaultBusId("");
      setDriverPin("");
```

- [ ] **Step 7: Replace driver default bus input area**

In the Drivers tab form, add this label between Phone and PIN:

```tsx
            <label>
              Default bus
              <select
                onChange={(event) => setDriverDefaultBusId(event.target.value)}
                value={driverDefaultBusId}
              >
                <option value="">No default bus</option>
                {buses.map((bus) => (
                  <option key={bus.id} value={bus.id}>
                    {bus.label}
                    {bus.registration_number ? ` - ${bus.registration_number}` : ""}
                  </option>
                ))}
              </select>
            </label>
```

- [ ] **Step 8: Replace the Students tab JSX**

Replace the current Students tab form block with this block:

```tsx
        {activeTab === "students" ? (
          <>
            <form onSubmit={handleCreateStudent}>
              <h2>Add Student</h2>
              <label>
                Full name
                <input
                  onChange={(event) => setStudentFullName(event.target.value)}
                  required
                  type="text"
                  value={studentFullName}
                />
              </label>
              <label>
                Home address
                <input
                  onChange={(event) => setStudentHomeAddress(event.target.value)}
                  required
                  type="text"
                  value={studentHomeAddress}
                />
              </label>
              <label>
                Location note
                <input
                  onChange={(event) => setStudentLocationNote(event.target.value)}
                  type="text"
                  value={studentLocationNote}
                />
              </label>
              <label>
                Primary parent name
                <input
                  onChange={(event) => setContact1Name(event.target.value)}
                  type="text"
                  value={contact1Name}
                />
              </label>
              <label>
                Primary parent phone
                <input
                  onChange={(event) => setContact1Phone(event.target.value)}
                  type="tel"
                  value={contact1Phone}
                />
              </label>
              <label>
                Primary relationship
                <input
                  onChange={(event) => setContact1Relationship(event.target.value)}
                  type="text"
                  value={contact1Relationship}
                />
              </label>
              <label>
                Secondary parent name
                <input
                  onChange={(event) => setContact2Name(event.target.value)}
                  type="text"
                  value={contact2Name}
                />
              </label>
              <label>
                Secondary parent phone
                <input
                  onChange={(event) => setContact2Phone(event.target.value)}
                  type="tel"
                  value={contact2Phone}
                />
              </label>
              <label>
                Secondary relationship
                <input
                  onChange={(event) => setContact2Relationship(event.target.value)}
                  type="text"
                  value={contact2Relationship}
                />
              </label>
              <label>
                <input
                  checked={createParentLinkForStudent}
                  onChange={(event) => setCreateParentLinkForStudent(event.target.checked)}
                  type="checkbox"
                />
                Create parent link
              </label>
              <label>
                <input
                  checked={assignStudentToTrip}
                  onChange={(event) => setAssignStudentToTrip(event.target.checked)}
                  type="checkbox"
                />
                Assign to trip
              </label>
              {assignStudentToTrip ? (
                <>
                  <label>
                    Trip
                    <select
                      onChange={(event) => setStudentSetupTripId(event.target.value)}
                      required
                      value={studentSetupTripId}
                    >
                      <option value="">Choose trip</option>
                      {trips.map((trip) => (
                        <option key={trip.id} value={trip.id}>
                          {trip.name} - {trip.service_date} - {trip.scheduled_start}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Stop number
                    <input
                      min={1}
                      onChange={(event) => setStudentSetupSequence(event.target.value)}
                      required
                      type="number"
                      value={studentSetupSequence}
                    />
                  </label>
                  <label>
                    Minutes from start
                    <input
                      min={0}
                      onChange={(event) => setStudentSetupMinutes(event.target.value)}
                      required
                      type="number"
                      value={studentSetupMinutes}
                    />
                  </label>
                </>
              ) : null}
              <button disabled={isSaving || !schoolId} type="submit">
                {isSaving ? "Saving..." : "Save student setup"}
              </button>
              {createdStudentSummary ? <p>{createdStudentSummary}</p> : null}
              {createdParentLink ? <p>{createdParentLink}</p> : null}
            </form>

            <form onSubmit={handleUpdateStudent}>
              <h2>Edit Student Address</h2>
              <label>
                Student to edit
                <select
                  onChange={(event) => selectStudentToEdit(event.target.value)}
                  required
                  value={editStudentId}
                >
                  <option value="">Choose student</option>
                  {students.map((student) => (
                    <option key={student.id} value={student.id}>
                      {student.full_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Edit full name
                <input
                  onChange={(event) => setEditStudentFullName(event.target.value)}
                  required
                  type="text"
                  value={editStudentFullName}
                />
              </label>
              <label>
                Edit home address
                <input
                  onChange={(event) => setEditStudentHomeAddress(event.target.value)}
                  required
                  type="text"
                  value={editStudentHomeAddress}
                />
              </label>
              <label>
                Edit location note
                <input
                  onChange={(event) => setEditStudentLocationNote(event.target.value)}
                  type="text"
                  value={editStudentLocationNote}
                />
              </label>
              <button disabled={isSaving || !schoolId || !editStudentId} type="submit">
                {isSaving ? "Saving..." : "Save address"}
              </button>
            </form>
          </>
        ) : null}
```

- [ ] **Step 9: Replace trip bus and driver fields with dropdowns**

In the Trips tab form, replace the Bus ID label with:

```tsx
              <label>
                Bus
                <select
                  onChange={(event) => setTripBusId(event.target.value)}
                  required
                  value={tripBusId}
                >
                  <option value="">Choose bus</option>
                  {buses.map((bus) => (
                    <option key={bus.id} value={bus.id}>
                      {bus.label}
                      {bus.registration_number ? ` - ${bus.registration_number}` : ""}
                    </option>
                  ))}
                </select>
              </label>
```

Replace the Driver ID label with:

```tsx
              <label>
                Driver
                <select
                  onChange={(event) => setTripDriverId(event.target.value)}
                  value={tripDriverId}
                >
                  <option value="">No driver assigned</option>
                  {drivers.map((driver) => (
                    <option key={driver.id} value={driver.id}>
                      {driver.full_name}
                      {driver.default_bus_label ? ` - default ${driver.default_bus_label}` : ""}
                    </option>
                  ))}
                </select>
              </label>
```

In `handleCreateTrip`, after `setTripPassengerTripId(String(trip.id));`, add:

```typescript
      await loadSetupData();
```

- [ ] **Step 10: Run the SchoolSetup test**

Run:

```bash
cd frontend
../.tools/bin/npm exec vitest --run tests/unit/SchoolSetup.test.tsx
```

Expected: pass.

- [ ] **Step 11: Run all frontend unit tests**

Run:

```bash
cd frontend
../.tools/bin/npm test
```

Expected: all frontend unit tests pass.

- [ ] **Step 12: Commit SchoolSetup implementation**

```bash
git add frontend/src/features/admin/SchoolSetup.tsx frontend/tests/unit/SchoolSetup.test.tsx
git commit -m "feat: improve admin setup forms"
```

## Task 7: End-To-End Verification

**Files:**
- Check: `backend/app/**`
- Check: `frontend/src/**`
- Check: `scripts/start-local.sh`

- [ ] **Step 1: Run backend tests**

Run:

```bash
cd backend
../.venv/bin/python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend unit tests**

Run:

```bash
cd frontend
../.tools/bin/npm test
```

Expected: all frontend unit tests pass.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend
../.tools/bin/npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Run shell syntax checks**

Run:

```bash
bash -n scripts/start-local.sh
bash -n scripts/reset-local-db.sh
```

Expected: both commands exit with code 0 and print no output.

- [ ] **Step 5: Verify local stack when Docker is available**

Run:

```bash
scripts/start-local.sh --reset
```

Expected:

```text
SafeRide local stack is ready.
```

Then open `http://localhost:5173/admin/setup` and verify:

- Load setup data works with school ID `11111111-1111-1111-1111-111111111111`.
- A student can be created with parent details, parent link, and trip assignment.
- The generated parent link opens under `/p/<token>`.
- Editing the student's address changes the value in the student dropdown data after reload.
- A driver can be saved with phone and default bus.
- A trip can be saved using bus and driver dropdowns.

- [ ] **Step 6: Commit verification note if code changed during verification**

If verification required code changes, commit them with:

```bash
git add backend frontend scripts
git commit -m "fix: complete admin setup verification"
```

If verification required no code changes, do not create an empty commit.

## Self-Review

Spec coverage:

- Student address edit is covered by Tasks 2, 3, 5, and 6.
- Route assignment as trip stop is covered by Tasks 3, 5, and 6.
- Combined student, address, parent, optional parent link, and trip stop setup is covered by Tasks 2, 3, 5, and 6.
- Driver phone and default bus are covered by Tasks 1, 2, 3, 5, and 6.
- Dropdowns for bus, driver, trip, and student selection are covered by Tasks 4, 5, and 6.
- Regression verification is covered by Task 7.

Placeholder scan:

- No unresolved draft markers or incomplete task bodies are present.
- Each code-changing step includes the code to add or replace.
- Each test step includes a command and expected result.

Type consistency:

- Backend request aliases use camelCase input names and snake_case Python attributes.
- Frontend request types use camelCase keys matching API aliases.
- Combined setup response uses camelCase envelope keys: `student`, `parentContact`, `parentLink`, and `tripPassenger`.
