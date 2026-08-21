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
  purgeStaleRuns,
  signInAs,
} from "./helpers";

// The admin Dashboard as a live status board. From a field report: a driver
// boarded everyone and ended the run, and the office, watching the Dashboard,
// still saw the bus on an active run.

// An assertion may legitimately wait out a full 15s admin poll cycle.
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
async function startMorningRun(request: APIRequestContext): Promise<{ runId: string; busId: string }> {
  const token = await apiDriverToken(request);
  const context = await driverContext(request);
  const route = context.routes.find((r: any) => r.type === "morning");
  expect(route, "the seeded driver bus has a morning route").toBeTruthy();
  const started = await request.post(`${API_URL}/api/runs/driver/start`, {
    headers: authHeaders(token),
    data: { route_id: route.id },
  });
  expect(started.ok(), await started.text()).toBeTruthy();
  return { runId: (await started.json()).id, busId: context.bus.id };
}

test.beforeAll(async ({ request }) => {
  // A leftover from an aborted session shares the watched run's bus and route
  // name on the Active Runs card, so the card's locators would stop being
  // unique. Hygiene only — the journey plants its own stale run.
  purgeStaleRuns((await driverContext(request)).bus.id);
});

test.afterEach(async ({ request }) => {
  await endActiveRun(request); // never leave an in-progress run behind
});

test("a run left open on a previous day is flagged on the dashboard as needing closing", async ({ page, request }) => {
  const { runId } = await startMorningRun(request);
  try {
    backdateRun(runId);

    await signInAs(page, ADMIN);
    const row = cardContaining(page, "Active Runs")
      .locator("div.rounded-lg.border")
      .filter({ hasText: SEED.driverBus });
    // Listed on purpose (R15): a run that outlived its service day is invisible
    // to every driver path, and the office is who closes it.
    await expect(row).toBeVisible();
    // But it must not read like a run in progress on the bus right now: the Runs
    // page flags it, and the Dashboard is where the office actually looks.
    await expect(row.getByText("Needs closing")).toBeVisible();
    await expect(row.getByText(nairobiDate(-1))).toBeVisible();
  } finally {
    // Backdated, so neither the driver nor endActiveRun can reach it.
    purgeRun(runId);
  }
});
