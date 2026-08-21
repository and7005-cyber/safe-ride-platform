import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  SEED,
  apiDriverToken,
  authHeaders,
  backdateRun,
  cardContaining,
  endActiveRun,
  purgeRun,
  signInAs,
} from "./helpers";

// The admin Dashboard as a live status board. Both journeys came from one field
// report: a driver boarded everyone and ended the run, and the office, watching
// the Dashboard, still saw the bus on an active run.
//
// The seed itself ships a run left open on 2026-06-16 for the same bus and
// route (backend/db/seeds/003_local_snapshot.sql). It sits on the Active Runs
// card flagged "Needs closing", so every row locator below tells the run it
// watches apart from that one instead of deleting seed data.

// Each assertion below may legitimately wait out a full 15s admin poll cycle,
// and the first journey waits out two.
test.describe.configure({ timeout: 90_000 });

/** "Today" in the backend's run calendar is Africa/Nairobi (UTC+3). */
function nairobiDate(offsetDays = 0): string {
  return new Date(Date.now() + (3 + offsetDays * 24) * 3600 * 1000).toISOString().slice(0, 10);
}

async function driverContext(request: APIRequestContext) {
  const token = await apiDriverToken(request);
  const response = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Start the seeded morning route through the driver API. */
async function startMorningRun(request: APIRequestContext): Promise<string> {
  const token = await apiDriverToken(request);
  const context = await driverContext(request);
  const route = context.routes.find((r: any) => r.type === "morning");
  expect(route, "the seeded driver bus has a morning route").toBeTruthy();
  const started = await request.post(`${API_URL}/api/runs/driver/start`, {
    headers: authHeaders(token),
    data: { route_id: route.id },
  });
  expect(started.ok(), await started.text()).toBeTruthy();
  return (await started.json()).id;
}

/**
 * The driver's own closing path: arrive every stop, board each child on the
 * roster, end the run. The closure gate refuses anything less (U4), and a test
 * that closed the run any other way would not be the reporter's flow.
 */
async function boardEveryoneAndEnd(request: APIRequestContext, runId: string): Promise<void> {
  const token = await apiDriverToken(request);
  const headers = authHeaders(token);
  let context = await driverContext(request);
  for (let i = context.active_run.stops_completed; i < context.active_run.total_stops; i++) {
    const arrived = await request.post(`${API_URL}/api/runs/driver/arrive`, {
      headers, data: { run_id: runId },
    });
    expect(arrived.ok(), await arrived.text()).toBeTruthy();
  }
  context = await driverContext(request);
  for (const student of context.students) {
    const boarded = await request.post(`${API_URL}/api/runs/driver/boarding`, {
      headers, data: { student_id: student.id, on_bus: true },
    });
    expect(boarded.ok(), await boarded.text()).toBeTruthy();
  }
  const ended = await request.post(`${API_URL}/api/runs/driver/end`, {
    headers, data: { run_id: runId },
  });
  expect(ended.ok(), await ended.text()).toBeTruthy();
}

test.afterEach(async ({ request }) => {
  await endActiveRun(request); // never leave an in-progress run behind
});

test("fleet status follows the run ending while the dashboard stays open", async ({ page, request }) => {
  const runId = await startMorningRun(request);

  await signInAs(page, ADMIN);
  await expect(page).toHaveURL("/");
  // Today's run is the bus's unflagged row; the seeded stale one carries the flag.
  const runRow = cardContaining(page, "Active Runs")
    .locator("div.rounded-lg.border")
    .filter({ hasText: `${SEED.driverBus} · ${SEED.driverMorningRoute}` })
    .filter({ hasNotText: "Needs closing" });
  const fleetRow = cardContaining(page, "Fleet Status")
    .locator("div.flex.items-center.justify-between")
    .filter({ hasText: SEED.driverBus });
  const busStatus = fleetRow.getByText(/^(Active|Idle|Delayed|Out of service)$/);
  const activeBuses = cardContaining(page, "Active Buses").locator("p.text-3xl");

  await expect(runRow).toBeVisible();
  await expect(busStatus).toHaveText("Active");
  const before = Number(await activeBuses.textContent());

  // The office keeps the page open; nothing is reloaded or refocused from here.
  await boardEveryoneAndEnd(request, runId);

  // The card already polled, so the run leaves it within one cadence...
  await expect(runRow).toHaveCount(0, { timeout: 25_000 });
  // ...and the bus line and the tile, which derive from the same server state,
  // must agree with it instead of holding the pre-run answer until a reload.
  await expect(busStatus).toHaveText("Idle", { timeout: 25_000 });
  await expect(activeBuses).toHaveText(String(before - 1));
});

test("a run left open on a previous day is flagged on the dashboard as needing closing", async ({ page, request }) => {
  const runId = await startMorningRun(request);
  try {
    backdateRun(runId);

    await signInAs(page, ADMIN);
    // Told apart from the seeded stale run by the date it was left open on —
    // the one fact that distinguishes it from a run happening now.
    const row = cardContaining(page, "Active Runs")
      .locator("div.rounded-lg.border")
      .filter({ hasText: SEED.driverBus })
      .filter({ hasText: nairobiDate(-1) });
    // Listed on purpose (R15): a run that outlived its service day is invisible
    // to every driver path, and the office is who closes it.
    await expect(row).toBeVisible();
    // But it must not read like a run in progress on the bus right now: the Runs
    // page flags it, and the Dashboard is where the office actually looks.
    await expect(row.getByText("Needs closing")).toBeVisible();
    await expect(row.getByRole("link", { name: "close it in Run History" })).toHaveAttribute("href", "/runs");
  } finally {
    // Backdated, so neither the driver nor endActiveRun can reach it.
    purgeRun(runId);
  }
});
