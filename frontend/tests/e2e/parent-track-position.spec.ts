import { expect, test, type APIRequestContext } from "@playwright/test";
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
  sqlAgeBusPosition,
  sqlClearBusPosition,
} from "./helpers";

// The position read model on both maps (GPS plan U8: R7, R9, R20's derived
// GPS-off marker, R27's derivation half, R37; AE11, AE13's Phase 1 half, F5).
//
// The driver's taps go through the API so each spec controls the fix it
// sends; the maps are read in the browser. Seeded morning route "Express 1 —
// Morning" on Simba (driver PIN 0322): Kilimani, Lavington, Karen, the gate.

// Every step below may legitimately wait out one 5 s live poll, and a few
// wait out two.
test.describe.configure({ timeout: 120_000 });

const NAIROBI = { lat: -1.2921, lng: 36.8219 };
const FIX_A = { lat: -1.2931, lng: 36.781, accuracy_m: 9 };
const FIX_B = { lat: -1.299, lng: 36.765, accuracy_m: 14 };

/** An aware ISO capture time, the client's shape ("+00:00" rather than "Z"). */
function capturedAt(msAgo = 0): string {
  return new Date(Date.now() - msAgo).toISOString().replace("Z", "+00:00");
}

async function driverContext(request: APIRequestContext) {
  const token = await apiDriverToken(request);
  const response = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Start the seeded morning route through the driver API with the given fix. */
async function startMorningRun(request: APIRequestContext, fix: unknown): Promise<string> {
  const token = await apiDriverToken(request);
  const context = await driverContext(request);
  const route = context.routes.find((r: any) => r.type === "morning");
  expect(route, "the seeded driver bus has a morning route").toBeTruthy();
  const started = await request.post(`${API_URL}/api/runs/driver/start`, {
    headers: { ...authHeaders(token), "Idempotency-Key": crypto.randomUUID() },
    data: { route_id: route.id, device_id: DEVICE_ID, fix },
  });
  expect(started.ok(), await started.text()).toBeTruthy();
  return (await started.json()).id;
}

const DEVICE_ID = crypto.randomUUID();

async function arrive(request: APIRequestContext, runId: string, expected: number, fix: unknown) {
  const token = await apiDriverToken(request);
  const response = await request.post(`${API_URL}/api/runs/driver/arrive`, {
    headers: { ...authHeaders(token), "Idempotency-Key": crypto.randomUUID() },
    data: { run_id: runId, expected_stop_order: expected, device_id: DEVICE_ID, fix },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = await response.json();
  expect(body.noop).toBe(false);
}

/** Delete today's runs for the driver's bus (completed runs block same-day
 * restarts; a purged run cascades its trail rows) and clear the bus's pair. */
async function resetDriverBus(request: APIRequestContext) {
  await endActiveRun(request);
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

async function staffPosition(request: APIRequestContext, busId: string) {
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const buses = await request.get(`${API_URL}/api/fleet/buses`, { headers: authHeaders(adminToken) });
  expect(buses.ok()).toBeTruthy();
  return (await buses.json()).find((b: any) => b.id === busId)?.position ?? null;
}

test.beforeEach(async ({ request }) => {
  await resetDriverBus(request);
  // Faith must be on the run: an absence left by an earlier spec would drop
  // her from the roster snapshot and leave her parent nothing to track.
  await clearCancellationState(request, SEED.parentChild);
});

test.afterEach(async ({ request }) => {
  await resetDriverBus(request);
});

test("AE11: the fleet map moves the dot to an Arrive fix within one poll with the tap time as freshness; the marker survives polls; stale dims it and a new fix clears it (AE13, Phase 1)", async ({ page, request }) => {
  const busId = (await driverContext(request)).bus.id;
  const runId = await startMorningRun(request, { ...NAIROBI, accuracy_m: 25, captured_at: capturedAt() });

  await signInAs(page, ADMIN);
  await page.goto("/fleet-map");
  await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });
  const marker = page.locator(`[data-testid="bus-marker"][data-bus-id="${busId}"]`);
  await expect(marker).toBeVisible({ timeout: 15_000 });
  await expect(marker).toHaveAttribute("data-stale", "false");
  await expect(marker).toHaveAttribute("data-gps-off", "false");
  const markerNode = await marker.elementHandle();
  const markerCount = await page.getByTestId("bus-marker").count();

  // The school checkpoint reads as a planned stop in the info window.
  await marker.click();
  const info = page.getByTestId("bus-info-window");
  await expect(info).toBeVisible();
  await expect(info.getByTestId("bus-info-label")).toHaveText("Starting — at school");
  await expect(info.getByTestId("bus-info-source")).toContainText("Planned stop");

  // Arrive with a fix captured 20 s before the tap: within one poll the dot
  // is the fix and the freshness counts from the CAPTURE time.
  await arrive(request, runId, 1, { ...FIX_A, captured_at: capturedAt(20_000) });
  await expect(info.getByTestId("bus-info-label")).toHaveText("Phone GPS", { timeout: 15_000 });
  await expect(info.getByTestId("bus-info-source")).toContainText("Phone GPS (tap)");
  await expect(info.getByTestId("bus-info-source")).toContainText("±9 m");
  await expect(info.getByTestId("bus-info-freshness")).toHaveText(/^updated [2-5]\d s ago$/);
  await expect(info.getByTestId("bus-info-freshness")).toHaveAttribute("data-stale", "false");
  await expect(info.getByTestId("bus-info-gps")).toHaveCount(0);
  await expect(page.getByTestId(`bus-row-${busId}`)).toContainText("Phone GPS");
  await expect(page.getByTestId(`bus-row-${busId}`).getByTestId("bus-row-freshness")).toHaveText(/^updated /);
  // The served position is the fix (the same object the parent reads).
  const served = await staffPosition(request, busId);
  expect(served).toMatchObject({ lat: FIX_A.lat, lng: FIX_A.lng, source: "action", accuracy_m: 9 });

  // Only the position changed: the marker count is the same and the glyph is
  // the same DOM node — no marker was recreated by the poll.
  expect(await page.getByTestId("bus-marker").count()).toBe(markerCount);
  expect(await markerNode!.evaluate((el) => el.isConnected)).toBe(true);

  // Past the staleness threshold the glyph dims and the wording turns to
  // "last seen"; the position stays on the map (never hidden during a run).
  sqlAgeBusPosition(busId, 300);
  await expect(marker).toHaveAttribute("data-stale", "true", { timeout: 15_000 });
  await expect(info.getByTestId("bus-info-freshness")).toHaveText("last seen 5 min ago");
  await expect(info.getByTestId("bus-info-freshness")).toHaveAttribute("data-stale", "true");
  await expect(marker).toBeVisible();
  expect(await markerNode!.evaluate((el) => el.isConnected)).toBe(true);

  // A new fix clears it.
  await arrive(request, runId, 2, { ...FIX_B, captured_at: capturedAt() });
  await expect(marker).toHaveAttribute("data-stale", "false", { timeout: 15_000 });
  await expect(info.getByTestId("bus-info-freshness")).toHaveText(/^updated (just now|\d+ s ago)$/);
  await expect(info.getByTestId("bus-info-source")).toContainText("±14 m");
  expect(await page.getByTestId("bus-marker").count()).toBe(markerCount);
  expect(await markerNode!.evaluate((el) => el.isConnected)).toBe(true);
});

test("F5/AE2: a run whose taps carry no fix shows the GPS-off marker and 'no GPS for this run'; the first fix clears both", async ({ page, request }) => {
  const busId = (await driverContext(request)).bus.id;
  const runId = await startMorningRun(request, { reason: "denied" });
  await arrive(request, runId, 1, { reason: "denied" });

  await signInAs(page, ADMIN);
  await page.goto("/fleet-map");
  await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });
  const marker = page.locator(`[data-testid="bus-marker"][data-bus-id="${busId}"]`);
  await expect(marker).toBeVisible({ timeout: 15_000 });
  await expect(marker).toHaveAttribute("data-gps-off", "true");
  await marker.click();
  const info = page.getByTestId("bus-info-window");
  await expect(info.getByTestId("bus-info-gps")).toHaveText(/No GPS for this run/);
  await expect(info.getByTestId("bus-info-label")).toHaveText(/^At .+ · en route to next$/);
  await expect(info.getByTestId("bus-info-source")).toContainText("Planned stop");

  // The first fix clears both, with nothing written but the tap.
  await arrive(request, runId, 2, { ...FIX_A, captured_at: capturedAt() });
  await expect(marker).toHaveAttribute("data-gps-off", "false", { timeout: 15_000 });
  await expect(info.getByTestId("bus-info-gps")).toHaveCount(0);
  await expect(info.getByTestId("bus-info-label")).toHaveText("Phone GPS");
  const served = await staffPosition(request, busId);
  expect(served).toMatchObject({ gps_off: false, no_gps_for_run: false, source: "action" });
});

test.describe("parent Track at phone width", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("R9/R37: the parent map plots the bus with 'updated X ago' beside the live badge, dims it when stale, and serves the allowlist only", async ({ page, request }) => {
    const busId = (await driverContext(request)).bus.id;
    const runId = await startMorningRun(request, { ...NAIROBI, accuracy_m: 25, captured_at: capturedAt() });
    await arrive(request, runId, 1, { ...FIX_A, captured_at: capturedAt(15_000) });

    await signInAs(page, PARENT);
    await page.goto("/parent/track");
    // Ben Barasa (school B) sorts first and becomes the default child; the
    // run is Faith's.
    await page.getByRole("combobox").first().click();
    await page.getByRole("option", { name: SEED.parentChild }).click();
    await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });

    const marker = page.getByTestId("bus-marker");
    await expect(marker).toHaveCount(1, { timeout: 15_000 });
    await expect(marker).toHaveAttribute("data-stale", "false");
    await expect(page.getByText(`${SEED.driverBus} is live`)).toBeVisible();
    const freshness = page.getByTestId("track-freshness");
    await expect(freshness).toHaveText(/^updated (just now|\d+ s ago)$/);
    await expect(freshness).toHaveAttribute("data-stale", "false");
    // No source, accuracy or exception wording anywhere on the parent page.
    await expect(page.getByText(/Phone GPS|Planned stop|±\d+ m|GPS off|No GPS/)).toHaveCount(0);

    // Parity through the API: the same coordinates and time the staff list
    // serves, as exactly {lat, lng, position_at, stale}, on Track and on the
    // children row.
    const parentToken = await apiToken(request, PARENT.email, PARENT.password);
    const children = await (
      await request.get(`${API_URL}/api/parent-portal/children`, { headers: authHeaders(parentToken) })
    ).json();
    const faith = children.find((c: any) => c.name === SEED.parentChild);
    const track = await (
      await request.get(`${API_URL}/api/parent-portal/track?student_id=${faith.id}`, {
        headers: authHeaders(parentToken),
      })
    ).json();
    const staff = await staffPosition(request, busId);
    expect(Object.keys(track.bus_position).sort()).toEqual(["lat", "lng", "position_at", "stale"]);
    expect(track.bus_position).toEqual({
      lat: staff.lat, lng: staff.lng, position_at: staff.position_at, stale: staff.stale,
    });
    expect(track.bus_position.lat).toBe(FIX_A.lat);
    expect(faith.bus_position).toEqual(track.bus_position);
    expect(track.bus).toEqual({ id: busId, name: SEED.driverBus });
    expect(staff.source).toBe("action");
    expect(JSON.stringify(track)).not.toMatch(/"(source|accuracy_m|age_s|gps_off|no_gps_for_run|bus_current_lat)"/);
    expect(JSON.stringify(children)).not.toMatch(/"(source|accuracy_m|age_s|gps_off|no_gps_for_run|bus_current_lat)"/);

    // Stale: the same dimming and "last seen" wording as the fleet map; the
    // live badge stays (the position is never hidden during a run).
    sqlAgeBusPosition(busId, 240);
    await expect(freshness).toHaveText("last seen 4 min ago", { timeout: 15_000 });
    await expect(freshness).toHaveAttribute("data-stale", "true");
    await expect(marker).toHaveAttribute("data-stale", "true");
    await expect(marker).toHaveCount(1);
    await expect(page.getByText(`${SEED.driverBus} is live`)).toBeVisible();

    // A new fix clears it within one poll.
    await arrive(request, runId, 2, { ...FIX_B, captured_at: capturedAt() });
    await expect(freshness).toHaveText(/^updated (just now|\d+ s ago)$/, { timeout: 15_000 });
    await expect(marker).toHaveAttribute("data-stale", "false");
    await expect(marker).toHaveCount(1);
  });
});
