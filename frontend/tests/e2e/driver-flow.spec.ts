import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  DRIVER,
  SEED,
  apiCancelRide,
  apiDriverToken,
  apiToken,
  authHeaders,
  cardContaining,
  clearCancellationState,
  endActiveRun,
  pinLogin,
} from "./helpers";

// Driver journey: PIN login, explicit run starts (R27/R28), confirmed final
// boarding/drop-off actions (R29/R31), incident report.

/** Delete today's runs for the driver's bus (mirrors the integration
 * `no_active_run` fixture): completed runs block same-day restarts (R28), so
 * merely ending a run would poison every later start in the suite. */
async function deleteTodaysRuns(request: APIRequestContext) {
  const driverToken = await apiDriverToken(request);
  const context = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(driverToken),
  });
  if (!context.ok()) return;
  const busId = (await context.json()).bus?.id;
  if (!busId) return;
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  // "Today" in the backend's run calendar is Africa/Nairobi (UTC+3).
  const today = new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const runs = await request.get(`${API_URL}/api/runs`, { headers: authHeaders(adminToken) });
  for (const run of await runs.json()) {
    if (run.bus_id === busId && String(run.date).startsWith(today)) {
      await request.delete(`${API_URL}/api/runs/${run.id}`, { headers: authHeaders(adminToken) });
    }
  }
}

test.afterEach(async ({ request }) => {
  await endActiveRun(request); // never leave an in-progress run behind
  await deleteTodaysRuns(request); // completed runs block same-day restarts
});

test("driver home shows the assigned bus and stat tiles", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await expect(page.getByText(/Hello,/)).toBeVisible();
  await expect(page.getByText(SEED.driverBus)).toBeVisible();
  await expect(page.getByText("Stops")).toBeVisible();
  await expect(page.getByText("Students")).toBeVisible();
  await expect(page.getByText("Depart")).toBeVisible();
});

test("morning run: explicit start, confirmed boarding, completed-today lock", async ({ page, request }) => {
  await pinLogin(page, DRIVER.pin);

  // The home tile never starts a run — it routes to the Run page (R27).
  await page.getByRole("button", { name: "Start Run" }).click();
  await page.waitForURL("/driver/run");

  // No auto-selected route: Start stays disabled until an explicit choice.
  await expect(page.getByRole("button", { name: "Start Run" })).toBeDisabled();
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverMorningRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();

  // No device GPS: starting a run pins the bus at the school, and each arrival
  // moves it to that stop. The bus becomes live on the admin fleet map.
  await expect
    .poll(
      async () => {
        const token = await apiDriverToken(request);
        const context = await request.get(`${API_URL}/api/runs/driver/context`, {
          headers: authHeaders(token),
        });
        return (await context.json()).bus?.current_lat;
      },
      { timeout: 15_000, message: "starting a run should pin the bus at the school" },
    )
    .not.toBeNull();

  // Reach the first stop, then board a student there.
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/1\/\d+ stops completed/)).toBeVisible();

  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  const boardButton = page.getByRole("button", { name: "Board", exact: true }).first();
  await expect(boardButton).toBeEnabled();
  await boardButton.click();

  // Boarding asks for confirmation naming the student, and is final (R29):
  // once confirmed the row shows a static badge — no Off/undo control.
  const boardDialog = page.getByRole("dialog");
  await expect(boardDialog.getByText(/Board .+\?/)).toBeVisible();
  await boardDialog.getByRole("button", { name: "Board", exact: true }).click();
  await expect(boardDialog).toHaveCount(0);
  await expect(page.getByText("On bus").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Off", exact: true })).toHaveCount(0);

  // Boarded counter moved off zero (the count renders just above its label).
  await expect(
    page.getByText("Boarded", { exact: true }).locator("xpath=preceding-sibling::span[1]"),
  ).not.toHaveText("0");

  // Ending the run also requires confirmation (R29).
  await page.goto("/driver/run");
  await page.getByRole("button", { name: "End Run" }).click();
  const endDialog = page.getByRole("dialog");
  await expect(endDialog.getByText("End this run?")).toBeVisible();
  await endDialog.getByRole("button", { name: "End Run" }).click();
  await page.waitForURL("/driver");

  // Before cleanup deletes today's runs: the completed route is locked for the
  // rest of the day — its option renders disabled with a hint (R28, AE8).
  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  const completedOption = page.getByRole("option", { name: SEED.driverMorningRoute });
  await expect(completedOption).toContainText("Completed today");
  await expect(completedOption).toHaveAttribute("aria-disabled", "true");
});

test("afternoon run: drop-off language and a confirmed, final drop-off", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);

  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverAfternoonRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();

  // Afternoon reword (R31): the primary action is Drop-off and the counter
  // says "Dropped off". The roster was auto-boarded at start (R32).
  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  await expect(page.getByText("Dropped off", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Drop-off" }).first()).toBeVisible();

  // Tap Arrive until some student's stop is reached (school gate stops carry
  // no student, so the first arrival may not unlock anything).
  const enabledDrop = page.getByRole("button", { name: "Drop-off", disabled: false });
  for (let arrivals = 1; arrivals <= 10; arrivals++) {
    await page.goto("/driver/run");
    const arrive = page.getByRole("button", { name: "Arrive Next Stop" });
    if (await arrive.isDisabled()) break;
    await arrive.click();
    await expect(page.getByText(new RegExp(`${arrivals}/\\d+ stops completed`))).toBeVisible();
    await page.goto("/driver/boarding");
    await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
    if ((await enabledDrop.count()) > 0) break;
  }
  await expect(enabledDrop.first()).toBeVisible();

  // Confirm one drop-off; the row flips to a static badge with no actions.
  // (Pin the row by student name first — the enabled-button filter stops
  // matching once the drop-off lands.)
  const actionable = page
    .locator("div[class*='bg-card']")
    .filter({ has: page.getByRole("button", { name: "Drop-off", disabled: false }) })
    .first();
  const studentName = (await actionable.locator("p").first().textContent()) ?? "";
  await actionable.getByRole("button", { name: "Drop-off" }).click();
  const dropDialog = page.getByRole("dialog");
  await expect(dropDialog.getByText(`Drop off ${studentName}?`)).toBeVisible();
  await dropDialog.getByRole("button", { name: "Drop off" }).click();
  await expect(dropDialog).toHaveCount(0);
  const row = cardContaining(page, studentName);
  await expect(row.getByText("Dropped off")).toBeVisible();
  await expect(row.getByRole("button")).toHaveCount(0);

  // End Run confirmation lists students not yet confirmed dropped off (R29).
  await page.goto("/driver/run");
  await page.getByRole("button", { name: "End Run" }).click();
  const endDialog = page.getByRole("dialog");
  await expect(endDialog.getByText("End this run?")).toBeVisible();
  await expect(endDialog.getByText(/Not yet confirmed dropped off:/)).toBeVisible();
  await endDialog.getByRole("button", { name: "End Run" }).click();
  await page.waitForURL("/driver");
});

test("driver can report an incident", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await page.goto("/driver/incident");
  await expect(page.getByText("New Incident Report")).toBeVisible();

  // Pick a type, describe, submit.
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "Heavy Traffic / Delay" }).click();
  await page.getByPlaceholder("Describe what happened…").fill("E2E traffic report from the driver flow spec.");
  await page.getByRole("button", { name: "Submit Report" }).click();
  await expect(page.getByText("Incident reported").first()).toBeVisible();
});

test("search filters the boarding list", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();

  await page.getByPlaceholder("Search students…").fill(SEED.parentChildSearch);
  await expect(page.getByText(SEED.parentChild)).toBeVisible();
  await expect(page.getByText(/Students \(1\)/)).toBeVisible();
});

// Cancel-a-Ride on the driver surface (U14: R16, R19; AE4): a pre-run
// afternoon cancellation keeps the child's stop out of the run snapshot, so
// the auto-boarded roster never carries her.
test("an afternoon cancellation excludes the student from the driver's run", async ({
  page,
  request,
}) => {
  await apiCancelRide(request, SEED.parentChild, "afternoon");
  try {
    await pinLogin(page, DRIVER.pin);
    await page.goto("/driver/run");
    await page.getByRole("combobox").click();
    await page.getByRole("option", { name: SEED.driverAfternoonRoute }).click();
    await page.getByRole("button", { name: "Start Run" }).click();
    await expect(page.getByText("Run in progress")).toBeVisible();

    // With an active run the boarding list is the RUN's roster: the cancelled
    // child was excluded at start (not merely flagged) — start_run dropped her
    // stop before the snapshot, and the afternoon auto-board skipped her.
    await page.goto("/driver/boarding");
    await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
    await expect(page.getByText(SEED.afternoonRideMate)).toBeVisible();
    await expect(page.getByText(SEED.parentChild)).toHaveCount(0);
    await page.getByPlaceholder("Search students…").fill(SEED.parentChildSearch);
    await expect(page.getByText(/Students \(0\)/)).toBeVisible();
  } finally {
    // End the run BEFORE clearing: clear_absence 409s ("End the run first")
    // while an active covered run exists on the student's route, even though
    // she was excluded from its snapshot. afterEach repeats the run cleanup
    // idempotently; the absence row and office alert are this journey's own.
    await endActiveRun(request);
    await clearCancellationState(request, SEED.parentChild);
  }
});
