import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const COMPOSE_FILE = "docker-compose.local.yml";

// Seeded local credentials and fixtures (backend/db/seeds/003_local_snapshot.sql).
// On seed drift, update these constants instead of individual specs.
export const ADMIN = { email: "admin@test.com", password: "test1234." };
export const PARENT = { email: "and7005@gmail.com", password: "Test1234" };
export const DRIVER = { email: "and7005@yahoo.it", password: "Test1234", pin: "0322" };

// Tenancy identities (U1): seeded staff/provider accounts for the
// multi-tenant suites (backend/db/seeds/003_local_snapshot.sql tail).
export const DIRECTOR_A = { email: "director.a@saferide.test", password: "Test1234" };
export const COORDINATOR_A = { email: "coordinator.a@saferide.test", password: "Test1234" };
export const DIRECTOR_B = { email: "director.b@saferide.test", password: "Test1234" };
export const PROVIDER = { email: "provider@kuumbai.test", password: "Test1234" };
export const DRIVER_B = { email: "driver.b@saferide.test", password: "Test1234", pin: "7391" };
export const SCHOOL_A_ID = "5cae0000-0000-0000-0000-000000000001";
export const SCHOOL_B_ID = "5cae0000-0000-0000-0000-000000000002";
export const SEED_B = {
  school: "IT Second School",
  bus: "IT Bus B",
  student: "Ben Barasa",
  route: "IT B — Morning",
};

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

/**
 * One token per account per worker.
 *
 * Every credential endpoint is rate limited on purpose — login is 10 attempts
 * per account per five minutes — and the suite was spending that budget on
 * setup: a full run drove the sign-in form dozens of times as admin, so the
 * last specs to run were refused and failed on a login timeout that had nothing
 * to do with what they were testing. Signing in once and reusing the token
 * keeps the limiter protecting the product rather than throttling the tests.
 */
const tokenCache = new Map<string, string>();

async function cachedToken(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const hit = tokenCache.get(email);
  if (hit) return hit;
  const token = await apiToken(request, email, password);
  tokenCache.set(email, token);
  return token;
}

/**
 * Put an authenticated session in the browser without driving the form.
 *
 * For every spec whose subject is not the login screen itself. The form's own
 * behaviour — success, wrong password, PIN entry — stays covered by
 * auth.spec.ts, which calls emailLogin/pinLogin below and must keep doing so.
 */
export async function signInAs(
  page: Page,
  account: { email: string; password: string },
  schoolId?: string,
): Promise<void> {
  const token = await cachedToken(page.request, account.email, account.password);
  // Any app-origin document, so localStorage is writable before the app boots.
  await page.goto("/auth");
  await page.evaluate((t) => localStorage.setItem("saferide-token", t), token);
  // Tenancy (U1/U12): the active school is per-tab, in sessionStorage. With
  // no school given, clear any value a previous sign-in left in this tab so
  // each signInAs starts the account's own first-landing flow.
  if (schoolId) {
    await page.evaluate((s) => sessionStorage.setItem("saferide-school", s), schoolId);
  } else {
    await page.evaluate(() => sessionStorage.removeItem("saferide-school"));
  }
  await page.goto("/");
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"));
}

/** The driver equivalent: PIN login is limited to 10 per IP per minute. */
export async function signInAsDriver(page: Page): Promise<void> {
  const token = await cachedDriverToken(page.request);
  await page.goto("/auth");
  await page.evaluate((t) => localStorage.setItem("saferide-token", t), token);
  // Driver routes 403 the school header by design (U12): make sure no staff
  // test's per-tab school survives into this driver session.
  await page.evaluate(() => sessionStorage.removeItem("saferide-school"));
  await page.goto("/driver");
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"));
}

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
  return cachedDriverToken(request);
}

const DRIVER_TOKEN_KEY = "__driver_pin__";

async function cachedDriverToken(request: APIRequestContext): Promise<string> {
  const hit = tokenCache.get(DRIVER_TOKEN_KEY);
  if (hit) return hit;
  const response = await request.post(`${API_URL}/api/auth/pin-login`, {
    data: { pin: DRIVER.pin },
  });
  expect(response.ok()).toBeTruthy();
  const token = (await response.json()).token;
  tokenCache.set(DRIVER_TOKEN_KEY, token);
  return token;
}

export function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** Staff-surface API calls carry the school scope explicitly (U12): with more
 * than one membership the server refuses to guess, so fixture/setup requests
 * name their school the same way the app does. */
export function schoolHeaders(token: string, schoolId: string) {
  return { Authorization: `Bearer ${token}`, "X-School-Id": schoolId };
}

/** End the demo driver's active run if one exists (idempotent cleanup). */
/**
 * Delete a run out-of-band. **Teardown only** — never inside an assertion.
 *
 * Since U7 the product refuses to delete a completed run dated today whose
 * children have recorded participation: those rows are the only evidence anyone
 * boarded, so cascading them would flip a whole roster from at school to at
 * home mid-day. That refusal is a real guarantee and the tests must not have a
 * product-level backdoor around it, so the suite's own cleanup goes to the
 * database directly — the same choice the integration suite's conftest makes.
 *
 * Uses the compose db container rather than a Node pg client, so the e2e suite
 * gains no new dependency.
 */
export function purgeRun(runId: string): void {
  psql(`delete from live_runs where id = '${runId}'`);
}

/**
 * Move a run into a previous service day. **Setup for stale-run specs only.**
 *
 * In SQL, as the integration suite's backdate_run does: nothing in the product
 * can change a run's service day, and waiting for midnight is not a test. A
 * backdated run falls out of every driver path, so the spec that creates one
 * must purgeRun it itself — endActiveRun will not find it.
 */
export function backdateRun(runId: string, days = 1): void {
  psql(`update live_runs set date = date - ${days} where id = '${runId}'`);
}

function psql(sql: string): void {
  execFileSync(
    "docker",
    [
      "compose", "-f", COMPOSE_FILE, "exec", "-T", "db",
      "psql", "-U", "saferide", "-d", "saferide", "-q", "-c", sql,
    ],
    { stdio: "ignore", cwd: REPO_ROOT },
  );
}

/** Like psql() but returns rows (tuples-only, unaligned; one row per line). */
function psqlQuery(sql: string): string {
  return execFileSync(
    "docker",
    [
      "compose", "-f", COMPOSE_FILE, "exec", "-T", "db",
      "psql", "-U", "saferide", "-d", "saferide", "-tA", "-c", sql,
    ],
    { encoding: "utf8", cwd: REPO_ROOT },
  ).trim();
}

// Fixture schools in SQL (U12): the app can no longer create or delete a
// school (creation moved to the provider console, deletion is out of scope),
// so suites that need a disposable school — admin-plan.spec.ts applies whole
// fleet plans and must never rewrite the seeded schools' routes — provision
// one directly in the database, with a director membership for the acting
// account, and drop it the same way. This mirrors purgeRun/backdateRun: the
// tests must not get a product-level backdoor around a real product rule.

/** Create a school row plus an active director membership for `email`. */
export function sqlCreateSchool(name: string, email: string): { id: string; code: string } {
  const id = crypto.randomUUID();
  const code = `E2P-${id.slice(0, 8)}`;
  psql(
    `insert into live_schools (id, name, address, phone, lat, lng, morning_bell, afternoon_bell, code) ` +
      `values ('${id}', '${name.replace(/'/g, "''")}', '1 Plan Lane, Nairobi', '+254700000001', ` +
      `-1.3005, 36.8102, '07:30', '15:30', '${code}')`,
  );
  psql(
    `insert into school_memberships (user_id, school_id, role, state, accepted_at) ` +
      `select id, '${id}', 'director', 'active', now() from app_users ` +
      `where lower(email) = lower('${email.replace(/'/g, "''")}')`,
  );
  return { id, code };
}

/** Drop a fixture school and every school-stamped row it still owns. */
export function sqlDropSchool(schoolId: string): void {
  const tables = [
    "live_notifications",
    "live_communicated_stops",
    "live_incidents",
    "live_student_absences",
    "live_parent_students",
    "live_student_routes",
    "live_route_stops",
    // 016 run children before the run; the key table before the school row.
    "run_exception_events",
    "run_exceptions",
    "run_positions",
    "run_stops",
    "run_absences",
    "run_participation",
    "live_runs",
    "live_routes",
    "live_students",
    "live_buses",
    "driver_action_keys",
    "school_memberships",
    "provider_support_sessions",
  ];
  for (const table of tables) {
    psql(`delete from ${table} where school_id = '${schoolId}'`);
  }
  // Fleet-plan documents cascade with the school row (011).
  psql(`delete from live_schools where id = '${schoolId}'`);
}

/** Ids of fixture schools left behind by aborted runs (sweep support). */
export function sqlListSchoolIdsByName(prefix: string): string[] {
  const out = psqlQuery(
    `select id from live_schools where name like '${prefix.replace(/'/g, "''")}%'`,
  );
  return out ? out.split("\n").map((line) => line.trim()).filter(Boolean) : [];
}

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
      purgeRun(run.id);
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

// TOTP for the provider suite (U13) ------------------------------------------
//
// RFC 6238 over the base32 key captured at the enrolment screen: 30-second
// steps, HMAC-SHA1, 6 digits — exactly backend/app/core/totp.py. The server
// accepts the current step ±1 and REFUSES any step at or below the last
// accepted one (replay guard), so a burst of code-verified actions inside one
// 30-second window would fail with "Invalid code". TotpMinter tracks the
// last minted step and, when the next unconsumed step is still outside the
// server's window, waits for the clock — tests must mint every code through
// one shared minter instance.

const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
export const TOTP_STEP_SECONDS = 30;

export function base32Decode(input: string): Buffer {
  const clean = input.toUpperCase().replace(/=+$/, "").replace(/\s+/g, "");
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const ch of clean) {
    const index = BASE32_ALPHABET.indexOf(ch);
    if (index === -1) throw new Error(`Invalid base32 character: ${ch}`);
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

/** The RFC 6238/4226 code for one step counter (SHA-1, 6 digits). */
export function totpCodeAtStep(secretB32: string, step: number): string {
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", base32Decode(secretB32)).update(message).digest();
  const offset = digest[digest.length - 1]! & 0x0f;
  const binary =
    ((digest[offset]! & 0x7f) << 24) |
    (digest[offset + 1]! << 16) |
    (digest[offset + 2]! << 8) |
    digest[offset + 3]!;
  return String(binary % 1_000_000).padStart(6, "0");
}

export function currentTotpStep(atMs: number = Date.now()): number {
  return Math.floor(atMs / 1000 / TOTP_STEP_SECONDS);
}

export class TotpMinter {
  private lastStep = -1;

  constructor(private readonly secretB32: string) {}

  /** The next acceptable code: never re-mints a consumed step; waits out the
   * clock when the next unconsumed step is still ahead of the ±1 window. */
  async next(): Promise<string> {
    for (;;) {
      const now = currentTotpStep();
      const target = Math.max(now, this.lastStep + 1);
      if (target <= now + 1) {
        this.lastStep = target;
        return totpCodeAtStep(this.secretB32, target);
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 1_000));
    }
  }
}

// Provider seed-state restoration (U13). The seeded provider
// (provider@kuumbai.test) ships UNENROLLED with a fixed salt; the provider
// suite enrols it. The product path back is the reset-totp peer action —
// provider.spec.ts ends by exercising exactly that — and these SQL helpers
// are the crash-proof backstop (a run that dies mid-suite must not leave the
// account enrolled under a secret nobody knows), the same philosophy as
// purgeRun: idempotent restoration of seeded state, not a backdoor around a
// product rule.

const PROVIDER_USER_ID = "a0000000-0000-0000-0000-000000000014";

/** Restore the seeded provider to its unenrolled seed state (fixed salt). */
export function sqlResetProviderTotp(): void {
  psql(
    `update provider_accounts set totp_enrolled_at = null, totp_last_step = null, ` +
      `totp_salt = '5eedab1e5a17c0ffee00000000000001', totp_pepper_key = null ` +
      `where user_id = '${PROVIDER_USER_ID}'`,
  );
}

/**
 * Restore a seeded stop exception to its unreviewed seed state (GPS plan U4).
 * The review route is idempotent and stamps once, so a spec that clicks
 * Reviewed on a seeded row must put the stamp back or the next run of the
 * suite finds nothing left to review. Same philosophy as purgeRun: restoring
 * seeded state, not a backdoor.
 *
 * The exception-reviewed audit row goes too: the seed carries none, and a
 * surviving one breaks the migration rehearsal in
 * backend/tests/integration/test_tenancy_schema.py, which re-applies 013's
 * audit CHECK (without that action) to the populated database before 016
 * widens it again. The throwaway sandbox schools the integration suite
 * reviews in take their audit rows with them; the seeded school does not.
 */
export function sqlUnreviewException(exceptionId: string): void {
  psql(
    `update run_exceptions set reviewed_at = null, reviewed_by = null ` +
      `where id = '${exceptionId}'`,
  );
  psql(
    `delete from live_admin_audit where action = 'exception-reviewed' ` +
      `and resource_id = '${exceptionId}'`,
  );
}

/**
 * Age a bus's served position by `seconds` (GPS plan U8, R27/AE13). **Setup
 * for staleness specs only**: nothing in the product can back-date a fix, and
 * waiting out the staleness threshold is not a test. The backdate_run
 * precedent.
 */
export function sqlAgeBusPosition(busId: string, seconds: number): void {
  psql(
    `update live_buses set position_at = now() - make_interval(secs => ${Math.floor(seconds)}) ` +
      `where id = '${busId}'`,
  );
}

/**
 * Move every trail row of a run `seconds` into the past by capture time (GPS
 * plan U12). **Setup for the custody and remote-absent specs only**: the
 * plausibility safeguard flags a fix that moved further than a bus could
 * since the run's previous fix, and an emulated phone teleports. Aging the
 * earlier fixes stands in for the minutes a driver takes to pull away from a
 * stop; nothing in the product can do this. The sqlAgeBusPosition precedent.
 */
export function sqlAgeTrail(runId: string, seconds: number): void {
  psql(
    `update run_positions set captured_at = captured_at - make_interval(secs => ${Math.floor(seconds)}) ` +
      `where run_id = '${runId}'`,
  );
}

/**
 * Null a bus's served position. **Teardown only**: purgeRun deletes the run
 * but not the five position columns End Run would have cleared, and a leftover
 * pair reads as a live bus on the next spec's fleet map.
 */
export function sqlClearBusPosition(busId: string): void {
  psql(
    `update live_buses set current_lat = null, current_lng = null, position_source = null, ` +
      `position_at = null, position_accuracy_m = null where id = '${busId}'`,
  );
}

/** Close any support session a crashed run left open for the seeded provider. */
export function sqlEndProviderSupportSessions(): void {
  psql(
    `update provider_support_sessions set ended_at = now(), end_cause = 'revoked' ` +
      `where provider_user_id = '${PROVIDER_USER_ID}' and ended_at is null`,
  );
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

