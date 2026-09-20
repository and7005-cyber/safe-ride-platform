import { expect, test, type APIRequestContext, type BrowserContext, type Page, type Request } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  PARENT,
  SEED,
  apiDriverToken,
  apiToken,
  authHeaders,
  clearCancellationState,
  endActiveRun,
  purgeRun,
  signInAs,
  signInAsDriver,
  sqlAgeTrail,
} from "./helpers";

// The driver's GPS fix on every tap (GPS plan U6: R1–R5, R33 client half;
// F1, F5; AE1, AE2, AE12). The server half is U7: today's API ignores the
// `fix`, `device_id` and `expected_stop_order` fields and the
// `Idempotency-Key` header, so every assertion here is on what the client
// sends, read off the request itself, and on what the driver sees.
//
// Seeded morning route "Express 1 — Morning": Kilimani, Lavington, Karen,
// then the school gate — four stops, three children.

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$/;
const NAIROBI = { latitude: -1.2921, longitude: 36.8219, accuracy: 25 };
const FIX_WAIT_BUDGET_MS = 5_000;

/** Delete today's runs for the driver's bus (completed runs block same-day
 * restarts, and a purged run cascades its trail rows and exceptions). */
async function deleteTodaysRuns(request: APIRequestContext) {
  const driverToken = await apiDriverToken(request);
  const context = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(driverToken),
  });
  if (!context.ok()) return;
  const busId = (await context.json()).bus?.id;
  if (!busId) return;
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const today = new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const runs = await request.get(`${API_URL}/api/runs`, { headers: authHeaders(adminToken) });
  for (const run of await runs.json()) {
    if (run.bus_id === busId && String(run.date).startsWith(today)) {
      purgeRun(run.id);
    }
  }
}

test.use({ geolocation: NAIROBI });

test.afterEach(async ({ request }) => {
  await endActiveRun(request);
  await deleteTodaysRuns(request);
});

// Spies on the real geolocation object (AE12): every watch, clear and
// one-shot request is recorded, installed before any app script runs.
const GEO_SPY = `
  window.__geo = { watch: [], clear: [], current: 0 };
  const geo = navigator.geolocation;
  const watch = geo.watchPosition.bind(geo);
  const clear = geo.clearWatch.bind(geo);
  const current = geo.getCurrentPosition.bind(geo);
  geo.watchPosition = (ok, err, opts) => {
    const id = watch(ok, err, opts);
    window.__geo.watch.push({ id, opts });
    return id;
  };
  geo.clearWatch = (id) => { window.__geo.clear.push(id); return clear(id); };
  geo.getCurrentPosition = (ok, err, opts) => { window.__geo.current += 1; return current(ok, err, opts); };
`;

/** A device whose location provider answers every request with
 * POSITION_UNAVAILABLE, at once. */
const GEO_UNAVAILABLE = `
  const error = { code: 2, message: "Position unavailable", PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 };
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition: (ok, err) => { setTimeout(() => err && err(error), 0); },
      watchPosition: (ok, err) => { setTimeout(() => err && err(error), 0); return 7; },
      clearWatch: () => {},
    },
  });
`;

/** A device that never answers at all. */
const GEO_SILENT = `
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition: () => {}, watchPosition: () => 9, clearWatch: () => {} },
  });
`;

/** A phone that has not answered the browser's location prompt yet: the
 * Permissions API says `prompt` while the (granted) emulation still answers. */
const PERMISSION_PROMPT = `
  const query = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (descriptor) =>
    descriptor && descriptor.name === "geolocation"
      ? Promise.resolve({ state: "prompt", onchange: null })
      : query(descriptor);
`;

interface GeoSpy {
  watch: { id: number; opts: PositionOptions | undefined }[];
  clear: number[];
  current: number;
}

async function readGeo(page: Page): Promise<GeoSpy> {
  return page.evaluate(() => (window as any).__geo);
}

interface Captured {
  key: string | undefined;
  body: any;
}

function captured(request: Request): Captured {
  return { key: request.headers()["idempotency-key"], body: request.postDataJSON() };
}

/** Perform `act` and return the driver action POST it produced. */
async function captureAction(page: Page, path: string, act: () => Promise<void>): Promise<Captured> {
  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith(path) && r.method() === "POST"),
    act(),
  ]);
  return captured(request);
}

async function chooseMorningRoute(page: Page) {
  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverMorningRoute }).click();
}

async function startMorningRun(page: Page): Promise<Captured> {
  await chooseMorningRoute(page);
  const start = await captureAction(page, "/api/runs/driver/start", () =>
    page.getByRole("button", { name: "Start Run" }).click(),
  );
  await expect(page.getByText("Run in progress")).toBeVisible();
  return start;
}

async function arriveAllStops(page: Page) {
  await page.goto("/driver/run");
  const arrive = page.getByRole("button", { name: "Arrive Next Stop" });
  const progress = page.getByText(/\d+\/\d+ stops completed/);
  for (let i = 0; i < 12; i++) {
    await expect(progress).toBeVisible();
    if (await arrive.isDisabled()) return;
    const before = await progress.textContent();
    await arrive.click();
    await expect(progress).not.toHaveText(before ?? "");
  }
}

async function boardEveryone(page: Page) {
  for (let i = 0; i < 12; i++) {
    await page.goto("/driver/boarding");
    await expect(page.getByText(/Students \([1-9]\d*\)/)).toBeVisible();
    const next = page.getByRole("button", { name: "Board", exact: true }).first();
    if ((await next.count()) === 0) return;
    await expect(next).toBeEnabled();
    await next.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Board", exact: true }).click();
    await expect(dialog).toHaveCount(0);
  }
}

function expectUsableFix(fix: any, accuracy = NAIROBI.accuracy) {
  expect(fix).toEqual({
    lat: NAIROBI.latitude,
    lng: NAIROBI.longitude,
    accuracy_m: accuracy,
    captured_at: expect.stringMatching(ISO_WITH_OFFSET),
  });
}

test("granted: every tap carries a fix, a key and the device id; a retry re-sends the same envelope; the watch lives only for the run and a reload resumes it", async ({ page }) => {
  await page.addInitScript(GEO_SPY);
  await signInAsDriver(page);
  await chooseMorningRoute(page);
  // Nothing before Start Run (R5, AE12).
  expect((await readGeo(page)).watch).toEqual([]);

  const start = await captureAction(page, "/api/runs/driver/start", () =>
    page.getByRole("button", { name: "Start Run" }).click(),
  );
  await expect(page.getByText("Run in progress")).toBeVisible();
  // Permission already granted: no explainer stood between the tap and the
  // request (the request went out, so nothing was waiting on a dialog).
  await expect(page.getByTestId("location-explainer")).toHaveCount(0);
  expect(start.key).toMatch(UUID_V4);
  expect(start.body.route_id).toBeTruthy();
  expect(start.body.device_id).toMatch(UUID_V4);
  expectUsableFix(start.body.fix);

  // The watch started inside the tap, high accuracy, once.
  const afterStart = await readGeo(page);
  expect(afterStart.watch).toHaveLength(1);
  expect(afterStart.watch[0]!.opts).toMatchObject({ enableHighAccuracy: true });
  const watchId = afterStart.watch[0]!.id;

  // AE1: Arrive sends the fix with capture time and accuracy, the stop it
  // intends to reach, the same device, a new key.
  const arrive = await captureAction(page, "/api/runs/driver/arrive", () =>
    page.getByRole("button", { name: "Arrive Next Stop" }).click(),
  );
  await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
  expect(arrive.key).toMatch(UUID_V4);
  expect(arrive.key).not.toBe(start.key);
  expect(arrive.body.expected_stop_order).toBe(1);
  expect(arrive.body.device_id).toBe(start.body.device_id);
  expectUsableFix(arrive.body.fix);

  // A reload mid-run resumes the watch from the context, with no explainer
  // and the permission still granted — no new prompt.
  await page.reload();
  await expect(page.getByText("Run in progress")).toBeVisible();
  await expect.poll(async () => (await readGeo(page)).watch.length).toBe(1);
  await expect(page.getByTestId("location-explainer")).toHaveCount(0);
  expect(
    await page.evaluate(() => navigator.permissions.query({ name: "geolocation" }).then((p) => p.state)),
  ).toBe("granted");
  const resumedWatchId = (await readGeo(page)).watch[0]!.id;

  // R3: the network drops the next Arrive; the driver taps again and the
  // retry carries the same key and the same frozen fix, not a new capture.
  const attempts: Captured[] = [];
  await page.route("**/api/runs/driver/arrive", async (route) => {
    attempts.push(captured(route.request()));
    if (attempts.length === 1) await route.abort("failed");
    else await route.continue();
  });
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/Failed to fetch/)).toBeVisible();
  await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/^2\/\d+ stops completed$/)).toBeVisible();
  await page.unroute("**/api/runs/driver/arrive");
  expect(attempts).toHaveLength(2);
  expect(attempts[0]!.key).toMatch(UUID_V4);
  expect(attempts[1]!.key).toBe(attempts[0]!.key);
  expect(attempts[1]!.body).toEqual(attempts[0]!.body);
  expect(attempts[0]!.body.expected_stop_order).toBe(2);
  expect(attempts[0]!.key).not.toBe(arrive.key);

  // Board through the board page: same envelope shape on an outcome route.
  await arriveAllStops(page);
  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \([1-9]\d*\)/)).toBeVisible();
  const boardButton = page.getByRole("button", { name: "Board", exact: true }).first();
  await expect(boardButton).toBeEnabled();
  await boardButton.click();
  const boarding = await captureAction(page, "/api/runs/driver/boarding", () =>
    page.getByRole("dialog").getByRole("button", { name: "Board", exact: true }).click(),
  );
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(boarding.key).toMatch(UUID_V4);
  expect(boarding.body).toMatchObject({ on_bus: true, device_id: start.body.device_id });
  expect(boarding.body.student_id).toBeTruthy();
  expectUsableFix(boarding.body.fix);
  await boardEveryone(page);

  // End Run carries the envelope too, and the watch stops with the run (AE12).
  await page.goto("/driver/run");
  await expect(page.getByTestId("blocking-list")).toHaveCount(0);
  await page.getByRole("button", { name: "End Run" }).click();
  const end = await captureAction(page, "/api/runs/driver/end", () =>
    page.getByRole("dialog").getByRole("button", { name: "End Run" }).click(),
  );
  await page.waitForURL("/driver");
  expect(end.key).toMatch(UUID_V4);
  expect(end.body).toMatchObject({ device_id: start.body.device_id });
  expect(end.body.run_id).toBeTruthy();
  expectUsableFix(end.body.fix);
  await expect.poll(async () => (await readGeo(page)).clear).toContain(resumedWatchId);
  expect((await readGeo(page)).watch).toHaveLength(1);
  expect(watchId).toBeGreaterThan(0);
});

test.describe("permission denied", () => {
  test.use({ permissions: [] });

  test("AE2: the tap completes with reason `denied`, the banner shows with unblock steps on every driver tab, and a later successful fix clears it", async ({ page, context }) => {
    await signInAsDriver(page);
    const start = await startMorningRun(page);
    // Chromium answers a non-granted request with PERMISSION_DENIED at once;
    // the Permissions API reads `denied`, so no explainer either.
    expect(start.body.fix).toEqual({ reason: "denied" });
    expect(start.key).toMatch(UUID_V4);
    await expect(page.getByTestId("location-explainer")).toHaveCount(0);

    const banner = page.getByTestId("location-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-kind", "denied");
    await expect(banner).toContainText("Location off");
    await expect(banner).toContainText("checkpoint positions only");
    await banner.getByTestId("location-banner-steps").click();
    expect(await banner.locator("li").count()).toBeGreaterThan(0);

    // Non-blocking: Arrive goes through, with the reason.
    const arrive = await captureAction(page, "/api/runs/driver/arrive", () =>
      page.getByRole("button", { name: "Arrive Next Stop" }).click(),
    );
    await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
    expect(arrive.body.fix).toEqual({ reason: "denied" });
    expect(arrive.body.expected_stop_order).toBe(1);

    // Persistent across the driver's screens.
    await page.goto("/driver/boarding");
    await expect(page.getByTestId("location-banner")).toBeVisible();
    await page.goto("/driver");
    await expect(page.getByTestId("location-banner")).toBeVisible();

    // The driver fixes the setting: the next tap gets a fix and the banner goes.
    await context.grantPermissions(["geolocation"]);
    await page.goto("/driver/run");
    const recovered = await captureAction(page, "/api/runs/driver/arrive", () =>
      page.getByRole("button", { name: "Arrive Next Stop" }).click(),
    );
    await expect(page.getByText(/^2\/\d+ stops completed$/)).toBeVisible();
    expectUsableFix(recovered.body.fix);
    await expect(page.getByTestId("location-banner")).toHaveCount(0);
  });
});

test("position unavailable: the tap completes with reason `unavailable` well inside the budget, and no banner", async ({ page }) => {
  await page.addInitScript(GEO_UNAVAILABLE);
  await signInAsDriver(page);
  await chooseMorningRoute(page);
  const tapped = Date.now();
  const start = await captureAction(page, "/api/runs/driver/start", () =>
    page.getByRole("button", { name: "Start Run" }).click(),
  );
  const elapsed = Date.now() - tapped;
  await expect(page.getByText("Run in progress")).toBeVisible();
  expect(start.body.fix).toEqual({ reason: "unavailable" });
  expect(elapsed).toBeLessThan(FIX_WAIT_BUDGET_MS);
  await expect(page.getByTestId("location-banner")).toHaveCount(0);
});

test("a device that never answers: the tap goes out at the budget with reason `timeout`", async ({ page }) => {
  await page.addInitScript(GEO_SILENT);
  await signInAsDriver(page);
  await chooseMorningRoute(page);
  const tapped = Date.now();
  const start = await captureAction(page, "/api/runs/driver/start", () =>
    page.getByRole("button", { name: "Start Run" }).click(),
  );
  const elapsed = Date.now() - tapped;
  await expect(page.getByText("Run in progress")).toBeVisible();
  expect(start.body.fix).toEqual({ reason: "timeout" });
  expect(elapsed).toBeGreaterThanOrEqual(FIX_WAIT_BUDGET_MS - 250);
  expect(elapsed).toBeLessThan(FIX_WAIT_BUDGET_MS + 3_000);
});

test("first run on a phone: the explainer precedes the browser's prompt; Cancel starts nothing; Continue starts the run with a fix", async ({ page }) => {
  await page.addInitScript(PERMISSION_PROMPT);
  const starts: Request[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/api/runs/driver/start") && r.method() === "POST") starts.push(r);
  });
  await signInAsDriver(page);
  await chooseMorningRoute(page);

  await page.getByRole("button", { name: "Start Run" }).click();
  const explainer = page.getByTestId("location-explainer");
  await expect(explainer).toBeVisible();
  await expect(explainer).toContainText("Share the bus's location during runs");
  for (const label of ["What", "When", "Who sees it", "How long", "Questions"]) {
    await expect(explainer.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(explainer).toContainText("Only while a run is in progress");
  await expect(explainer).toContainText("transport coordinator");
  await expect(explainer).toContainText("Whatever you choose, the run starts");

  // Cancel is the driver cancelling their own tap: no request, no run.
  await explainer.getByRole("button", { name: "Cancel" }).click();
  await expect(explainer).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start Run" })).toBeEnabled();
  expect(starts).toHaveLength(0);

  // Re-checked every run: the same tap asks again; Continue is the gesture
  // the browser's prompt lands in, and the run starts with the fix.
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(explainer).toBeVisible();
  const start = await captureAction(page, "/api/runs/driver/start", () =>
    explainer.getByRole("button", { name: "Continue" }).click(),
  );
  await expect(page.getByText("Run in progress")).toBeVisible();
  await expect(explainer).toHaveCount(0);
  expectUsableFix(start.body.fix);
  expect(starts).toHaveLength(1);
});

test("approximate location: a coarse fix keeps its coordinates with the reason; three fixes at 1 km or worse raise the precise-location banner; a better fix clears it", async ({ page, context }) => {
  await context.setGeolocation({ ...NAIROBI, accuracy: 1_500 });
  await signInAsDriver(page);
  const start = await startMorningRun(page);
  expect(start.body.fix).toEqual({
    lat: NAIROBI.latitude,
    lng: NAIROBI.longitude,
    accuracy_m: 1_500,
    captured_at: expect.stringMatching(ISO_WITH_OFFSET),
    reason: "coarse",
  });

  // Each emulated position change reaches the live watch. Start Run's own
  // request plus the watch's first delivery already count; a third
  // approximate fix trips the banner.
  await expect.poll(
    async () => {
      await context.setGeolocation({ ...NAIROBI, accuracy: 1_200 });
      return page.getByTestId("location-banner").count();
    },
    { timeout: 10_000 },
  ).toBe(1);
  const banner = page.getByTestId("location-banner");
  await expect(banner).toHaveAttribute("data-kind", "approximate");
  await expect(banner).toContainText("Turn on precise location");

  await context.setGeolocation(NAIROBI);
  await expect(page.getByTestId("location-banner")).toHaveCount(0);
});

// --- the custody check (GPS plan U9: R14, R35; F2; AE3, AE4) -----------------------
//
// Seeded morning route "Express 1 — Morning": stop 1 Kilimani (Faith), stop 2
// Lavington (Happiness). The emulated position is moved between the Arrive and
// the Board the way a driver pulls away before tapping.
//
// The plausibility safeguard (GPS plan U12, R32) judges every fix against the
// run's previous one, so the emulated phone has to behave like a phone: it
// stops a few metres off a stop's pin (a fix exactly on the pin is flagged),
// each point carries its own accuracy as a real phone's fixes would, and
// before a long move the trail is aged (sqlAgeTrail) to stand in for the
// minutes a driver takes to pull 1.8 km away — a teleport in seconds is
// exactly what the safeguard flags, and it must not disqualify the Board.

const KILIMANI = { latitude: -1.2902, longitude: 36.7823, accuracy: 20 };
const LAVINGTON = { latitude: -1.2789, longitude: 36.7685, accuracy: 20 };

/** `metres` due north of a point (one degree of latitude is ~111.2 km). */
function northOf(point: typeof KILIMANI, metres: number, accuracy = point.accuracy): typeof KILIMANI {
  return { ...point, latitude: point.latitude + metres / 111_195, accuracy };
}

/** The phone at the stop: 20 m off the pin, its own accuracy. */
const AT_KILIMANI = northOf(KILIMANI, 20, 18);
const AT_LAVINGTON = northOf(LAVINGTON, 20, 19);
/** Where the run starts: 40 m off Kilimani's pin. */
const START_POINT = northOf(KILIMANI, 40, 22);
/** Seconds the earlier fixes are aged by before a long move: 3 km in 200 s
 * is 15 m/s, a bus's pace. */
const PULL_AWAY_S = 200;

/** Records every position the live watch delivered to the app, so a test can
 * wait for an emulated move to land before tapping. */
const FIX_LOG = `
  window.__fixes = [];
  const geo = navigator.geolocation;
  const watch = geo.watchPosition.bind(geo);
  geo.watchPosition = (ok, err, opts) => watch((pos) => {
    window.__fixes.push({ lat: pos.coords.latitude, lng: pos.coords.longitude });
    ok(pos);
  }, err, opts);
`;

/** The attention cue spies (as in driver-nudges.spec): the custody card must
 * neither sound nor vibrate. */
const CUE_SPY = `
  window.__cues = { plays: [], vibrations: [] };
  HTMLMediaElement.prototype.play = function () {
    window.__cues.plays.push({ muted: this.muted });
    return Promise.resolve();
  };
  Object.defineProperty(navigator, "vibrate", {
    configurable: true,
    value: (pattern) => { window.__cues.vibrations.push(pattern); return true; },
  });
`;

async function moveTo(page: Page, context: BrowserContext, point: typeof KILIMANI) {
  await context.setGeolocation(point);
  await expect
    .poll(
      async () => {
        const fixes: { lat: number; lng: number }[] = await page.evaluate(
          () => (window as any).__fixes ?? [],
        );
        const last = fixes[fixes.length - 1];
        return last != null
          && Math.abs(last.lat - point.latitude) < 1e-6
          && Math.abs(last.lng - point.longitude) < 1e-6;
      },
      { timeout: 10_000, message: "the emulated move should reach the live watch" },
    )
    .toBe(true);
}

async function driverContext(request: APIRequestContext) {
  const token = await apiDriverToken(request);
  const response = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function adminExceptions(request: APIRequestContext, runId: string) {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const response = await request.get(`${API_URL}/api/runs/${runId}/exceptions`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function boardFromRow(page: Page, studentId: string): Promise<Captured> {
  const row = page.getByTestId(`student-row-${studentId}`);
  await expect(row.getByTestId(`board-${studentId}`)).toBeEnabled();
  await row.getByTestId(`board-${studentId}`).click();
  const captured = await captureAction(page, "/api/runs/driver/boarding", () =>
    page.getByRole("dialog").getByRole("button", { name: "Board", exact: true }).click(),
  );
  await expect(page.getByRole("dialog")).toHaveCount(0);
  return captured;
}

test("custody: a Board 1.8 km from the stop raises the silent confirm card with the distance; Confirm records it with the bus seen at the stop; Undo from the card and from the board page withdraws the boarding", async ({ page, context, request }) => {
  await page.addInitScript(FIX_LOG);
  await page.addInitScript(CUE_SPY);
  await context.setGeolocation(START_POINT);
  await signInAsDriver(page);
  await startMorningRun(page);
  const ctx = await driverContext(request);
  const runId: string = ctx.active_run.id;
  const faith = ctx.students.find((s: any) => s.name === SEED.parentChild);
  const happiness = ctx.students.find((s: any) => s.name === SEED.afternoonRideMate);
  expect(faith && happiness).toBeTruthy();

  // Arrive at Kilimani with the phone at the stop — the fix "bus seen at
  // stop" reads. Nothing to ask yet.
  await moveTo(page, context, AT_KILIMANI);
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);

  // AE3: the driver pulls away (minutes, by the trail) before tapping Board.
  await page.goto("/driver/boarding");
  sqlAgeTrail(runId, PULL_AWAY_S);
  await moveTo(page, context, northOf(KILIMANI, 1800, 16));
  const boarding = await boardFromRow(page, faith.id);
  expect(boarding.body.fix.lat).toBeCloseTo(northOf(KILIMANI, 1800).latitude, 5);

  const card = page.getByTestId("nudge-card");
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-kind", "custody-away");
  await expect(card).toContainText("Kilimani");
  await expect(card).toContainText(
    `You marked ${SEED.parentChild} boarded about 1.8 km from their stop. Confirm, or undo?`,
  );
  await expect(card.getByTestId("nudge-dismiss")).toHaveCount(0);
  // The tap completed regardless (R23): the row reads On bus and offers Undo.
  const faithRow = page.getByTestId(`student-row-${faith.id}`);
  await expect(faithRow.getByText("On bus")).toBeVisible();
  await expect(faithRow.getByTestId(`undo-${faith.id}`)).toBeVisible();
  // Silent (F2): no tone, no vibration for the routine confirm.
  const cues = await page.evaluate(() => (window as any).__cues);
  expect(cues.plays.filter((p: any) => !p.muted)).toHaveLength(0);
  expect(cues.vibrations).toEqual([]);

  await card.getByTestId("nudge-confirm").click();
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);
  let rows = await adminExceptions(request, runId);
  const custody = rows.filter((x: any) => x.kind === "custody-away");
  expect(custody).toHaveLength(1);
  expect(custody[0].stop_order).toBe(1);
  expect(custody[0].status).toBe("confirmed");
  expect(custody[0].seen_at_stop).toBe(true);
  expect(custody[0].distance_m).toBeGreaterThan(1700);
  expect(custody[0].distance_m).toBeLessThan(1900);
  expect(custody[0].students.map((s: any) => s.id)).toEqual([faith.id]);
  expect(custody[0].events.map((e: any) => [e.prompt_state, e.response, e.student_id])).toEqual([
    ["answered", "confirmed", faith.id],
  ]);
  expect(custody[0].events[0].shown_at).not.toBeNull();
  expect(rows.filter((x: any) => x.kind === "unverified")).toHaveLength(0);
  // A plausible drive so far: nothing for the office to second-guess (U12).
  expect(rows.filter((x: any) => x.kind === "implausible-movement")).toHaveLength(0);

  // Undo from the card (R35): Lavington, the same pattern. The emulated
  // phone teleports 1.6 km to Lavington in seconds — the safeguard flags
  // that Arrive's fix (asserted at the end); the Board after it, made once
  // the trail is aged, is checked normally.
  await page.goto("/driver/run");
  await moveTo(page, context, AT_LAVINGTON);
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/^2\/\d+ stops completed$/)).toBeVisible();
  await page.goto("/driver/boarding");
  sqlAgeTrail(runId, PULL_AWAY_S);
  await moveTo(page, context, northOf(LAVINGTON, 1800, 17));
  await boardFromRow(page, happiness.id);
  await expect(card).toContainText(SEED.afternoonRideMate);
  await card.getByTestId("nudge-undo").click();
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);
  // No outcome: the Board control is back, the Undo gone.
  const happinessRow = page.getByTestId(`student-row-${happiness.id}`);
  await expect(happinessRow.getByTestId(`board-${happiness.id}`)).toBeEnabled();
  await expect(happinessRow.getByTestId(`undo-${happiness.id}`)).toHaveCount(0);
  rows = await adminExceptions(request, runId);
  let lavington = rows.find((x: any) => x.kind === "custody-away" && x.stop_order === 2);
  expect(lavington.status).toBe("retracted");
  expect(lavington.events.map((e: any) => [e.prompt_state, e.response])).toEqual([
    ["answered", "retracted"],
  ]);

  // Undo from the board page: the same reverse path, the same ledger answer.
  // The phone has crept 10 m on: a fresh fix at the same coordinates would
  // read as a repeat, and the second tap must be checked like the first.
  await moveTo(page, context, northOf(LAVINGTON, 1810, 21));
  await boardFromRow(page, happiness.id);
  await expect(card).toContainText(SEED.afternoonRideMate);
  await happinessRow.getByTestId(`undo-${happiness.id}`).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Undo your entry for ${SEED.afternoonRideMate}?`)).toBeVisible();
  await dialog.getByRole("button", { name: "Undo" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);
  await expect(happinessRow.getByTestId(`board-${happiness.id}`)).toBeEnabled();
  rows = await adminExceptions(request, runId);
  lavington = rows.find((x: any) => x.kind === "custody-away" && x.stop_order === 2);
  expect(lavington.status).toBe("retracted");
  expect(lavington.events.map((e: any) => e.response)).toEqual(["retracted", "retracted"]);
  expect(rows.filter((x: any) => x.kind === "custody-away")).toHaveLength(2);
  expect(rows.filter((x: any) => x.kind === "unverified")).toHaveLength(0);

  // The office's second opinion (U12, R32): the one teleport in this run —
  // the Arrive at Lavington, 1.6 km in seconds — is the run's single
  // implausible-movement row, a jump, named no child, and nothing else on
  // the run is listed there.
  const implausible = rows.filter((x: any) => x.kind === "implausible-movement");
  expect(implausible).toHaveLength(1);
  expect(implausible[0].reason.split(",")).toEqual(["jump", "speed"]);
  expect(implausible[0].distance_m).toBeGreaterThan(1000);
  expect(implausible[0].events.map((e: any) => [e.student_id, e.prompt_state])).toEqual([[null, null]]);
});

// --- the remote absent (GPS plan U10: R16, R17, R18, R35; F4; AE6, AE7) -----------
//
// Same seeded route. The driver arrives at Kilimani, pulls 3 km away and marks
// Faith absent: the attestation card. "No — I wasn't at the stop" leaves the
// mark uncorroborated — the family gets the call-now notice once, the office
// its alert. Then Happiness at Lavington, "Yes — they told me": attested,
// history only.

async function absentFromRow(page: Page, studentId: string): Promise<Captured> {
  const row = page.getByTestId(`student-row-${studentId}`);
  await expect(row.getByTestId(`absent-${studentId}`)).toBeEnabled();
  await row.getByTestId(`absent-${studentId}`).click();
  const captured = await captureAction(page, "/api/runs/driver/absent", () =>
    page.getByRole("dialog").getByRole("button", { name: "Mark absent", exact: true }).click(),
  );
  await expect(page.getByRole("dialog")).toHaveCount(0);
  return captured;
}

async function adminIncidents(request: APIRequestContext, runId: string, type: string) {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const response = await request.get(`${API_URL}/api/incidents`, { headers: authHeaders(token) });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).filter((i: any) => i.run_id === runId && i.type === type);
}

test("remote absent: an Absent 3 km from the stop raises the attestation card with tone and vibration; No — I wasn't at the stop sends call-now once and alerts the office; Yes — they told me attests the next one", async ({ page, context, request }) => {
  await page.addInitScript(FIX_LOG);
  await page.addInitScript(CUE_SPY);
  await context.setGeolocation(START_POINT);
  await signInAsDriver(page);
  await startMorningRun(page);
  const ctx = await driverContext(request);
  const runId: string = ctx.active_run.id;
  const faith = ctx.students.find((s: any) => s.name === SEED.parentChild);
  const happiness = ctx.students.find((s: any) => s.name === SEED.afternoonRideMate);
  expect(faith && happiness).toBeTruthy();
  try {
    // Arrive at Kilimani with the phone at the stop; nothing to ask yet.
    await moveTo(page, context, AT_KILIMANI);
    await page.getByRole("button", { name: "Arrive Next Stop" }).click();
    await expect(page.getByText(/^1\/\d+ stops completed$/)).toBeVisible();
    await expect(page.getByTestId("nudge-card")).toHaveCount(0);

    // AE7: 3 km on (minutes, by the trail), the driver marks Faith absent
    // from the board page.
    await page.goto("/driver/boarding");
    sqlAgeTrail(runId, PULL_AWAY_S);
    await moveTo(page, context, northOf(KILIMANI, 3000, 16));
    const marked = await absentFromRow(page, faith.id);
    expect(marked.body.fix.lat).toBeCloseTo(northOf(KILIMANI, 3000).latitude, 5);

    const card = page.getByTestId("nudge-card");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-kind", "absent-remote");
    await expect(card).toContainText("Kilimani");
    await expect(card).toContainText(
      `You marked ${SEED.parentChild} absent about 3.0 km from their stop. `
        + `Did a parent or the office tell you ${SEED.parentChild} isn't coming?`,
    );
    await expect(card.getByTestId("nudge-told-me")).toBeVisible();
    await expect(card.getByTestId("nudge-not-at-stop")).toBeVisible();
    await expect(card.getByTestId("nudge-dismiss")).toBeVisible();
    // The tap completed regardless (R23): the row reads Absent today and offers Undo.
    const faithRow = page.getByTestId(`student-row-${faith.id}`);
    await expect(faithRow.getByText("Absent today")).toBeVisible();
    await expect(faithRow.getByTestId(`undo-${faith.id}`)).toBeVisible();
    // A safety prompt (F4): tone and vibration.
    await expect
      .poll(async () => (await page.evaluate(() => (window as any).__cues)).plays.filter((p: any) => !p.muted).length)
      .toBe(1);
    expect((await page.evaluate(() => (window as any).__cues)).vibrations).toEqual([[200, 100, 200]]);

    await card.getByTestId("nudge-not-at-stop").click();
    await expect(page.getByTestId("nudge-card")).toHaveCount(0);
    let rows = await adminExceptions(request, runId);
    const remote = rows.filter((x: any) => x.kind === "absent-remote");
    expect(remote).toHaveLength(1);
    expect(remote[0].student_id).toBe(faith.id);
    expect(remote[0].stop_order).toBe(1);
    expect(remote[0].status).toBe("uncorroborated");
    expect(remote[0].distance_m).toBeGreaterThan(2900);
    expect(remote[0].distance_m).toBeLessThan(3100);
    expect(remote[0].events.map((e: any) => [e.prompt_state, e.response, e.student_id])).toEqual([
      ["answered", "not-at-stop", faith.id],
    ]);
    expect(remote[0].events[0].shown_at).not.toBeNull();
    expect(remote[0].events[0].call_now_due_at).not.toBeNull();
    // Sent once: the drain rides the answer and every poll.
    await expect
      .poll(async () => {
        const again = await adminExceptions(request, runId);
        return again.find((x: any) => x.kind === "absent-remote")?.events[0]?.call_now_sent_at ?? null;
      }, { timeout: 15_000 })
      .not.toBeNull();
    // The office: one absent-remote alert naming Faith and the stop, no coordinate.
    await expect.poll(async () => (await adminIncidents(request, runId, "absent-remote")).length, { timeout: 15_000 }).toBe(1);
    const alert = (await adminIncidents(request, runId, "absent-remote"))[0];
    expect(alert.description).toContain(SEED.parentChild);
    expect(alert.description).toContain("stop 1");
    expect(alert.description).not.toMatch(/-?1\.2\d{3,}/);

    // The family: the standard notice and the call-now, each once, in the feed.
    await signInAs(page, PARENT);
    await page.goto("/parent/alerts");
    await expect(page.getByText("Call the office now").first()).toBeVisible();
    await expect(page.getByText(/call the school office now/).first()).toBeVisible();
    await expect(page.getByText("Marked absent").first()).toBeVisible();
    const feedCards = page.locator("div[class*='bg-card']");
    expect(await feedCards.filter({ hasText: "call the school office now" }).count()).toBe(1);

    // AE6: back as the driver, Happiness at Lavington, marked from 3 km away,
    // "Yes — they told me": attested, no second alert.
    await signInAsDriver(page);
    await page.goto("/driver/run");
    await moveTo(page, context, AT_LAVINGTON);
    await page.getByRole("button", { name: "Arrive Next Stop" }).click();
    await expect(page.getByText(/^2\/\d+ stops completed$/)).toBeVisible();
    await page.goto("/driver/boarding");
    sqlAgeTrail(runId, PULL_AWAY_S);
    await moveTo(page, context, northOf(LAVINGTON, 3000, 21));
    await absentFromRow(page, happiness.id);
    await expect(card).toHaveAttribute("data-kind", "absent-remote");
    await expect(card).toContainText(SEED.afternoonRideMate);
    await card.getByTestId("nudge-told-me").click();
    await expect(page.getByTestId("nudge-card")).toHaveCount(0);
    rows = await adminExceptions(request, runId);
    const attested = rows.find((x: any) => x.kind === "absent-attested");
    expect(attested).toBeTruthy();
    expect(attested.student_id).toBe(happiness.id);
    expect(attested.status).toBe("attested");
    expect(attested.events.map((e: any) => [e.prompt_state, e.response])).toEqual([["answered", "told-me"]]);
    expect(attested.events[0].call_now_due_at).toBeNull();
    expect(rows.filter((x: any) => x.kind === "absent-remote")).toHaveLength(1);
    expect(await adminIncidents(request, runId, "absent-remote")).toHaveLength(1);
  } finally {
    // The marks outlive the run: clear them so later specs still find both
    // children on the route (the admin clear is refused while a covered run
    // is open, hence the run first).
    await endActiveRun(request);
    await clearCancellationState(request, SEED.parentChild);
    await clearCancellationState(request, SEED.afternoonRideMate);
  }
});
