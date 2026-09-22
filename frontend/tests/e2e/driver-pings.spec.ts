import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  SCHOOL_A_ID,
  SEED,
  apiDriverToken,
  apiToken,
  authHeaders,
  endActiveRun,
  purgeRun,
  schoolHeaders,
  signInAs,
  signInAsDriver,
  sqlAgeBusPosition,
  sqlClearBusPosition,
} from "./helpers";

// Phase 2 interval pings, the wake lock and prominent staleness (GPS plan
// U14: R24, R25, R27, R28; F6; AE13). The driver drives the app in the
// browser with an emulated phone; the pings it posts are read off the
// requests and their effect off the staff API and the fleet map.
//
// Seeded morning route "Express 1 — Morning" on Simba (driver PIN 0322),
// school A. The school's ping interval is set to the smallest allowed for
// the spec and restored afterwards.

test.describe.configure({ timeout: 180_000 });

const START = { latitude: -1.2921, longitude: 36.8219, accuracy: 20 };
const PING_INTERVAL_S = 5;
const PINGS = "/api/runs/driver/pings";
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$/;

/** A wake lock the page can hold: every request and release is recorded. */
const WAKE_SPY = `
  window.__wake = { requests: [], releases: 0 };
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: {
      request: async (type) => {
        window.__wake.requests.push(type);
        const sentinel = {
          type, released: false, onrelease: null, _listener: null,
          addEventListener(name, fn) { if (name === "release") sentinel._listener = fn; },
          release: async () => {
            sentinel.released = true;
            window.__wake.releases += 1;
            if (sentinel._listener) sentinel._listener();
            if (sentinel.onrelease) sentinel.onrelease();
          },
        };
        return sentinel;
      },
    },
  });
`;

/** A browser that refuses the lock (battery saver, a policy). */
const WAKE_DENIED = `
  window.__wake = { requests: [], releases: 0 };
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    value: {
      request: async (type) => {
        window.__wake.requests.push(type);
        throw new DOMException("Wake lock refused", "NotAllowedError");
      },
    },
  });
`;

interface WakeSpy {
  requests: string[];
  releases: number;
}

async function readWake(page: Page): Promise<WakeSpy> {
  return page.evaluate(() => (window as any).__wake);
}

async function driverContext(request: APIRequestContext) {
  const token = await apiDriverToken(request);
  const response = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function deleteTodaysRuns(request: APIRequestContext) {
  const context = await driverContext(request);
  const busId = context.bus?.id;
  if (!busId) return;
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const today = new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const runs = await request.get(`${API_URL}/api/runs`, { headers: authHeaders(adminToken) });
  for (const run of await runs.json()) {
    if (run.bus_id === busId && String(run.date).startsWith(today)) purgeRun(run.id);
  }
  sqlClearBusPosition(busId);
}

/** The seeded school's ping interval, through the Settings API (U11). */
async function setPingInterval(request: APIRequestContext, value: number | null) {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const school = await (await request.get(`${API_URL}/api/fleet/school`, { headers })).json();
  const base = Object.fromEntries(
    ["name", "address", "phone", "lat", "lng", "morning_bell", "afternoon_bell"].map((k) => [k, school[k] ?? null]),
  );
  const response = await request.put(`${API_URL}/api/fleet/schools/${SCHOOL_A_ID}`, {
    headers,
    data: { ...base, ping_interval_s: value },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function staffBusPosition(request: APIRequestContext, busId: string) {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const response = await request.get(`${API_URL}/api/fleet/buses`, {
    headers: schoolHeaders(token, SCHOOL_A_ID),
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).find((b: any) => b.id === busId)?.position ?? null;
}

async function startMorningRun(page: Page) {
  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverMorningRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();
}

/** Fake the page going to the background (a locked phone): the app reads
 * `document.visibilityState` and listens for `visibilitychange`. */
async function setVisibility(page: Page, state: "hidden" | "visible") {
  await page.evaluate((next) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => next });
    Object.defineProperty(document, "hidden", { configurable: true, get: () => next === "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
}

test.use({ geolocation: START });

test.beforeEach(async ({ request }) => {
  await setPingInterval(request, PING_INTERVAL_S);
});

test.afterEach(async ({ request }) => {
  await endActiveRun(request);
  await deleteTodaysRuns(request);
  await setPingInterval(request, null);
});

test("AE13: pings flow while the run is active and the page visible, move the served position, stop when hidden, resume on return; the label goes stale and refreshes; the wake lock is held for the run", async ({ page, context, browser, request }) => {
  await page.addInitScript(WAKE_SPY);
  const pings: { body: any; status: number }[] = [];
  page.on("response", async (response) => {
    if (response.url().endsWith(PINGS) && response.request().method() === "POST") {
      pings.push({ body: response.request().postDataJSON(), status: response.status() });
    }
  });

  await signInAsDriver(page);
  // Nothing before Start Run (R24).
  await page.goto("/driver/run");
  await page.waitForTimeout(PING_INTERVAL_S * 1000 + 1_000);
  expect(pings).toHaveLength(0);

  await startMorningRun(page);
  const { active_run: run, bus } = await driverContext(request);
  expect(run).toBeTruthy();
  // The wake lock was asked for on the tap (R25).
  await expect.poll(async () => (await readWake(page)).requests).toContain("screen");

  // The first batch: the watch's first fix, on the school's interval.
  await expect.poll(() => pings.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
  const first = pings[0]!;
  expect(first.status).toBe(200);
  expect(first.body.run_id).toBe(run.id);
  expect(first.body.device_id).toMatch(UUID_V4);
  expect(first.body.fixes.length).toBeGreaterThanOrEqual(1);
  expect(first.body.fixes.length).toBeLessThanOrEqual(6);
  for (const fix of first.body.fixes) {
    expect(fix).toEqual({
      lat: expect.any(Number),
      lng: expect.any(Number),
      accuracy_m: START.accuracy,
      captured_at: expect.stringMatching(ISO_WITH_OFFSET),
    });
  }

  // The bus moves: the next batch carries the new fix and the served
  // position follows it, as source `ping`.
  const moved = { latitude: -1.2941, longitude: 36.8219, accuracy: 20 };
  await context.setGeolocation(moved);
  await expect.poll(() => pings.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(2);
  await expect.poll(async () => (await staffBusPosition(request, bus.id))?.source, { timeout: 15_000 }).toBe("ping");
  const served = await staffBusPosition(request, bus.id);
  expect(served.lat).toBeCloseTo(moved.latitude, 5);
  expect(served.stale).toBe(false);

  // The phone is locked: no pings, however far the (still emitting)
  // geolocation says the bus went.
  await setVisibility(page, "hidden");
  const sentBeforeHide = pings.length;
  await context.setGeolocation({ latitude: -1.2961, longitude: 36.8219, accuracy: 20 });
  await page.waitForTimeout(PING_INTERVAL_S * 1000 * 2.5);
  expect(pings.length).toBe(sentBeforeHide);

  // Past the staleness threshold the office sees "last seen" (R27):
  // the Buses card and the marker, dimmed, still on the map.
  sqlAgeBusPosition(bus.id, 240);
  const office = await browser.newContext();
  const admin = await office.newPage();
  try {
    await signInAs(admin, ADMIN, SCHOOL_A_ID);
    await admin.goto("/fleet-map");
    const row = admin.getByTestId(`bus-row-${bus.id}`).getByTestId("bus-row-freshness");
    await expect(row).toHaveText(/^last seen 4 min ago$/, { timeout: 15_000 });
    await expect(row).toHaveAttribute("data-stale", "true");
    const marker = admin.locator(`[data-testid="bus-marker"][data-bus-id="${bus.id}"]`);
    await expect(marker).toHaveAttribute("data-stale", "true");
    await expect(marker).toHaveCount(1);
    await expect(admin.getByTestId("fleet-map-summary")).toContainText("last seen a while ago");

    // The driver unlocks the phone: pings resume and the label refreshes.
    await setVisibility(page, "visible");
    await context.setGeolocation({ latitude: -1.2971, longitude: 36.8219, accuracy: 20 });
    await expect.poll(() => pings.length, { timeout: 20_000 }).toBeGreaterThan(sentBeforeHide);
    expect(pings[pings.length - 1]!.status).toBe(200);
    await expect(row).toHaveText(/^updated (just now|\d+ s ago)$/, { timeout: 20_000 });
    await expect(row).toHaveAttribute("data-stale", "false");
    await expect(admin.getByTestId("fleet-map-summary")).not.toContainText("last seen");
  } finally {
    await office.close();
  }

  // The run ends (the office's close would do the same): the stream stops
  // within one poll and the lock is released.
  await endActiveRun(request);
  await expect.poll(async () => (await readWake(page)).releases, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  const sentAtEnd = pings.length;
  await context.setGeolocation({ latitude: -1.2981, longitude: 36.8219, accuracy: 20 });
  await page.waitForTimeout(PING_INTERVAL_S * 1000 * 2);
  expect(pings.length).toBe(sentAtEnd);
});

test("a browser that refuses the wake lock: the run starts, the Run page shows the keep-your-screen-on hint, and pings still flow", async ({ page }) => {
  await page.addInitScript(WAKE_DENIED);
  const pingRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith(PINGS) && r.method() === "POST") pingRequests.push(r.url());
  });
  await signInAsDriver(page);
  await startMorningRun(page);
  await expect.poll(async () => (await readWake(page)).requests.length).toBeGreaterThanOrEqual(1);
  const hint = page.getByTestId("wake-lock-hint");
  await expect(hint).toBeVisible();
  await expect(hint).toContainText("Keep your screen on during the run");
  await expect(hint).toHaveAttribute("data-reason", "denied");
  await expect.poll(() => pingRequests.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
  // Every tap keeps working underneath the hint.
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
});

test("R28: a second sign-in that taps takes the run's pings with it; this app's stream pauses with a hint until its own next tap re-binds the run", async ({ page, context, request }) => {
  const pings: { status: number; code: string | null }[] = [];
  page.on("response", async (response) => {
    if (response.url().endsWith(PINGS) && response.request().method() === "POST") {
      let code: string | null = null;
      try {
        code = (await response.json())?.detail?.code ?? null;
      } catch {
        // A 200 body has no detail.
      }
      pings.push({ status: response.status(), code });
    }
  });
  await signInAsDriver(page);
  await startMorningRun(page);
  await expect.poll(() => pings.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
  expect(pings[0]!.status).toBe(200);
  await expect(page.getByTestId("ping-hint")).toHaveCount(0);

  // The same PIN on another phone taps Arrive: the server re-binds the run
  // to that session (U7), so this app's next batch is refused.
  const { active_run: run } = await driverContext(request);
  const other = await request.post(`${API_URL}/api/auth/pin-login`, { data: { pin: "0322" } });
  expect(other.ok()).toBeTruthy();
  const otherToken = (await other.json()).token;
  const arrived = await request.post(`${API_URL}/api/runs/driver/arrive`, {
    headers: { ...authHeaders(otherToken), "Idempotency-Key": crypto.randomUUID() },
    data: { run_id: run.id, expected_stop_order: 1 },
  });
  expect(arrived.ok(), await arrived.text()).toBeTruthy();

  await context.setGeolocation({ latitude: -1.2941, longitude: 36.8219, accuracy: 20 });
  await expect.poll(() => pings.some((p) => p.code === "session-mismatch"), { timeout: 20_000 }).toBe(true);
  const hint = page.getByTestId("ping-hint");
  await expect(hint).toBeVisible();
  await expect(hint).toContainText("started from another sign-in");
  // Paused: no further batches while nothing is tapped here.
  const refusedAt = pings.length;
  await context.setGeolocation({ latitude: -1.2951, longitude: 36.8219, accuracy: 20 });
  await page.waitForTimeout(PING_INTERVAL_S * 1000 * 2);
  expect(pings.length).toBe(refusedAt);

  // This app's own tap re-binds the run: the hint goes and pings resume.
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/^2\/\d+ stops completed$/)).toBeVisible();
  await expect(hint).toHaveCount(0);
  await context.setGeolocation({ latitude: -1.2961, longitude: 36.8219, accuracy: 20 });
  await expect.poll(() => pings.length, { timeout: 20_000 }).toBeGreaterThan(refusedAt);
  expect(pings[pings.length - 1]).toEqual({ status: 200, code: null });
});
