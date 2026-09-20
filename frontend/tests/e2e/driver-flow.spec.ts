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
  signInAsDriver,
  purgeRun,
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
      purgeRun(run.id);
    }
  }
}

test.afterEach(async ({ request }) => {
  await endActiveRun(request); // never leave an in-progress run behind
  await deleteTodaysRuns(request); // completed runs block same-day restarts
});

/** Start the seeded afternoon route and land on a run in progress. */
async function startAfternoonRun(page: import("@playwright/test").Page) {
  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverAfternoonRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();
}

/**
 * Open the board and wait for a roster that has actually loaded.
 *
 * The page renders "Students (0)" during the context query's first paint, so
 * asserting on the count text alone passes against an empty list and every
 * later action then races the real data.
 */
async function openRoster(page: import("@playwright/test").Page) {
  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \([1-9]\d*\)/)).toBeVisible();
}

/** Arrive every remaining stop, so each child's stop counts as reached. */
async function arriveAllStops(page: import("@playwright/test").Page) {
  await page.goto("/driver/run");
  const arrive = page.getByRole("button", { name: "Arrive Next Stop" });
  const progress = page.getByText(/\d+\/\d+ stops completed/);
  for (let i = 0; i < 12; i++) {
    await expect(progress).toBeVisible();
    if (await arrive.isDisabled()) return;
    const before = await progress.textContent();
    await arrive.click();
    // Waits for the count to actually move rather than predicting it: the test
    // may already have arrived stops of its own before calling this.
    await expect(progress).not.toHaveText(before ?? "");
  }
}

/**
 * Give every child on the roster a recorded outcome.
 *
 * Since U4 a run cannot close while anyone is unaccounted for, so a test that
 * resolves one child and ends the run is asserting a contract the product no
 * longer has.
 */
async function accountForEveryone(
  page: import("@playwright/test").Page,
  action: "Board" | "Drop-off",
) {
  const confirmLabel = action === "Board" ? "Board" : "Drop off";
  for (let i = 0; i < 12; i++) {
    await openRoster(page);
    const next = page.getByRole("button", { name: action, exact: true }).first();
    if ((await next.count()) === 0) return;
    // Wait for enablement rather than filtering on it: the row paints from the
    // context query's first response, and a button read in that window is
    // disabled purely because stops_completed has not arrived yet. Filtering
    // would silently match nothing and leave this child unresolved — which the
    // gate then refuses the run over, several steps later.
    await expect(next).toBeEnabled();
    await next.click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: confirmLabel, exact: true }).click();
    await expect(dialog).toHaveCount(0);
  }
}

test("driver home shows the assigned bus and stat tiles", async ({ page }) => {
  await signInAsDriver(page);
  await expect(page.getByText(/Hello,/)).toBeVisible();
  await expect(page.getByText(SEED.driverBus)).toBeVisible();
  await expect(page.getByText("Stops")).toBeVisible();
  await expect(page.getByText("Students")).toBeVisible();
  await expect(page.getByText("Depart")).toBeVisible();
});

test("morning run: explicit start, confirmed boarding, completed-today lock", async ({ page, request }) => {
  await signInAsDriver(page);

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

  // Boarding asks for confirmation naming the student (R29); once confirmed
  // the row shows the badge and no "Off" toggle — un-boarding stays refused.
  // (Since the GPS work the row offers Undo through the reverse path, the
  // correction that tells the family; that is not a toggle.)
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

  // The gate refuses while anyone is still unaccounted for (U4), so the rest of
  // the roster is boarded before the run can close.
  await arriveAllStops(page);
  await accountForEveryone(page, "Board");

  // Ending the run also requires confirmation (R29).
  await page.goto("/driver/run");
  await expect(page.getByTestId("blocking-list")).toHaveCount(0);
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
  await signInAsDriver(page);

  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverAfternoonRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();

  // Afternoon reword (R31): the primary action is Drop-off and the counter
  // says "Dropped off". The roster was auto-boarded at start (R32).
  await openRoster(page);
  await expect(page.getByText("Dropped off", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Drop-off" }).first()).toBeVisible();

  // School gate stops carry no student, so arrive everything before expecting
  // any drop-off to unlock.
  await arriveAllStops(page);
  await openRoster(page);
  const enabledDrop = page.getByRole("button", { name: "Drop-off", disabled: false });
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
  // The only control left is Undo (U13/R10) — this driver recorded it, and the
  // run is still open. Before U5 there was no way back from a mis-tap at all.
  await expect(row.getByRole("button", { name: "Undo" })).toBeVisible();

  // The gate refuses while anyone is unaccounted for (U13/R11): the run stays
  // in progress and the server's own message names the children.
  await page.goto("/driver/run");
  await expect(page.getByText(/Still to account for \(\d+\)/)).toBeVisible();
  await page.getByRole("button", { name: "End Run" }).click();
  const endDialog = page.getByRole("dialog");
  await expect(endDialog.getByText("End this run?")).toBeVisible();
  await endDialog.getByRole("button", { name: "End Run" }).click();
  await expect(page.getByText(/Not everyone is accounted for/)).toBeVisible();
  await expect(page).toHaveURL(/\/driver\/run/);

  // Resolving the rest empties the list and the same control now closes it.
  await accountForEveryone(page, "Drop-off");
  await page.goto("/driver/run");
  await expect(page.getByTestId("blocking-list")).toHaveCount(0);
  await page.getByRole("button", { name: "End Run" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "End Run" }).click();
  await page.waitForURL("/driver");
});

test("the blocking list names each child and navigates to them", async ({ page }) => {
  // R11: with several blockers a driver would otherwise make a manual
  // multi-screen round trip per child, on a phone, at the end of every route.
  await signInAsDriver(page);
  await startAfternoonRun(page);

  await page.goto("/driver/run");
  const blocking = page.getByTestId("blocking-list");
  await expect(blocking).toBeVisible();
  const firstName = (await blocking.getByRole("button").first().textContent()) ?? "";
  await blocking.getByRole("button").first().click();

  await page.waitForURL(/\/driver\/boarding\?student=/);
  await expect(cardContaining(page, firstName.trim())).toBeVisible();
});

test("absent is offered before the child's stop has been reached", async ({ page }) => {
  // R8: it used to require the stop to have been reached, which left the driver
  // of a child who was never at the stop with nothing to tap — and the gate then
  // refused to close the run over exactly that child.
  await signInAsDriver(page);
  await startAfternoonRun(page);

  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  // No stop has been arrived at yet, so Drop-off is disabled everywhere.
  await expect(page.getByRole("button", { name: "Drop-off" }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "Absent" }).first()).toBeEnabled();
});

test("a hand-over cannot be confirmed without a note", async ({ page }) => {
  // R9: "left the bus" without where or why is not an account of anything, and
  // the driver's only other release would be marking the child absent — which
  // tells the family they were never on the bus home.
  await signInAsDriver(page);
  await startAfternoonRun(page);

  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  const target = page
    .locator("div[class*='bg-card']")
    .filter({ has: page.getByRole("button", { name: "Off-route" }) })
    .first();
  const studentName = ((await target.locator("p").first().textContent()) ?? "").trim();
  await target.getByRole("button", { name: "Off-route" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`${studentName} left the bus off-route?`)).toBeVisible();
  const record = dialog.getByRole("button", { name: "Record hand-over" });
  await expect(record).toBeDisabled();

  await dialog.getByTestId("confirm-note").fill("Collected by an aunt at the junction");
  await expect(record).toBeEnabled();
  await record.click();
  await expect(dialog).toHaveCount(0);

  // Accounted for, and the row now offers the undo rather than another release.
  const row = cardContaining(page, studentName);
  await expect(row.getByRole("button", { name: "Undo" })).toBeVisible();
});

test("no driver status badge renders a raw slug", async ({ page }) => {
  // R3: the board reads the derived status, which carries values the raw
  // column never held ('expected on bus'), and every one of them is labelled.
  await signInAsDriver(page);
  await startAfternoonRun(page);

  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  await expect(page.getByText("Expected on bus").first()).toBeVisible();
  const body = (await page.locator("main").textContent()) ?? "";
  for (const slug of ["expected-on-bus", "on-bus", "at-school", "dropped-off", "unaccounted"]) {
    expect(body).not.toContain(slug);
  }
});

test("driver can report an incident", async ({ page }) => {
  await signInAsDriver(page);
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
  await signInAsDriver(page);
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
    await signInAsDriver(page);
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
