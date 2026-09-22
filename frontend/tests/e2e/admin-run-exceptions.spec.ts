import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  COORDINATOR_A,
  SCHOOL_A_ID,
  SEED,
  apiDriverToken,
  authHeaders,
  cardContaining,
  endActiveRun,
  purgeRun,
  signInAs,
  sqlUnreviewException,
} from "./helpers";

// The staff exception surfaces (GPS plan U4: R21, R22).
//
// The seed puts one exception of every kind on the historical Greenfield run
// of 2026-06-17 (bus Simba, "Express 1 — Morning"): the custody one is
// reviewed, the other five are not (backend/db/seeds/003_local_snapshot.sql
// tail). That run is the canvas for the panel; a live run started here is
// the canvas for the badge agreeing between the Dashboard and Run History.

const SEEDED_RUN_DATE = "2026-06-17";
const SEEDED = {
  custody: "72160000-0000-0000-0000-000000000001",
  bypassed: "72160000-0000-0000-0000-000000000002",
  absentRemote: "72160000-0000-0000-0000-000000000003",
  absentAttested: "72160000-0000-0000-0000-000000000004",
  unverified: "72160000-0000-0000-0000-000000000005",
  implausible: "72160000-0000-0000-0000-000000000006",
};

function dialog(page: Page) {
  return page.getByRole("dialog");
}

/** The Run History row of the seeded run — the one of its day that carries
 * the badge (a second, exception-free run shares the date). */
function seededRow(page: Page) {
  return page
    .getByRole("row", { name: new RegExp(SEEDED_RUN_DATE) })
    .filter({ has: page.getByTestId("exceptions-to-review") });
}

async function openSeededReport(page: Page) {
  await page.goto("/runs");
  const row = seededRow(page);
  await expect(row).toHaveCount(1);
  await row.getByRole("cell", { name: SEEDED_RUN_DATE }).click();
  const report = dialog(page);
  await expect(report.getByText("Run Report")).toBeVisible();
  await expect(report.getByTestId("run-exceptions")).toBeVisible();
  return report;
}

test("the run report lists every kind of stop exception in plain words", async ({ page }) => {
  await signInAs(page, ADMIN, SCHOOL_A_ID);
  await page.goto("/runs");

  // Five of the six seeded rows await the office; the badge says so.
  await expect(seededRow(page).getByTestId("exceptions-to-review")).toContainText("5 to review");

  const report = await openSeededReport(page);
  const panel = report.getByTestId("run-exceptions");
  await expect(panel.getByTestId("run-exceptions-to-review")).toContainText("5 to review");

  // All six kinds render, each labelled — no raw slug reaches the office.
  for (const [id, label] of [
    [SEEDED.custody, "Tap far from the stop"],
    [SEEDED.bypassed, "Stop passed without outcomes"],
    [SEEDED.absentRemote, "Absent marked away from the stop"],
    [SEEDED.absentAttested, "Absent attested by the driver"],
    [SEEDED.unverified, "Check not verified"],
    [SEEDED.implausible, "Implausible movement"],
  ] as const) {
    await expect(panel.getByTestId(`exception-${id}`)).toContainText(label);
  }
  await expect(panel).not.toContainText(/custody-away|stop-bypassed|absent-remote|too-coarse/);

  // The custody tap: the fix beside its accuracy, the distance, seen-at-stop,
  // the driver's answer, and the seeded review stamp with who and when.
  const custody = panel.getByTestId(`exception-${SEEDED.custody}`);
  await expect(custody).toContainText("Happiness Kenesa");
  await expect(custody).toContainText(/Phone reported within 20 m at \d\d:\d\d/);
  await expect(custody).toContainText("2.3 km from the stop");
  await expect(custody).toContainText("Bus seen at stop: no");
  await expect(custody).toContainText("prompt answered: confirmed by the driver");
  await expect(custody.getByTestId(`reviewed-${SEEDED.custody}`)).toContainText("Reviewed");
  await expect(custody.getByTestId(`reviewed-${SEEDED.custody}`)).toContainText("by Dora Director");
  await expect(custody.getByRole("button", { name: "Mark reviewed" })).toHaveCount(0);

  // The bypassed stop: no fix line at all, the stop, the child still without
  // an outcome, and the derived status.
  const bypassed = panel.getByTestId(`exception-${SEEDED.bypassed}`);
  await expect(bypassed).toContainText("Kilimani");
  await expect(bypassed).toContainText(`Still no outcome: ${SEED.parentChild}`);
  await expect(bypassed).toContainText("Open");
  await expect(bypassed).not.toContainText("Phone reported");
  await expect(bypassed.getByRole("button", { name: "Mark reviewed" })).toBeVisible();

  // The remote absent: the answer and the call-now notice's fate.
  const remote = panel.getByTestId(`exception-${SEEDED.absentRemote}`);
  await expect(remote).toContainText("Kevin Mwangi");
  await expect(remote).toContainText("the driver was not at the stop");
  await expect(remote).toContainText(/call-now notice sent to the family at \d\d:\d\d/);

  // The attested absent names its attestation; the unverified one its reason;
  // the implausible one has no stop and reads as a jump.
  await expect(panel.getByTestId(`exception-${SEEDED.absentAttested}`)).toContainText(
    "a parent or the office told the driver",
  );
  await expect(panel.getByTestId(`exception-${SEEDED.unverified}`)).toContainText("fix too coarse");
  await expect(panel.getByTestId(`exception-${SEEDED.implausible}`)).toContainText(
    "Moved 5.2 km between fixes",
  );
});

test.describe("reviewing a seeded exception", () => {
  // The review stamps once and is idempotent, so the seed must be restored by
  // hand or the next run of this suite has nothing to click.
  test.afterEach(() => {
    sqlUnreviewException(SEEDED.unverified);
  });

  test("a coordinator marks one reviewed; the row stays and the badge count drops", async ({ page }) => {
    await signInAs(page, COORDINATOR_A, SCHOOL_A_ID);
    const report = await openSeededReport(page);
    const panel = report.getByTestId("run-exceptions");
    const row = panel.getByTestId(`exception-${SEEDED.unverified}`);
    await expect(row.getByTestId(`review-exception-${SEEDED.unverified}`)).toBeVisible();

    await row.getByTestId(`review-exception-${SEEDED.unverified}`).click();

    // The row is still there, now carrying the stamp; the panel count moves.
    await expect(row.getByTestId(`reviewed-${SEEDED.unverified}`)).toContainText("Reviewed");
    // The seeded coordinator's display name (backend/db/seeds/003_local_snapshot.sql).
    await expect(row.getByTestId(`reviewed-${SEEDED.unverified}`)).toContainText("by Carla Coordinator");
    await expect(row.getByRole("button", { name: "Mark reviewed" })).toHaveCount(0);
    await expect(row).toContainText("Check not verified");
    await expect(panel.getByTestId("run-exceptions-to-review")).toContainText("4 to review");

    // The list badge followed without a reload (the runs query was
    // invalidated), and again after one.
    await page.keyboard.press("Escape");
    await expect(report).toHaveCount(0);
    await expect(seededRow(page).getByTestId("exceptions-to-review")).toContainText("4 to review");
    await page.reload();
    await expect(seededRow(page).getByTestId("exceptions-to-review")).toContainText("4 to review");
  });
});

test.describe("a live run's badge", () => {
  /** "Today" in the backend's run calendar is Africa/Nairobi (UTC+3). */
  function nairobiDate(): string {
    return new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
  }

  async function driverHeaders(request: APIRequestContext) {
    return authHeaders(await apiDriverToken(request));
  }

  /** Start the seeded morning route and Arrive twice: the second Arrive moves
   * progress past stop 1 (Kilimani, Faith) with nothing recorded, which
   * raises one bypassed-stop exception. */
  async function startRunWithOneException(request: APIRequestContext): Promise<string> {
    const headers = await driverHeaders(request);
    const context = await (
      await request.get(`${API_URL}/api/runs/driver/context`, { headers })
    ).json();
    const route = context.routes.find((r: any) => r.type === "morning");
    expect(route, "the seeded driver bus has a morning route").toBeTruthy();
    const started = await request.post(`${API_URL}/api/runs/driver/start`, {
      headers, data: { route_id: route.id },
    });
    expect(started.ok(), await started.text()).toBeTruthy();
    const runId = (await started.json()).id;
    for (let i = 0; i < 2; i++) {
      const arrived = await request.post(`${API_URL}/api/runs/driver/arrive`, {
        headers, data: { run_id: runId },
      });
      expect(arrived.ok(), await arrived.text()).toBeTruthy();
    }
    return runId;
  }

  test.afterEach(async ({ request }) => {
    await endActiveRun(request); // never leave an in-progress run behind
  });

  test("Dashboard and Run History agree on the count, and the driver's list has none", async ({
    page,
    request,
  }) => {
    const runId = await startRunWithOneException(request);
    try {
      // The driver's own read of the runs list never carries the count (R22).
      const mine = await request.get(`${API_URL}/api/runs`, { headers: await driverHeaders(request) });
      expect(mine.ok()).toBeTruthy();
      const own = (await mine.json()).find((r: any) => r.id === runId);
      expect(own, "the driver sees their own bus's run").toBeTruthy();
      expect(own).not.toHaveProperty("exception_count");

      await signInAs(page, ADMIN, SCHOOL_A_ID);

      // Dashboard: the Active Runs card row for today's run. The seed also
      // leaves a stale run of the same route on this card ("Needs closing"),
      // so the row is told apart by the badge it carries.
      await page.goto("/");
      const card = cardContaining(page, "Active Runs");
      const liveRow = card
        .locator("div.rounded-lg", { hasText: SEED.driverMorningRoute })
        .filter({ has: page.getByTestId("exceptions-to-review") });
      await expect(liveRow).toHaveCount(1);
      await expect(liveRow.getByTestId("exceptions-to-review")).toContainText("1 to review");
      await expect(liveRow).toContainText("Stop exceptions waiting for the office");

      // Run History: the same run, the same count.
      await page.goto("/runs");
      const historyRow = page
        .getByRole("row", { name: new RegExp(nairobiDate()) })
        .filter({ has: page.getByTestId("exceptions-to-review") });
      await expect(historyRow).toHaveCount(1);
      await expect(historyRow.getByTestId("exceptions-to-review")).toContainText("1 to review");

      // And the report names the child the stop left unrecorded.
      await historyRow.getByRole("cell", { name: nairobiDate() }).click();
      const panel = dialog(page).getByTestId("run-exceptions");
      await expect(panel).toContainText("Stop passed without outcomes");
      await expect(panel).toContainText(`Still no outcome: ${SEED.parentChild}`);
      await expect(panel.getByRole("button", { name: "Mark reviewed" })).toHaveCount(1);
    } finally {
      purgeRun(runId); // cascades the exception and its prompt event
    }
  });
});
