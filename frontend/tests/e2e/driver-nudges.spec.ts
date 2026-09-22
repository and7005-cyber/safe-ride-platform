import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  SEED,
  apiDriverToken,
  apiToken,
  authHeaders,
  cardContaining,
  endActiveRun,
  purgeRun,
  signInAsDriver,
} from "./helpers";

// The driver nudge surface (GPS plan U3: R13, R15, R23, R34; F3 prompt half).
//
// Seeded morning route "Express 1 — Morning": stop 1 Kilimani (Faith), stop 2
// Lavington (Happiness), stop 3 Karen (Kevin), stop 4 the school gate. Arriving
// at stop 2 with nobody recorded at stop 1 raises the bypassed-stop prompt for
// Faith; arriving at 3 as well raises a second one for Happiness.

/** Delete today's runs for the driver's bus (completed runs block same-day
 * restarts, and a purged run cascades its exceptions and prompt events). */
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

test.afterEach(async ({ request }) => {
  await endActiveRun(request);
  await deleteTodaysRuns(request);
});

// Spies for the attention cue (R15): every media play is recorded with its
// muted flag — Start Run's priming play is muted, the prompt's tone is not —
// and vibration patterns are collected. Installed before any app script runs.
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

interface Cues {
  plays: { muted: boolean }[];
  vibrations: number[][];
}

async function readCues(page: Page): Promise<Cues> {
  return page.evaluate(() => (window as any).__cues);
}

async function startMorningRun(page: Page) {
  await page.goto("/driver/run");
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: SEED.driverMorningRoute }).click();
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();
}

/** Tap Arrive on the Run page and wait for progress to read `n`. */
async function arriveTo(page: Page, n: number) {
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(new RegExp(`^${n}/\\d+ stops completed$`))).toBeVisible();
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

test("a bypassed stop raises one card with tone and vibration; it survives a reload, follows the driver to Home, ignores taps outside, and resolves through the card", async ({ page, request }) => {
  await page.addInitScript(CUE_SPY);
  await signInAsDriver(page);
  await startMorningRun(page);
  // Start Run primed the audio element inside the tap: a muted play, no tone.
  const primed = await readCues(page);
  expect(primed.plays.some((p) => p.muted)).toBe(true);
  expect(primed.plays.filter((p) => !p.muted)).toHaveLength(0);

  // Reaching stop 1 passes nothing; reaching stop 2 passes Faith's stop
  // with no record — the card arrives on that response.
  await arriveTo(page, 1);
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);
  await arriveTo(page, 2);
  const card = page.getByTestId("nudge-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText(SEED.parentChild);
  await expect(card).toContainText(SEED.parentChildStop);
  await expect(card).toContainText("has no record. Mark boarded or absent?");
  await expect.poll(async () => (await readCues(page)).plays.filter((p) => !p.muted).length).toBe(1);
  expect((await readCues(page)).vibrations).toEqual([[200, 100, 200]]);

  // Server-owned (R34): a reload re-shows it from the context poll. The
  // server already knows it was shown, so the cue does not sound again.
  await page.reload();
  await expect(page.getByTestId("nudge-card")).toBeVisible();
  await expect(page.getByTestId("nudge-card")).toContainText(SEED.parentChild);
  const afterReload = await readCues(page);
  expect(afterReload.plays.filter((p) => !p.muted)).toHaveLength(0);
  expect(afterReload.vibrations).toEqual([]);

  // Visible from Home too; non-modal: tapping outside changes nothing.
  await page.goto("/driver");
  await expect(page.getByText(/Hello,/)).toBeVisible();
  await expect(page.getByTestId("nudge-card")).toBeVisible();
  await page.getByText(/Hello,/).click();
  await page.getByText("Students", { exact: true }).click();
  await expect(page.getByTestId("nudge-card")).toBeVisible();

  // "Mark boarded" through the card: the outcome is recorded with the event
  // id, the card goes, and the ledger shows the tap as the resolution.
  const context = await driverContext(request);
  const runId: string = context.active_run.id;
  const faith = context.students.find((s: any) => s.name === SEED.parentChild);
  expect(faith).toBeTruthy();
  expect(context.pending_prompts).toHaveLength(1);
  await page.getByTestId(`nudge-outcome-${faith.id}`).click();
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);

  const rows = await adminExceptions(request, runId);
  const bypassed = rows.filter((x: any) => x.kind === "stop-bypassed");
  expect(bypassed).toHaveLength(1);
  expect(bypassed[0].status).toBe("resolved");
  const promptEvent = bypassed[0].events.find((e: any) => e.prompt_state != null);
  expect(promptEvent.prompt_state).toBe("answered");
  expect(promptEvent.response).toBe("resolution");
  expect(promptEvent.delivered_at).not.toBeNull();
  expect(promptEvent.shown_at).not.toBeNull();
  expect(
    bypassed[0].events.some((e: any) => e.student_id === faith.id && e.response === "resolution"),
  ).toBe(true);
  // The exemption (U9/R14): the board came through the card with the suite's
  // default fix — Nairobi CBD, some 4 km from Kilimani — and raises no
  // custody exception because it resolved the bypassed stop.
  expect(rows.filter((x: any) => x.kind === "custody-away")).toHaveLength(0);

  // The board agrees: Faith is on the bus.
  await page.goto("/driver/boarding");
  await expect(cardContaining(page, SEED.parentChild).getByText("On bus")).toBeVisible();
});

test("two prompts show one at a time; dismiss records `dismissed`; a stale card on a second context settles silently on the server's conflict", async ({ page, browser, request }) => {
  await signInAsDriver(page);
  await startMorningRun(page);
  // Passing stop 1 (Faith) and stop 2 (Happiness) unrecorded: two prompts.
  await arriveTo(page, 1);
  await arriveTo(page, 2);
  await arriveTo(page, 3);
  const card = page.getByTestId("nudge-card");
  await expect(card).toContainText(SEED.parentChild);
  await expect(card).not.toContainText(SEED.afternoonRideMate);
  await expect(page.getByTestId("nudge-card")).toHaveCount(1);
  const firstEventId = await card.getAttribute("data-event-id");
  expect(firstEventId).toBeTruthy();
  const { active_run } = await driverContext(request);
  const runId: string = active_run.id;

  // A second browser context on the same driver token shows the same card
  // from the poll (R34).
  const context2 = await browser.newContext();
  const page2 = await context2.newPage();
  try {
    await signInAsDriver(page2);
    await page2.goto("/driver/run");
    const card2 = page2.getByTestId("nudge-card");
    await expect(card2).toContainText(SEED.parentChild);
    // Freeze the second screen's poll so its card goes stale on purpose.
    await page2.route("**/api/runs/driver/context", (route) => route.abort());

    // The first screen dismisses: the next prompt takes the slot at once.
    await page.getByTestId("nudge-dismiss").click();
    await expect(card).toContainText(SEED.afternoonRideMate);
    await expect(card).not.toContainText(SEED.parentChild);
    await expect(page.getByTestId("nudge-card")).toHaveCount(1);

    // The stale card's dismiss meets the server's 409 prompt-already-answered:
    // no error, the card goes, the next prompt shows (R23).
    expect(await card2.getAttribute("data-event-id")).toBe(firstEventId);
    await page2.getByTestId("nudge-dismiss").click();
    await page2.unroute("**/api/runs/driver/context");
    await expect(card2).toContainText(SEED.afternoonRideMate);
    await expect(page2.getByText("Could not record")).toHaveCount(0);
  } finally {
    await context2.close();
  }

  // The ledger: the first prompt answered `dismissed`, the exception still
  // open (a dismiss records, it does not resolve); the second still pending.
  const rows = await adminExceptions(request, runId);
  const byOrder = new Map<number, any>(
    rows.filter((x: any) => x.kind === "stop-bypassed").map((x: any) => [x.stop_order, x]),
  );
  expect([...byOrder.keys()].sort()).toEqual([1, 2]);
  const first = byOrder.get(1);
  const second = byOrder.get(2);
  expect(first.status).toBe("open");
  expect(first.events.map((e: any) => [e.prompt_state, e.response])).toEqual([["answered", "dismissed"]]);
  expect(first.events[0].id).toBe(firstEventId);
  expect(second.events.map((e: any) => e.prompt_state)).toEqual(["pending"]);

  // Dismissed stays dismissed: a reload brings back only the second prompt.
  await page.reload();
  await expect(page.getByTestId("nudge-card")).toContainText(SEED.afternoonRideMate);
  await expect(page.getByTestId("nudge-card")).not.toContainText(SEED.parentChild);

  // Recording the outcome from the Board page, not the card: membership is
  // the key, so the prompt answers as `resolution` all the same and the card
  // leaves through the poll.
  await page.goto("/driver/boarding");
  await expect(page.getByTestId("nudge-card")).toContainText(SEED.afternoonRideMate);
  const happiness = (await driverContext(request)).students.find(
    (s: any) => s.name === SEED.afternoonRideMate,
  );
  expect(happiness).toBeTruthy();
  const rowBoard = page.getByTestId(`board-${happiness.id}`);
  await expect(rowBoard).toBeEnabled();
  await rowBoard.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Board", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("nudge-card")).toHaveCount(0);

  const after = await adminExceptions(request, runId);
  const resolvedSecond = after.find((x: any) => x.kind === "stop-bypassed" && x.stop_order === 2);
  expect(resolvedSecond.status).toBe("resolved");
  expect(resolvedSecond.events.map((e: any) => [e.prompt_state, e.response, e.student_id])).toEqual([
    ["answered", "resolution", null],
    [null, "resolution", happiness.id],
  ]);
});
