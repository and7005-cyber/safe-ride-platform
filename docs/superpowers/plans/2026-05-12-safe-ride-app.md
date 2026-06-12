# SafeRide Kenya Beta MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 SafeRide Kenya beta MVP for Nairobi private schools: admin dashboard, driver mobile web interface, parent PWA, trip history, tenant isolation, SMS plus push notifications, and offline-tolerant driver taps.

**Architecture:** Use one TypeScript web app with role-specific routes backed by Supabase Postgres/Auth and Supabase Edge Functions. The database is the source of truth, with row-level security enforcing school isolation, and trip events driving admin status, parent progress, history, and notifications.

**Tech Stack:** Vite, React, TypeScript, React Router, TanStack Query, Supabase Postgres/Auth/RLS/Edge Functions, Vitest, Playwright, IndexedDB, Web Push, Africa's Talking SMS API.

---

## Source Context

Primary PRD: `/Users/andreanatali/Google Drive/My Documents/New Initiatives/Kenya Inc/Safe Ride/SafeRide-PRD-v1.2-decision-updates.docx`

Phase 1 decisions to preserve:
- Student-as-stop routing: each trip has an ordered passenger list; parent views may show only their own child by name.
- Trip is the operational unit: a bus may run multiple named trips per day.
- Driver PINs belong to drivers, not buses, and remain stable through reassignment.
- Daily attendance is recorded per student and applied to assigned trips for the selected date.
- Parent links are permanent 32+ character tokens and revokable by admin.
- Parent notifications use SMS as fallback plus push where available; WhatsApp is out of Phase 1.
- Phase 1 UI language is English only; Swahili is Phase 2.
- GPS, maps, route optimisation, billing, cross-school group admin, and parent messaging are out of Phase 1.

## Target File Structure

- Create: `package.json` - scripts, dependencies, and test commands.
- Create: `vite.config.ts` - Vite and Vitest configuration.
- Create: `tsconfig.json` - TypeScript strict mode config.
- Create: `src/main.tsx` - app bootstrap.
- Create: `src/app/App.tsx` - router and role layouts.
- Create: `src/app/routes.tsx` - route definitions.
- Create: `src/lib/supabase.ts` - browser Supabase client.
- Create: `src/lib/date.ts` - school-day helpers using Africa/Nairobi dates.
- Create: `src/lib/phone.ts` - +254 phone normalization.
- Create: `src/lib/eta.ts` - ETA and sequence calculations.
- Create: `src/lib/privacy.ts` - parent-safe progress projection.
- Create: `src/lib/offlineQueue.ts` - IndexedDB queue for driver events.
- Create: `src/features/admin/AdminDashboard.tsx` - live fleet overview.
- Create: `src/features/admin/SchoolSetup.tsx` - school, buses, drivers, students, trips setup.
- Create: `src/features/admin/DailyAttendance.tsx` - per-student daily attendance management.
- Create: `src/features/admin/RunHistory.tsx` - completed trip history and correction UI.
- Create: `src/features/driver/DriverLogin.tsx` - PIN login.
- Create: `src/features/driver/DriverTripSelect.tsx` - trips assigned to driver for today.
- Create: `src/features/driver/DriverTrip.tsx` - start trip, passenger taps, report issue, end trip.
- Create: `src/features/parent/ParentTrip.tsx` - token-based parent progress view.
- Create: `src/features/shared/TripProgress.tsx` - reusable progress rendering.
- Create: `src/services/adminApi.ts` - admin data mutations.
- Create: `src/services/driverApi.ts` - driver trip event mutations.
- Create: `src/services/parentApi.ts` - token-scoped parent queries.
- Create: `src/services/notificationCopy.ts` - 10 parent notification templates plus admin notifications.
- Create: `supabase/migrations/0001_initial_schema.sql` - schema, indexes, enums, and RLS.
- Create: `supabase/migrations/0002_functions_and_triggers.sql` - notification outbox and audit helpers.
- Create: `supabase/functions/send-notifications/index.ts` - Africa's Talking SMS plus Web Push dispatch.
- Create: `supabase/functions/register-push/index.ts` - parent PWA push subscription registration.
- Create: `tests/unit/eta.test.ts` - ETA calculation tests.
- Create: `tests/unit/privacy.test.ts` - parent anonymisation tests.
- Create: `tests/unit/phone.test.ts` - Kenyan phone normalization tests.
- Create: `tests/unit/notificationCopy.test.ts` - event copy coverage tests.
- Create: `tests/e2e/admin-driver-parent.spec.ts` - complete beta trip flow.

## Milestone 1: Project Skeleton

### Task 1: Create the Vite React App Shell

**Files:**
- Create: `package.json`
- Create: `vite.config.ts`
- Create: `tsconfig.json`
- Create: `src/main.tsx`
- Create: `src/lib/supabase.ts`
- Create: `src/app/App.tsx`
- Create: `src/app/routes.tsx`

- [ ] **Step 1: Add scripts and dependencies**

```json
{
  "name": "saferide-kenya",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test",
    "lint": "tsc --noEmit"
  },
  "dependencies": {
    "@supabase/supabase-js": "^2.45.0",
    "@tanstack/react-query": "^5.51.0",
    "idb": "^8.0.0",
    "lucide-react": "^0.468.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.46.0",
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: Configure strict TypeScript and tests**

```ts
// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["@testing-library/jest-dom/vitest"]
  }
});
```

- [ ] **Step 3: Add Supabase client**

```ts
// src/lib/supabase.ts
import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error("Missing Supabase environment variables.");
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
```

- [ ] **Step 4: Add app router shell**

```tsx
// src/app/App.tsx
import { RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { router } from "./routes";

const queryClient = new QueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
```

```tsx
// src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./app/App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 5: Add route skeleton**

```tsx
// src/app/routes.tsx
import { createBrowserRouter } from "react-router-dom";
import { AdminDashboard } from "../features/admin/AdminDashboard";
import { SchoolSetup } from "../features/admin/SchoolSetup";
import { DailyAttendance } from "../features/admin/DailyAttendance";
import { RunHistory } from "../features/admin/RunHistory";
import { DriverLogin } from "../features/driver/DriverLogin";
import { DriverTripSelect } from "../features/driver/DriverTripSelect";
import { DriverTrip } from "../features/driver/DriverTrip";
import { ParentTrip } from "../features/parent/ParentTrip";

export const router = createBrowserRouter([
  { path: "/", element: <AdminDashboard /> },
  { path: "/admin/setup", element: <SchoolSetup /> },
  { path: "/admin/attendance", element: <DailyAttendance /> },
  { path: "/admin/history", element: <RunHistory /> },
  { path: "/driver", element: <DriverLogin /> },
  { path: "/driver/trips", element: <DriverTripSelect /> },
  { path: "/driver/trips/:tripId", element: <DriverTrip /> },
  { path: "/p/:token", element: <ParentTrip /> }
]);
```

- [ ] **Step 6: Verify shell**

Run: `npm install && npm run build`

Expected: build completes with no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add package.json vite.config.ts tsconfig.json src
git commit -m "chore: create saferide app shell"
```

## Milestone 2: Database and Tenant Isolation

### Task 2: Create Supabase Schema with RLS

**Files:**
- Create: `supabase/migrations/0001_initial_schema.sql`

- [ ] **Step 1: Create enums, tenant tables, and core entities**

```sql
create extension if not exists pgcrypto;

create type trip_session as enum ('morning', 'afternoon', 'adhoc', 'staff');
create type trip_status as enum ('scheduled', 'active', 'delayed', 'issue_reported', 'completed', 'cancelled');
create type passenger_type as enum ('student', 'staff');
create type trip_passenger_status as enum ('pending', 'boarded', 'dropped', 'absent_admin', 'absent_driver', 'alternative_transport');
create type attendance_status as enum ('riding', 'absent', 'alternative_transport');
create type event_type as enum ('trip_started', 'passenger_boarded', 'passenger_not_present', 'passenger_dropped', 'trip_ended', 'issue_reported', 'missed_tap', 'admin_correction');
create type notification_status as enum ('pending', 'sent', 'failed', 'skipped');

create table schools (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  approaching_threshold integer not null default 2,
  default_inter_student_minutes integer not null default 6,
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
```

- [ ] **Step 2: Create passenger, trip, attendance, token, event, and notification tables**

```sql
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
  contact_1_phone text not null check (contact_1_phone ~ '^\\+254[0-9]{9}$'),
  contact_1_relationship text not null,
  contact_2_name text,
  contact_2_phone text check (contact_2_phone is null or contact_2_phone ~ '^\\+254[0-9]{9}$'),
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
  attempts integer not null default 0,
  last_error text,
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
```

- [ ] **Step 3: Add RLS helper and policies**

```sql
create or replace function current_school_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select school_id from admin_profiles where id = auth.uid()
$$;

alter table schools enable row level security;
alter table admin_profiles enable row level security;
alter table buses enable row level security;
alter table drivers enable row level security;
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

create policy "admins see own school" on schools
for select using (id = current_school_id());

create policy "admins manage own profiles" on admin_profiles
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school buses" on buses
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school drivers" on drivers
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school students" on students
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school parent contacts" on parent_contacts
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school staff" on staff_passengers
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school trips" on trips
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school trip passengers" on trip_passengers
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school attendance" on daily_attendance
for all using (school_id = current_school_id())
with check (school_id = current_school_id());

create policy "admins manage own school parent links" on parent_links
for all using (school_id = current_school_id())
with check (school_id = current_school_id());
```

- [ ] **Step 4: Verify tenant isolation**

Run: `supabase db reset`

Expected: migrations complete with all RLS policies enabled. In the Supabase SQL editor, selecting from `trips` as an admin from School A returns no rows from School B.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0001_initial_schema.sql
git commit -m "feat: add saferide tenant-isolated schema"
```

## Milestone 3: Shared Business Logic

### Task 3: Build ETA, Privacy, Date, and Phone Helpers

**Files:**
- Create: `src/lib/date.ts`
- Create: `src/lib/phone.ts`
- Create: `src/lib/eta.ts`
- Create: `src/lib/privacy.ts`
- Create: `tests/unit/phone.test.ts`
- Create: `tests/unit/eta.test.ts`
- Create: `tests/unit/privacy.test.ts`

- [ ] **Step 1: Write phone normalization tests**

```ts
// tests/unit/phone.test.ts
import { describe, expect, it } from "vitest";
import { normalizeKenyanPhone } from "../../src/lib/phone";

describe("normalizeKenyanPhone", () => {
  it("normalizes local Safaricom-style numbers to +254 format", () => {
    expect(normalizeKenyanPhone("0712 345 678")).toBe("+254712345678");
  });

  it("keeps valid +254 numbers", () => {
    expect(normalizeKenyanPhone("+254712345678")).toBe("+254712345678");
  });

  it("rejects numbers outside the Kenyan mobile format", () => {
    expect(() => normalizeKenyanPhone("+255712345678")).toThrow("Use a Kenyan phone number in +254 format.");
  });
});
```

- [ ] **Step 2: Implement phone normalization**

```ts
// src/lib/phone.ts
export function normalizeKenyanPhone(input: string): string {
  const digits = input.replace(/[^\d+]/g, "");
  const normalized = digits.startsWith("0")
    ? `+254${digits.slice(1)}`
    : digits.startsWith("254")
      ? `+${digits}`
      : digits;

  if (!/^\+254\d{9}$/.test(normalized)) {
    throw new Error("Use a Kenyan phone number in +254 format.");
  }

  return normalized;
}
```

- [ ] **Step 3: Write ETA tests**

```ts
// src/lib/date.ts
export function todayInNairobi(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Africa/Nairobi",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(now);
}
```

```ts
// tests/unit/eta.test.ts
import { describe, expect, it } from "vitest";
import { estimateMinutesToPassenger } from "../../src/lib/eta";

describe("estimateMinutesToPassenger", () => {
  it("uses remaining sequence gaps from the last completed passenger", () => {
    const passengers = [
      { id: "a", sequencePosition: 1, estimatedMinutesFromStart: 0, status: "boarded" },
      { id: "b", sequencePosition: 2, estimatedMinutesFromStart: 6, status: "pending" },
      { id: "c", sequencePosition: 3, estimatedMinutesFromStart: 12, status: "pending" }
    ];

    expect(estimateMinutesToPassenger(passengers, "c")).toBe(12);
  });

  it("returns 0 when the target passenger is already completed", () => {
    const passengers = [
      { id: "a", sequencePosition: 1, estimatedMinutesFromStart: 0, status: "boarded" }
    ];

    expect(estimateMinutesToPassenger(passengers, "a")).toBe(0);
  });
});
```

- [ ] **Step 4: Implement ETA helper**

```ts
// src/lib/eta.ts
export type PassengerProgressStatus =
  | "pending"
  | "boarded"
  | "dropped"
  | "absent_admin"
  | "absent_driver"
  | "alternative_transport";

export type PassengerProgress = {
  id: string;
  sequencePosition: number;
  estimatedMinutesFromStart: number;
  status: PassengerProgressStatus;
};

const completeStatuses = new Set<PassengerProgressStatus>([
  "boarded",
  "dropped",
  "absent_admin",
  "absent_driver",
  "alternative_transport"
]);

export function estimateMinutesToPassenger(passengers: PassengerProgress[], targetId: string): number {
  const ordered = [...passengers].sort((a, b) => a.sequencePosition - b.sequencePosition);
  const target = ordered.find((passenger) => passenger.id === targetId);
  if (!target || completeStatuses.has(target.status)) return 0;

  const completedBeforeTarget = ordered
    .filter((passenger) => passenger.sequencePosition < target.sequencePosition && completeStatuses.has(passenger.status))
    .at(-1);

  return Math.max(0, target.estimatedMinutesFromStart - (completedBeforeTarget?.estimatedMinutesFromStart ?? 0));
}
```

- [ ] **Step 5: Write parent privacy tests**

```ts
// tests/unit/privacy.test.ts
import { describe, expect, it } from "vitest";
import { toParentSafeProgress } from "../../src/lib/privacy";

describe("toParentSafeProgress", () => {
  it("shows only the parent's child by name", () => {
    const result = toParentSafeProgress([
      { id: "a", studentId: "child-a", studentName: "Amina", locationLabel: "Kilimani", sequencePosition: 1, status: "boarded" },
      { id: "b", studentId: "child-b", studentName: "Brian", locationLabel: "Lavington", sequencePosition: 2, status: "pending" }
    ], "child-b");

    expect(result[0].label).toBe("Kilimani");
    expect(result[1].label).toBe("Brian");
  });
});
```

- [ ] **Step 6: Implement parent-safe projection**

```ts
// src/lib/privacy.ts
export type RawTripPassenger = {
  id: string;
  studentId: string | null;
  studentName: string | null;
  locationLabel: string;
  sequencePosition: number;
  status: string;
};

export type ParentSafePassenger = {
  id: string;
  label: string;
  sequencePosition: number;
  status: string;
  isOwnChild: boolean;
};

export function toParentSafeProgress(passengers: RawTripPassenger[], ownStudentId: string): ParentSafePassenger[] {
  return passengers
    .sort((a, b) => a.sequencePosition - b.sequencePosition)
    .map((passenger) => ({
      id: passenger.id,
      label: passenger.studentId === ownStudentId ? passenger.studentName ?? "Your child" : passenger.locationLabel,
      sequencePosition: passenger.sequencePosition,
      status: passenger.status,
      isOwnChild: passenger.studentId === ownStudentId
    }));
}
```

- [ ] **Step 7: Verify helpers**

Run: `npm test -- tests/unit/phone.test.ts tests/unit/eta.test.ts tests/unit/privacy.test.ts`

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/lib tests/unit
git commit -m "feat: add saferide shared business rules"
```

## Milestone 4: Admin Setup and Attendance

### Task 4: Build School Setup Screens

**Files:**
- Create: `src/services/adminApi.ts`
- Create: `src/features/admin/SchoolSetup.tsx`

- [ ] **Step 1: Add admin API functions**

```ts
// src/services/adminApi.ts
import { supabase } from "../lib/supabase";

export async function createBus(input: { schoolId: string; label: string; registrationNumber?: string }) {
  const { data, error } = await supabase
    .from("buses")
    .insert({
      school_id: input.schoolId,
      label: input.label,
      registration_number: input.registrationNumber ?? null
    })
    .select()
    .single();

  if (error) throw error;
  return data;
}

export async function createStudent(input: {
  schoolId: string;
  fullName: string;
  homeAddress: string;
  homeLocationNote?: string;
}) {
  const { data, error } = await supabase
    .from("students")
    .insert({
      school_id: input.schoolId,
      full_name: input.fullName,
      home_address: input.homeAddress,
      home_location_note: input.homeLocationNote ?? null
    })
    .select()
    .single();

  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Implement setup page with tabs**

```tsx
// src/features/admin/SchoolSetup.tsx
import { useState } from "react";

type SetupTab = "buses" | "drivers" | "students" | "trips" | "parents";

const tabs: SetupTab[] = ["buses", "drivers", "students", "trips", "parents"];

export function SchoolSetup() {
  const [activeTab, setActiveTab] = useState<SetupTab>("buses");

  return (
    <main className="admin-page">
      <header>
        <h1>School Setup</h1>
        <p>Configure the buses, drivers, students, trips, and parent contacts for this school.</p>
      </header>

      <nav aria-label="Setup sections">
        {tabs.map((tab) => (
          <button key={tab} type="button" aria-pressed={activeTab === tab} onClick={() => setActiveTab(tab)}>
            {tab[0].toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </nav>

      <section aria-label={`${activeTab} setup`}>
        {activeTab === "buses" && <p>Add each school bus or van used during daily transport.</p>}
        {activeTab === "drivers" && <p>Create each driver and assign their school-issued PIN.</p>}
        {activeTab === "students" && <p>Add students with home address or location notes.</p>}
        {activeTab === "trips" && <p>Create morning, afternoon, staff, and ad hoc trips with ordered passengers.</p>}
        {activeTab === "parents" && <p>Add up to two parent contacts per student using +254 phone numbers.</p>}
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Verify setup route**

Run: `npm run build`

Expected: TypeScript build succeeds and `/admin/setup` renders all five setup sections.

- [ ] **Step 4: Commit**

```bash
git add src/services/adminApi.ts src/features/admin/SchoolSetup.tsx
git commit -m "feat: add admin setup foundation"
```

### Task 5: Build Daily Attendance Management

**Files:**
- Modify: `src/services/adminApi.ts`
- Create: `src/features/admin/DailyAttendance.tsx`

- [ ] **Step 1: Add attendance mutation**

```ts
// Add to src/services/adminApi.ts
export async function markDailyAttendance(input: {
  schoolId: string;
  studentId: string;
  attendanceDate: string;
  status: "riding" | "absent" | "alternative_transport";
  note?: string;
}) {
  const { data, error } = await supabase
    .from("daily_attendance")
    .upsert({
      school_id: input.schoolId,
      student_id: input.studentId,
      attendance_date: input.attendanceDate,
      status: input.status,
      note: input.note ?? null
    }, { onConflict: "student_id,attendance_date" })
    .select()
    .single();

  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Implement attendance page**

```tsx
// src/features/admin/DailyAttendance.tsx
import { useState } from "react";

type AttendanceStatus = "riding" | "absent" | "alternative_transport";

const options: { value: AttendanceStatus; label: string }[] = [
  { value: "riding", label: "Riding" },
  { value: "absent", label: "Not riding today" },
  { value: "alternative_transport", label: "Alternative transport" }
];

export function DailyAttendance() {
  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10));

  return (
    <main className="admin-page">
      <header>
        <h1>Daily Attendance</h1>
        <input
          aria-label="Attendance date"
          type="date"
          value={selectedDate}
          onChange={(event) => setSelectedDate(event.target.value)}
        />
      </header>

      <section aria-label="Student attendance list">
        <p>Students marked absent or alternative transport are removed from driver pickup lists for the selected date.</p>
        <div role="group" aria-label="Attendance statuses">
          {options.map((option) => (
            <button key={option.value} type="button">
              {option.label}
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Add database trigger to apply admin absence to trip passengers**

```sql
-- Add to supabase/migrations/0002_functions_and_triggers.sql
create or replace function apply_daily_attendance_to_trip_passengers()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update trip_passengers tp
  set status = case
    when new.status = 'absent' then 'absent_admin'::trip_passenger_status
    when new.status = 'alternative_transport' then 'alternative_transport'::trip_passenger_status
    else 'pending'::trip_passenger_status
  end
  from trips t
  where tp.trip_id = t.id
    and tp.student_id = new.student_id
    and t.service_date = new.attendance_date
    and t.status in ('scheduled', 'active')
    and tp.school_id = new.school_id;

  return new;
end;
$$;

create trigger daily_attendance_apply_to_trips
after insert or update on daily_attendance
for each row execute function apply_daily_attendance_to_trip_passengers();
```

- [ ] **Step 4: Verify attendance behavior**

Run: `supabase db reset && npm run build`

Expected: setting a student to `absent` updates that student's active or scheduled trip passenger status to `absent_admin`.

- [ ] **Step 5: Commit**

```bash
git add src/services/adminApi.ts src/features/admin/DailyAttendance.tsx supabase/migrations/0002_functions_and_triggers.sql
git commit -m "feat: add daily attendance management"
```

## Milestone 5: Driver Flow

### Task 6: Build Driver PIN Login and Trip Selection

**Files:**
- Create: `src/services/driverApi.ts`
- Create: `src/features/driver/DriverLogin.tsx`
- Create: `src/features/driver/DriverTripSelect.tsx`

- [ ] **Step 1: Add driver API contract**

```ts
// src/services/driverApi.ts
import { supabase } from "../lib/supabase";

export async function getDriverTripsForToday(driverId: string, serviceDate: string) {
  const { data, error } = await supabase
    .from("trips")
    .select("id, name, scheduled_start, session, status, buses(label)")
    .eq("driver_id", driverId)
    .eq("service_date", serviceDate)
    .order("scheduled_start");

  if (error) throw error;
  return data;
}

export async function startTrip(tripId: string) {
  const { data, error } = await supabase
    .from("trips")
    .update({ status: "active", started_at: new Date().toISOString() })
    .eq("id", tripId)
    .select()
    .single();

  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Implement PIN login screen**

```tsx
// src/features/driver/DriverLogin.tsx
import { useState } from "react";

export function DriverLogin() {
  const [pin, setPin] = useState("");

  return (
    <main className="driver-page">
      <form aria-label="Driver PIN login">
        <h1>Driver Login</h1>
        <label htmlFor="driver-pin">Enter your school PIN</label>
        <input
          id="driver-pin"
          inputMode="numeric"
          autoComplete="one-time-code"
          minLength={4}
          maxLength={6}
          value={pin}
          onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 6))}
        />
        <button type="submit" disabled={pin.length < 4}>Continue</button>
      </form>
    </main>
  );
}
```

- [ ] **Step 3: Implement trip selection screen**

```tsx
// src/features/driver/DriverTripSelect.tsx
export function DriverTripSelect() {
  return (
    <main className="driver-page">
      <h1>Select Trip</h1>
      <p>Choose the trip you are starting now.</p>
      <section aria-label="Assigned trips for today">
        <button type="button">Morning Trip 1 - 6:00am</button>
        <button type="button">Morning Trip 2 - 7:30am</button>
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Verify mobile ergonomics**

Run: `npm run build`

Expected: build succeeds; PIN input accepts only 4-6 digits; trip selection uses large tap targets.

- [ ] **Step 5: Commit**

```bash
git add src/services/driverApi.ts src/features/driver
git commit -m "feat: add driver login and trip selection"
```

### Task 7: Build Driver Trip Event Flow with Offline Queue

**Files:**
- Create: `src/lib/offlineQueue.ts`
- Modify: `src/services/driverApi.ts`
- Create: `src/features/driver/DriverTrip.tsx`

- [ ] **Step 1: Add IndexedDB event queue**

```ts
// src/lib/offlineQueue.ts
import { openDB } from "idb";

export type QueuedDriverEvent = {
  id: string;
  tripId: string;
  tripPassengerId?: string;
  eventType: "trip_started" | "passenger_boarded" | "passenger_not_present" | "passenger_dropped" | "trip_ended" | "issue_reported";
  occurredAt: string;
  metadata: Record<string, unknown>;
};

const dbPromise = openDB("saferide-driver", 1, {
  upgrade(db) {
    db.createObjectStore("driver-events", { keyPath: "id" });
  }
});

export async function queueDriverEvent(event: QueuedDriverEvent) {
  const db = await dbPromise;
  await db.put("driver-events", event);
}

export async function listQueuedDriverEvents() {
  const db = await dbPromise;
  return db.getAll("driver-events") as Promise<QueuedDriverEvent[]>;
}

export async function removeQueuedDriverEvent(id: string) {
  const db = await dbPromise;
  await db.delete("driver-events", id);
}
```

- [ ] **Step 2: Add trip event mutation**

```ts
// Add to src/services/driverApi.ts
export async function recordDriverEvent(input: {
  schoolId: string;
  tripId: string;
  tripPassengerId?: string;
  eventType: string;
  metadata?: Record<string, unknown>;
}) {
  const { data, error } = await supabase
    .from("trip_events")
    .insert({
      school_id: input.schoolId,
      trip_id: input.tripId,
      trip_passenger_id: input.tripPassengerId ?? null,
      event_type: input.eventType,
      created_by_role: "driver",
      metadata: input.metadata ?? {}
    })
    .select()
    .single();

  if (error) throw error;
  return data;
}
```

- [ ] **Step 3: Implement driver trip screen**

```tsx
// src/features/driver/DriverTrip.tsx
export function DriverTrip() {
  return (
    <main className="driver-page">
      <header>
        <h1>Morning Trip 1</h1>
        <button type="button">Report Issue</button>
      </header>

      <button type="button">Start Trip</button>

      <section aria-label="Passenger list">
        <button type="button">Amina Mwangi</button>
        <button type="button">Brian Otieno</button>
        <button type="button">Cynthia Wanjiku</button>
      </section>

      <button type="button">End Trip</button>
    </main>
  );
}
```

- [ ] **Step 4: Add event trigger to update trip passenger status**

```sql
-- Add to supabase/migrations/0002_functions_and_triggers.sql
create or replace function apply_trip_event()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.event_type = 'passenger_boarded' then
    update trip_passengers
    set status = 'boarded', actual_pickup_time = new.occurred_at
    where id = new.trip_passenger_id and school_id = new.school_id;
  elsif new.event_type = 'passenger_dropped' then
    update trip_passengers
    set status = 'dropped', actual_dropoff_time = new.occurred_at
    where id = new.trip_passenger_id and school_id = new.school_id;
  elsif new.event_type = 'passenger_not_present' then
    update trip_passengers
    set status = 'absent_driver'
    where id = new.trip_passenger_id and school_id = new.school_id;
  elsif new.event_type = 'trip_started' then
    update trips
    set status = 'active', started_at = new.occurred_at
    where id = new.trip_id and school_id = new.school_id;
  elsif new.event_type = 'trip_ended' then
    update trips
    set status = 'completed', ended_at = new.occurred_at
    where id = new.trip_id and school_id = new.school_id;
  elsif new.event_type = 'issue_reported' then
    update trips
    set status = 'issue_reported'
    where id = new.trip_id and school_id = new.school_id;
  end if;

  return new;
end;
$$;

create trigger trip_event_apply
after insert on trip_events
for each row execute function apply_trip_event();
```

- [ ] **Step 5: Verify offline and event behavior**

Run: `npm run build && supabase db reset`

Expected: driver event records update trip and trip passenger status; queued events remain in IndexedDB when network calls fail.

- [ ] **Step 6: Commit**

```bash
git add src/lib/offlineQueue.ts src/services/driverApi.ts src/features/driver/DriverTrip.tsx supabase/migrations/0002_functions_and_triggers.sql
git commit -m "feat: add driver trip event flow"
```

## Milestone 6: Notifications and Parent PWA

### Task 8: Define Notification Copy and Outbox Triggers

**Files:**
- Create: `src/services/notificationCopy.ts`
- Create: `tests/unit/notificationCopy.test.ts`
- Modify: `supabase/migrations/0002_functions_and_triggers.sql`

- [ ] **Step 1: Write copy coverage test**

```ts
// tests/unit/notificationCopy.test.ts
import { describe, expect, it } from "vitest";
import { parentNotificationTemplates } from "../../src/services/notificationCopy";

describe("parent notification templates", () => {
  it("contains the 10 PRD parent events", () => {
    expect(Object.keys(parentNotificationTemplates).sort()).toEqual([
      "child_arrived_at_school",
      "child_confirmed_on_van",
      "child_dropped_off_home",
      "child_not_boarded",
      "issue_reported",
      "trip_delayed",
      "trip_started_afternoon",
      "trip_started_morning",
      "van_approaching_afternoon",
      "van_approaching_morning"
    ]);
  });
});
```

- [ ] **Step 2: Add concise SMS and push templates**

```ts
// src/services/notificationCopy.ts
export const parentNotificationTemplates = {
  trip_started_morning: {
    sms: "SafeRide: Your child's morning trip has started. Estimated pickup: {{eta}}.",
    push: "Morning trip started. Estimated pickup: {{eta}}."
  },
  van_approaching_morning: {
    sms: "SafeRide: The van is approaching your child. Please have them ready.",
    push: "The van is approaching. Please be ready."
  },
  child_confirmed_on_van: {
    sms: "SafeRide: Your child has been confirmed on the van.",
    push: "Your child is on the van."
  },
  child_arrived_at_school: {
    sms: "SafeRide: The van has arrived at school.",
    push: "The van has arrived at school."
  },
  trip_started_afternoon: {
    sms: "SafeRide: Your child's afternoon trip has started. Estimated drop-off: {{eta}}.",
    push: "Afternoon trip started. Estimated drop-off: {{eta}}."
  },
  van_approaching_afternoon: {
    sms: "SafeRide: The van is approaching your child's drop-off. Please be ready.",
    push: "The van is approaching your drop-off."
  },
  child_dropped_off_home: {
    sms: "SafeRide: Your child has been confirmed dropped off at home.",
    push: "Your child has been dropped off."
  },
  child_not_boarded: {
    sms: "SafeRide: Your child was expected but was not confirmed by the driver. Please contact the school.",
    push: "Your child was not confirmed by the driver."
  },
  trip_delayed: {
    sms: "SafeRide: The van is running behind schedule. Open your SafeRide link for the updated ETA.",
    push: "The van is running behind schedule."
  },
  issue_reported: {
    sms: "SafeRide: The van has reported a delay or issue. The school has been notified.",
    push: "The van has reported a delay or issue."
  }
} as const;
```

- [ ] **Step 3: Create notification outbox rows from trip events**

```sql
-- Add to supabase/migrations/0002_functions_and_triggers.sql
create or replace function enqueue_parent_notifications()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  contact parent_contacts%rowtype;
  template text;
begin
  if new.trip_passenger_id is null then
    return new;
  end if;

  select pc.* into contact
  from parent_contacts pc
  join trip_passengers tp on tp.student_id = pc.student_id
  where tp.id = new.trip_passenger_id
    and tp.passenger_type = 'student'
    and pc.school_id = new.school_id;

  if contact.id is null then
    return new;
  end if;

  template := case new.event_type
    when 'passenger_boarded' then 'child_confirmed_on_van'
    when 'passenger_dropped' then 'child_dropped_off_home'
    when 'passenger_not_present' then 'child_not_boarded'
    else null
  end;

  if template is null then
    return new;
  end if;

  insert into notification_outbox (school_id, trip_event_id, recipient_kind, recipient_phone, channel, template_key, payload)
  values
    (new.school_id, new.id, 'parent', contact.contact_1_phone, 'sms', template, new.metadata),
    (new.school_id, new.id, 'parent', contact.contact_2_phone, 'sms', template, new.metadata)
  on conflict do nothing;

  delete from notification_outbox where recipient_phone is null;
  return new;
end;
$$;

create trigger trip_event_enqueue_parent_notifications
after insert on trip_events
for each row execute function enqueue_parent_notifications();
```

- [ ] **Step 4: Verify notification coverage**

Run: `npm test -- tests/unit/notificationCopy.test.ts && supabase db reset`

Expected: all 10 parent templates exist; passenger event inserts create SMS outbox rows for one or two parent phone numbers.

- [ ] **Step 5: Commit**

```bash
git add src/services/notificationCopy.ts tests/unit/notificationCopy.test.ts supabase/migrations/0002_functions_and_triggers.sql
git commit -m "feat: add saferide notification model"
```

### Task 9: Build Notification Dispatch Edge Functions

**Files:**
- Create: `supabase/functions/send-notifications/index.ts`
- Create: `supabase/functions/register-push/index.ts`

- [ ] **Step 1: Implement SMS and push dispatcher**

```ts
// supabase/functions/send-notifications/index.ts
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

Deno.serve(async () => {
  const { data: messages, error } = await supabase
    .from("notification_outbox")
    .select("*")
    .eq("status", "pending")
    .lt("attempts", 3)
    .limit(50);

  if (error) {
    return Response.json({ error: error.message }, { status: 500 });
  }

  for (const message of messages ?? []) {
    try {
      if (message.channel === "sms" && message.recipient_phone) {
        await fetch("https://api.africastalking.com/version1/messaging", {
          method: "POST",
          headers: {
            apiKey: Deno.env.get("AFRICAS_TALKING_API_KEY")!,
            "Content-Type": "application/x-www-form-urlencoded"
          },
          body: new URLSearchParams({
            username: Deno.env.get("AFRICAS_TALKING_USERNAME")!,
            to: message.recipient_phone,
            message: String(message.payload?.body ?? "SafeRide update from your school.")
          })
        });
      }

      await supabase
        .from("notification_outbox")
        .update({ status: "sent", sent_at: new Date().toISOString(), attempts: message.attempts + 1 })
        .eq("id", message.id);
    } catch (sendError) {
      await supabase
        .from("notification_outbox")
        .update({ status: "failed", attempts: message.attempts + 1, last_error: String(sendError) })
        .eq("id", message.id);
    }
  }

  return Response.json({ processed: messages?.length ?? 0 });
});
```

- [ ] **Step 2: Implement push subscription registration**

```ts
// supabase/functions/register-push/index.ts
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

Deno.serve(async (request) => {
  const body = await request.json();
  const { data: link, error: linkError } = await supabase
    .from("parent_links")
    .select("id, school_id")
    .eq("token", body.token)
    .is("revoked_at", null)
    .single();

  if (linkError || !link) {
    return Response.json({ error: "Invalid or revoked parent link" }, { status: 403 });
  }

  const { error } = await supabase.from("push_subscriptions").upsert({
    school_id: link.school_id,
    parent_link_id: link.id,
    endpoint: body.subscription.endpoint,
    p256dh: body.subscription.keys.p256dh,
    auth: body.subscription.keys.auth
  }, { onConflict: "parent_link_id,endpoint" });

  if (error) return Response.json({ error: error.message }, { status: 500 });
  return Response.json({ ok: true });
});
```

- [ ] **Step 3: Verify Edge Functions**

Run: `supabase functions serve send-notifications` and POST to the local function with one pending `notification_outbox` row.

Expected: the row changes from `pending` to `sent` for valid credentials or to `failed` with `last_error` populated for invalid credentials.

- [ ] **Step 4: Commit**

```bash
git add supabase/functions/send-notifications/index.ts supabase/functions/register-push/index.ts
git commit -m "feat: add notification edge functions"
```

### Task 10: Build Token-Based Parent Progress View

**Files:**
- Create: `src/services/parentApi.ts`
- Create: `src/features/shared/TripProgress.tsx`
- Create: `src/features/parent/ParentTrip.tsx`

- [ ] **Step 1: Add parent API**

```ts
// src/services/parentApi.ts
import { supabase } from "../lib/supabase";

export async function getParentTripByToken(token: string) {
  const { data, error } = await supabase.rpc("get_parent_trip_progress", { link_token: token });
  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Add secure RPC for parent progress**

```sql
-- Add to supabase/migrations/0002_functions_and_triggers.sql
create or replace function get_parent_trip_progress(link_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  link parent_links%rowtype;
  active_trip trips%rowtype;
  passengers jsonb;
begin
  select * into link
  from parent_links
  where token = link_token and revoked_at is null;

  if link.id is null then
    raise exception 'Parent link is invalid or revoked';
  end if;

  select t.* into active_trip
  from trips t
  join trip_passengers tp on tp.trip_id = t.id
  where tp.student_id = link.student_id
    and t.school_id = link.school_id
    and t.status in ('scheduled', 'active', 'delayed', 'issue_reported')
  order by t.service_date desc, t.scheduled_start desc
  limit 1;

  select jsonb_agg(jsonb_build_object(
    'id', tp.id,
    'studentId', tp.student_id,
    'studentName', case when tp.student_id = link.student_id then s.full_name else null end,
    'locationLabel', coalesce(s.home_location_note, s.home_address, 'Stop ' || tp.sequence_position),
    'sequencePosition', tp.sequence_position,
    'estimatedMinutesFromStart', tp.estimated_minutes_from_start,
    'status', tp.status
  ) order by tp.sequence_position) into passengers
  from trip_passengers tp
  left join students s on s.id = tp.student_id
  where tp.trip_id = active_trip.id
    and tp.school_id = link.school_id;

  return jsonb_build_object(
    'ownStudentId', link.student_id,
    'trip', row_to_json(active_trip),
    'passengers', coalesce(passengers, '[]'::jsonb)
  );
end;
$$;
```

- [ ] **Step 3: Implement reusable progress component**

```tsx
// src/features/shared/TripProgress.tsx
type TripProgressItem = {
  id: string;
  label: string;
  sequencePosition: number;
  status: string;
  isOwnChild?: boolean;
};

export function TripProgress({ items }: { items: TripProgressItem[] }) {
  return (
    <ol className="trip-progress">
      {items.map((item) => (
        <li key={item.id} data-status={item.status} data-own-child={item.isOwnChild ? "true" : "false"}>
          <span>{item.sequencePosition}</span>
          <strong>{item.label}</strong>
          <small>{item.status.replaceAll("_", " ")}</small>
        </li>
      ))}
    </ol>
  );
}
```

- [ ] **Step 4: Implement parent page**

```tsx
// src/features/parent/ParentTrip.tsx
import { useParams } from "react-router-dom";
import { TripProgress } from "../shared/TripProgress";

export function ParentTrip() {
  const { token } = useParams();

  return (
    <main className="parent-page">
      <h1>SafeRide</h1>
      <p>Trip progress for your child.</p>
      <TripProgress items={[]} />
      <p>{token ? "Link active" : "Missing parent link"}</p>
    </main>
  );
}
```

- [ ] **Step 5: Verify parent confidentiality**

Run: `npm test -- tests/unit/privacy.test.ts && npm run build`

Expected: other students' names never appear in parent-safe data; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add src/services/parentApi.ts src/features/shared/TripProgress.tsx src/features/parent/ParentTrip.tsx supabase/migrations/0002_functions_and_triggers.sql
git commit -m "feat: add parent trip progress view"
```

## Milestone 7: Admin Live Operations and History

### Task 11: Build Live Fleet Overview

**Files:**
- Create: `src/features/admin/AdminDashboard.tsx`

- [ ] **Step 1: Implement dashboard layout**

```tsx
// src/features/admin/AdminDashboard.tsx
export function AdminDashboard() {
  return (
    <main className="admin-page">
      <header>
        <h1>Live Fleet</h1>
        <p>All active trips for this school.</p>
      </header>

      <section aria-label="Fleet status">
        <article>
          <h2>Morning Trip 1</h2>
          <p>Status: active</p>
          <p>Last tap: Amina Mwangi, 6:18am</p>
          <p>Current position: 1 of 20</p>
        </article>
      </section>

      <section aria-label="Alerts">
        <h2>Alerts</h2>
        <p>No current issues.</p>
      </section>
    </main>
  );
}
```

- [ ] **Step 2: Add polling interval for active trips**

```tsx
// Replace the static data in src/features/admin/AdminDashboard.tsx when activeTripSummary is available.
import { useQuery } from "@tanstack/react-query";

async function activeTripSummary() {
  return [];
}

const { data: activeTrips = [] } = useQuery({
  queryKey: ["active-trips"],
  queryFn: activeTripSummary,
  refetchInterval: 15000
});
```

- [ ] **Step 3: Verify dashboard performance**

Run: `npm run build`

Expected: dashboard builds and active trip polling is no slower than the PRD's 30-second update requirement.

- [ ] **Step 4: Commit**

```bash
git add src/features/admin/AdminDashboard.tsx
git commit -m "feat: add live fleet overview"
```

### Task 12: Build Run History and Admin Corrections

**Files:**
- Create: `src/features/admin/RunHistory.tsx`
- Modify: `src/services/adminApi.ts`

- [ ] **Step 1: Add correction mutation**

```ts
// Add to src/services/adminApi.ts
export async function correctTripPassengerStatus(input: {
  schoolId: string;
  tripPassengerId: string;
  originalValue: Record<string, unknown>;
  correctedStatus: "pending" | "boarded" | "dropped" | "absent_admin" | "absent_driver" | "alternative_transport";
  reason: string;
}) {
  const { data, error } = await supabase.rpc("correct_trip_passenger_status", {
    p_school_id: input.schoolId,
    p_trip_passenger_id: input.tripPassengerId,
    p_original_value: input.originalValue,
    p_corrected_status: input.correctedStatus,
    p_reason: input.reason
  });

  if (error) throw error;
  return data;
}
```

- [ ] **Step 2: Add correction RPC**

```sql
-- Add to supabase/migrations/0002_functions_and_triggers.sql
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
set search_path = public
as $$
declare
  audit_id uuid;
begin
  if p_school_id <> current_school_id() then
    raise exception 'Cannot correct records outside your school';
  end if;

  update trip_passengers
  set status = p_corrected_status
  where id = p_trip_passenger_id and school_id = p_school_id;

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
    p_original_value,
    jsonb_build_object('status', p_corrected_status),
    p_reason
  )
  returning id into audit_id;

  return audit_id;
end;
$$;
```

- [ ] **Step 3: Implement run history page**

```tsx
// src/features/admin/RunHistory.tsx
export function RunHistory() {
  return (
    <main className="admin-page">
      <header>
        <h1>Run History</h1>
        <input aria-label="Search by bus or trip" placeholder="Search by bus or trip" />
      </header>

      <section aria-label="Completed trips">
        <article>
          <h2>Morning Trip 1</h2>
          <p>Pickups: 18 confirmed, 1 absent, 1 alternative transport</p>
          <button type="button">Review records</button>
        </article>
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Verify audit trail**

Run: `supabase db reset && npm run build`

Expected: correction attempts outside the current admin's school fail; successful corrections insert an `audit_log` row with original value, corrected value, admin user, timestamp, and reason.

- [ ] **Step 5: Commit**

```bash
git add src/features/admin/RunHistory.tsx src/services/adminApi.ts supabase/migrations/0002_functions_and_triggers.sql
git commit -m "feat: add run history and corrections"
```

## Milestone 8: Beta Verification

### Task 13: Add End-to-End Beta Trip Test

**Files:**
- Create: `tests/e2e/admin-driver-parent.spec.ts`

- [ ] **Step 1: Write end-to-end test scenario**

```ts
// tests/e2e/admin-driver-parent.spec.ts
import { expect, test } from "@playwright/test";

test("admin creates a trip, driver records pickups, parent sees only own child", async ({ page }) => {
  await page.goto("/admin/setup");
  await expect(page.getByRole("heading", { name: "School Setup" })).toBeVisible();

  await page.goto("/driver");
  await page.getByLabel("Enter your school PIN").fill("1234");
  await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled();

  await page.goto("/p/example-parent-token-that-is-at-least-32-chars");
  await expect(page.getByRole("heading", { name: "SafeRide" })).toBeVisible();
  await expect(page.getByText("Trip progress for your child.")).toBeVisible();
});
```

- [ ] **Step 2: Verify full app**

Run: `npm run build && npm test && npm run e2e`

Expected: build, unit tests, and Playwright tests pass.

- [ ] **Step 3: Manual beta-readiness checklist**

Confirm in browser on desktop and Android-width viewport:
- Admin can create buses, drivers, students, parent contacts, and trips.
- Admin can mark a student absent or alternative transport for today.
- Driver can log in with a PIN, select a trip, start it, tap each passenger, report issue, and end trip.
- Driver interface remains usable with 48px minimum touch targets.
- Parent link page loads under 4 seconds on throttled 3G.
- Parent view shows the parent's child by name and no other student names.
- SMS outbox contains all triggered parent notifications for both parent phone numbers.
- Push registration works when a parent grants PWA notification permission.
- Run history includes timestamps, absences, issue reports, missed taps, and corrections.
- Admin cannot see another school's data when signed into a different school account.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/admin-driver-parent.spec.ts
git commit -m "test: cover saferide beta trip flow"
```

## Beta Launch Order

1. Week 1: schema, RLS, seed data, admin setup.
2. Week 2: driver PIN, trip selection, trip event flow, offline queue.
3. Week 3: parent token view, ETA, privacy projection, notification copy.
4. Week 4: SMS dispatch, push registration, admin alerts, delay and missed-tap detection.
5. Week 5: run history, corrections, audit logs, performance pass.
6. Week 6: beta school setup, driver walkthrough, laminated 5-step card, live trip rehearsal, launch.

## PRD Coverage Review

- Driver mobile web: covered by Tasks 6-7.
- Admin dashboard and setup: covered by Tasks 4-5 and 10-11.
- Parent mobile web/PWA: covered by Task 9.
- Student-as-stop routing: covered by `trip_passengers.sequence_position`.
- Multiple trips per bus per day: covered by `trips` keyed by bus, service date, and trip name.
- Staff trips: covered by `staff_passengers` and `passenger_type`.
- Daily attendance per student: covered by Task 5 and `daily_attendance`.
- Two parent contacts: covered by `parent_contacts` and outbox trigger.
- Ten parent notification events: covered by Task 8.
- SMS plus push: covered by notification outbox and Edge Function tasks.
- Parent confidentiality: covered by `get_parent_trip_progress` and `privacy.test.ts`.
- Permanent revokable parent links: covered by `parent_links`.
- Driver PIN reassignment model: covered by `drivers.pin_hash` and trips assigned by `driver_id`.
- Admin-only corrections with audit trail: covered by Task 11.
- School data isolation: covered by RLS in Task 2.
- Offline tolerance: covered by Task 7.
- Out-of-scope exclusions: GPS, maps, Swahili, billing, ERP integrations, route optimisation, public API, and school-group admin are not included in this plan.

## Implementation Status - 2026-05-12

- React/Vite app scaffold, Supabase schema, RPC functions, Edge Functions, unit test files, and an e2e beta-flow test file have been created.
- Driver flow was hardened after review: PIN login now creates a short-lived session token, driver RPCs validate token and trip assignment, passenger state transitions are enforced server-side, reloads of active trips recover trip state, and failed network taps queue locally for later sync.
- Parent link flow now calls `get_parent_trip_progress`, limits results to today's Nairobi trip, and hides other students' names and addresses.
- Admin beta workflows now write or read live data for active trips, bus creation, hashed-PIN driver creation, student creation, parent contacts, parent links, trip creation, trip passenger stop order, daily attendance, completed trips, and completed-trip corrections.
- Notification dispatch now claims work items, retries failed SMS sends, skips unsupported channels, includes rendered SMS body text, and supports CORS preflight.
- Verification is partially blocked in this workspace because `npm`, `deno`, and the Supabase CLI are not installed; JSON parsing and relative import checks passed.
