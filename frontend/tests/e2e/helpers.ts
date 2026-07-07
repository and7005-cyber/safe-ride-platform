import { expect, type APIRequestContext, type Page } from "@playwright/test";

// Seeded local credentials and fixtures (backend/db/seeds/003_local_snapshot.sql).
// On seed drift, update these constants instead of individual specs.
export const ADMIN = { email: "admin@test.com", password: "test1234." };
export const PARENT = { email: "and7005@gmail.com", password: "Test1234" };
export const DRIVER = { email: "and7005@yahoo.it", password: "Test1234", pin: "0322" };

export const SEED = {
  school: "Greenfield Academy",
  /** The demo driver's (Daniel Kamau) live bus. */
  driverBus: "Simba",
  /** The driver bus's seeded routes (Run page dropdown options). */
  driverMorningRoute: "Express 1 — Morning",
  driverAfternoonRoute: "Express 1 — Afternoon",
  /** Any seeded live bus, for admin bus <Select> options. */
  busOption: /Simba|Twiga|Mamba/,
  /** Full name of the PARENT account (Amina). */
  parentName: "Amina Achieng",
  /** Amina's child riding the demo driver's bus (first stop of Express 1). */
  parentChild: "Faith Achieng",
  /** Search term that narrows the boarding list to exactly parentChild. */
  parentChildSearch: "Faith",
  /** The other seeded student riding Express 1 — Afternoon with parentChild. */
  afternoonRideMate: "Happiness Kenesa",
  /** The stop name shown for parentChild on the track map. */
  parentChildStop: /Kilimani/,
  /** Amina's bus-less child (renders without driver actions). */
  buslessChild: "Grace Njeri",
  /** Parent-side label of the seeded incident on the parent's bus. */
  parentBusAlertLabel: "Vehicle Breakdown",
};

export const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:9001";

export async function emailLogin(page: Page, email: string, password: string) {
  await page.goto("/auth");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  // Wait for the post-login redirect so the bearer token is persisted before
  // any follow-up navigation reloads the app.
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"));
}

export async function pinLogin(page: Page, pin: string) {
  await page.goto("/auth");
  await page.getByRole("tab", { name: /Driver PIN/ }).click();
  await page.locator("#pin").fill(pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await page.waitForURL("/driver");
}

// API-side helpers for cross-role test setup/teardown ------------------------

export async function apiToken(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const response = await request.post(`${API_URL}/api/auth/login`, {
    data: { email, password },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).token;
}

export async function apiDriverToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API_URL}/api/auth/pin-login`, {
    data: { pin: DRIVER.pin },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).token;
}

export function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** End the demo driver's active run if one exists (idempotent cleanup). */
export async function endActiveRun(request: APIRequestContext): Promise<void> {
  const token = await apiDriverToken(request);
  const context = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  if (!context.ok()) return;
  const body = await context.json();
  const activeRun = body.active_run;
  if (activeRun) {
    await request.post(`${API_URL}/api/runs/driver/end`, {
      headers: authHeaders(token),
      data: { run_id: activeRun.id },
    });
  }
  // Also DELETE today's runs for the driver's bus: completed runs block
  // same-day restarts (R28) and would poison later lifecycle tests.
  const busId = body.bus?.id;
  if (!busId) return;
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const runs = await request.get(`${API_URL}/api/runs`, {
    headers: authHeaders(adminToken),
  });
  if (!runs.ok()) return;
  const today = new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10); // Nairobi UTC+3
  for (const run of await runs.json()) {
    if (run.bus_id === busId && String(run.date).slice(0, 10) === today) {
      await request.delete(`${API_URL}/api/runs/${run.id}`, {
        headers: authHeaders(adminToken),
      });
    }
  }
}

// Cancel-a-Ride journey helpers (U14): the cross-role specs set up and tear
// down parent cancellations through the API so each spec file stays
// self-contained (the suite is serial, but files must not depend on each
// other's leftover state).

export type CancelScope = "morning" | "afternoon" | "day";

/** Parent-side Cancel-a-Ride (R14): cancel `scope` for the named linked child
 * today. Returns the child's student id. */
export async function apiCancelRide(
  request: APIRequestContext,
  childName: string,
  scope: CancelScope,
): Promise<string> {
  const token = await apiToken(request, PARENT.email, PARENT.password);
  const children = await request.get(`${API_URL}/api/parent-portal/children`, {
    headers: authHeaders(token),
  });
  expect(children.ok()).toBeTruthy();
  const child = (await children.json()).find((c: any) => c.name === childName);
  expect(child, `${childName} should be linked to the seeded parent`).toBeTruthy();
  const cancelled = await request.post(`${API_URL}/api/parent-portal/cancel-ride`, {
    headers: authHeaders(token),
    data: { student_id: child.id, scope },
  });
  expect(cancelled.ok()).toBeTruthy();
  return child.id;
}

/** Office-side cleanup after a cancellation journey (idempotent): remove the
 * student's absence rows and their "Ride Cancellation" alerts. Uses the admin
 * endpoints rather than the parent withdraw, which by design 409s once a
 * covered run row exists for the day (R18). */
export async function clearCancellationState(
  request: APIRequestContext,
  studentName: string,
): Promise<void> {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = authHeaders(token);
  const absences = await request.get(`${API_URL}/api/students/absences`, { headers });
  if (absences.ok()) {
    for (const row of await absences.json()) {
      if (row.student_name === studentName) {
        await request.delete(`${API_URL}/api/students/absences/${row.id}`, { headers });
      }
    }
  }
  const incidents = await request.get(`${API_URL}/api/incidents`, { headers });
  if (incidents.ok()) {
    for (const row of await incidents.json()) {
      if (row.type === "cancellation" && String(row.description ?? "").startsWith(`${studentName}:`)) {
        await request.delete(`${API_URL}/api/incidents/${row.id}`, { headers });
      }
    }
  }
}

export function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
}

// Admin dialog forms render <Label>Text</Label><Input/> without htmlFor, so
// fields are located through their shared container.
import type { Locator } from "@playwright/test";

export function fieldInput(scope: Locator, label: string): Locator {
  return scope.locator(`div:has(> label:text-is("${label}"))`).locator("input, textarea").first();
}

export async function pickSelectOption(scope: Locator, label: string, option: string | RegExp) {
  await scope.locator(`div:has(> label:text-is("${label}"))`).locator("button").first().click();
  await scope.page().getByRole("option", { name: option }).first().click();
}

/** The shadcn Card root (class bg-card) containing the given text. */
export function cardContaining(page: Page, text: string | RegExp): Locator {
  return page.locator("div[class*='bg-card']").filter({ hasText: text }).first();
}

/** Confirm a destructive action in the shared "are you sure?" dialog (#6). */
export async function confirmDelete(page: Page) {
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: /Delete|Remove|Yes/ }).click();
  await expect(dialog).toHaveCount(0);
}

