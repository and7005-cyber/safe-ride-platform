import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  SEED,
  apiDriverToken,
  apiToken,
  authHeaders,
  endActiveRun,
  purgeRun,
  signInAsDriver,
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
