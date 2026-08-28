import { expect, test, type Page } from "@playwright/test";
import {
  PROVIDER,
  DIRECTOR_B,
  SCHOOL_B_ID,
  SEED,
  SEED_B,
  TotpMinter,
  sqlDropSchool,
  sqlEndProviderSupportSessions,
  sqlListSchoolIdsByName,
  sqlResetProviderTotp,
  uniqueName,
} from "./helpers";

// U13 — the provider console (R19–R22, R24, R25, R27; AE19, AE29): two-step
// login, second-factor enrolment, create school with the reveal-once
// temporary password, step-in with a reason into a school's admin console
// (banner + director powers) and back out, the unmasked audit reader, and
// the reset path that returns the seed to its unenrolled state.
//
// SEED CONTRACT: provider@kuumbai.test ships UNENROLLED (fixed totp_salt in
// backend/db/seeds/003_local_snapshot.sql). This suite enrols it, captures
// the secret from the one-time enrolment screen, and puts the account back:
// the final test exercises the PRODUCT reset path (reset-totp on itself),
// and before/afterAll run the SQL restoration as the crash-proof backstop —
// a run that dies mid-suite must not leave the account enrolled under a
// secret nobody knows.
//
// RATE-LIMIT BUDGET: /api/auth/totp allows 10 POSTs per IP per rolling
// minute (never scaled locally). This file's login tests spend 1 (enrolment
// exchange) + 2 (wrong+right) + 6 (login-and-void) = 9, separated by UI
// interaction time; keep any new code-step calls out of the first three
// tests. Codes come from ONE shared TotpMinter: the server refuses any step
// at or below the last accepted one (replay guard), and the minter is what
// spaces consecutive codes across 30-second steps.

const SCHOOL_PREFIX = "E2E Provider School";

// Captured at the enrolment screen (shown once); minted through one shared
// minter for every later code.
let minter: TotpMinter | null = null;
// A confirmed provider session token, reused across tests the way signInAs
// caches tokens (login is rate-limited; the provider flow doubly so).
let providerToken: string | null = null;

function sweepFixtureSchools(): void {
  for (const id of sqlListSchoolIdsByName(SCHOOL_PREFIX)) sqlDropSchool(id);
}

test.beforeAll(() => {
  // Self-heal from a crashed earlier run, then start from the seed contract.
  sqlEndProviderSupportSessions();
  sqlResetProviderTotp();
  sweepFixtureSchools();
});

test.afterAll(() => {
  sqlEndProviderSupportSessions();
  sqlResetProviderTotp();
  sweepFixtureSchools();
});

async function fillProviderPassword(page: Page): Promise<void> {
  await page.goto("/auth");
  await page.locator("#email").fill(PROVIDER.email);
  await page.locator("#password").fill(PROVIDER.password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
}

/** Session-seeding sign-in for tests whose subject is NOT the login flow:
 * reuses the confirmed token (each Playwright test gets a fresh context, so
 * localStorage starts empty every time). */
async function providerSignIn(page: Page): Promise<void> {
  if (!providerToken) throw new Error("provider token not captured yet — test order broken");
  await page.goto("/auth");
  await page.evaluate((t) => localStorage.setItem("saferide-token", t), providerToken);
  await page.evaluate(() => sessionStorage.removeItem("saferide-school"));
  await page.goto("/provider");
  await page.waitForURL(/\/provider/);
}

test("first provider sign-in walks through enrolment: key shown once, confirm, console", async ({
  page,
}) => {
  await fillProviderPassword(page);

  // The unenrolled provider is exchanged straight into a restricted session
  // (no code exists yet) and lands on the enrolment interstitial.
  const enrolCard = page.getByTestId("totp-enrol-card");
  await expect(enrolCard).toBeVisible();

  // The key and otpauth URI, shown once, for manual entry (no QR by scope).
  const secret = (await page.getByTestId("totp-secret").textContent())!.trim();
  expect(secret.length).toBeGreaterThanOrEqual(16);
  const uri = (await page.getByTestId("totp-uri").textContent())!.trim();
  expect(uri).toContain("otpauth://totp/");
  expect(uri).toContain(`secret=${secret}`);

  minter = new TotpMinter(secret);
  await page.getByTestId("totp-enrol-code").fill(await minter.next());
  await page.getByTestId("totp-enrol-confirm").click();

  // Enrolment confirmed: the provider console is the provider's home.
  await page.waitForURL(/\/provider/);
  await expect(page.getByTestId("provider-layout")).toBeVisible();
  await expect(page.getByText(SEED_B.school).first()).toBeVisible();

  // R24: the health list renders counts, never a roster — no student name
  // from either seeded school may appear anywhere on this page.
  await expect(page.getByText(SEED_B.student)).toHaveCount(0);
  await expect(page.getByText(SEED.parentChild)).toHaveCount(0);

  providerToken = await page.evaluate(() => localStorage.getItem("saferide-token"));
  expect(providerToken).toBeTruthy();
});

test("two-step login: no session from the password, wrong code errors, reload forgets the token, right code lands", async ({
  page,
}) => {
  test.skip(!minter, "enrolment test must have run first");

  await fillProviderPassword(page);
  await expect(page.getByTestId("totp-code")).toBeVisible();
  // AE19: the password step alone must NOT have issued a session.
  expect(await page.evaluate(() => localStorage.getItem("saferide-token"))).toBeNull();

  // Wrong code → the server's message, still on the challenge.
  await page.getByTestId("totp-code").fill("000000");
  await page.getByTestId("totp-submit").click();
  await expect(page.getByTestId("totp-error")).toHaveText(/invalid code/i);

  // The pre-auth token lives in component state only: a reload returns to
  // the password step (AE19's "token not persisted").
  await page.reload();
  await expect(page.locator("#password")).toBeVisible();
  await expect(page.getByTestId("totp-code")).toHaveCount(0);

  // Fresh password step, right code → the provider console.
  await fillProviderPassword(page);
  await page.getByTestId("totp-code").fill(await minter!.next());
  await page.getByTestId("totp-submit").click();
  await page.waitForURL(/\/provider/);

  providerToken = await page.evaluate(() => localStorage.getItem("saferide-token"));
  expect(providerToken).toBeTruthy();
});

test("five wrong codes void the pre-auth token and return to the password step", async ({
  page,
}) => {
  test.skip(!minter, "enrolment test must have run first");

  await fillProviderPassword(page);
  await expect(page.getByTestId("totp-code")).toBeVisible();

  // Attempts 1–4 answer "Invalid code"; the fifth voids the token and the
  // client returns to the password step with the distinct message (AE19).
  // Wrong codes never consume a TOTP step, so the shared minter is unaffected.
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    await page.getByTestId("totp-code").fill(String(attempt).padStart(6, "0"));
    await page.getByTestId("totp-submit").click();
    await expect(page.getByTestId("totp-error")).toHaveText(/invalid code/i);
  }
  await page.getByTestId("totp-code").fill("000005");
  await page.getByTestId("totp-submit").click();

  await expect(page.getByTestId("login-notice")).toHaveText(/too many wrong codes/i);
  await expect(page.locator("#password")).toBeVisible();
  await expect(page.getByTestId("totp-code")).toHaveCount(0);
});

test("create school: reveal-once code + temporary password; existing email is offered", async ({
  page,
}) => {
  test.skip(!providerToken, "a confirmed provider session is required");
  const schoolName = uniqueName(SCHOOL_PREFIX);
  const directorEmail = `e2e-provider-director-${Date.now()}@saferide.test`;

  try {
    await providerSignIn(page);

    // --- new director email → created + temporary password shown once ----
    await page.getByTestId("provider-create-school").click();
    const dialog = page.getByTestId("create-school-dialog");
    await dialog.getByTestId("school-name").fill(schoolName);
    await dialog.getByTestId("director-email").fill(directorEmail);
    await dialog.getByTestId("director-name").fill("E2E Director");
    await dialog.getByTestId("create-school-save").click();

    const created = page.getByTestId("school-created-dialog");
    await expect(created).toBeVisible();
    // The generated, non-editable code: AAA-NNN.
    const code = (await created.getByTestId("school-code").textContent())!.trim();
    expect(code).toMatch(/^[A-Z]{3}-\d{3}$/);
    const tempPassword = (await created.getByTestId("temp-password").textContent())!.trim();
    expect(tempPassword.length).toBeGreaterThanOrEqual(12);
    await created.getByRole("button", { name: "Done" }).click();
    await expect(created).toHaveCount(0);

    // The school appears on the list with its code.
    const row = page.getByRole("row").filter({ hasText: schoolName });
    await expect(row).toBeVisible();
    await expect(row.getByText(code)).toBeVisible();

    // --- existing email → offered, no password to reveal ------------------
    await page.getByTestId("provider-create-school").click();
    await dialog.getByTestId("school-name").fill(`${schoolName} Offered`);
    await dialog.getByTestId("director-email").fill(DIRECTOR_B.email);
    await dialog.getByTestId("create-school-save").click();

    await expect(page.getByTestId("school-created-dialog")).toBeVisible();
    await expect(page.getByTestId("director-offered")).toContainText(DIRECTOR_B.email);
    await expect(page.getByTestId("temp-password")).toHaveCount(0);
    await page.getByTestId("school-created-dialog").getByRole("button", { name: "Done" }).click();
  } finally {
    // Drops both fixture schools AND the DIRECTOR_B offer row (memberships
    // are deleted with the school) — an un-answered offer would otherwise
    // trap every later DIRECTOR_B session on the offers interstitial. The
    // one-off director account remains, like staff.spec's unique emails.
    sweepFixtureSchools();
  }
});

test("step-in with a reason opens the school console with banner + director powers; step out returns", async ({
  page,
}) => {
  test.skip(!providerToken || !minter, "a confirmed provider session is required");

  await providerSignIn(page);

  // Reason is required: the dialog refuses to submit while it is empty.
  await page.getByTestId(`step-in-${SCHOOL_B_ID}`).click();
  const dialog = page.getByTestId("step-in-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("step-in-submit")).toBeDisabled();

  await dialog.getByTestId("step-in-reason").fill("E2E support ticket — checking route setup");
  // The code input shows up front only when the session's last code is
  // stale (>15 min); a normal run is fresh here. Fill it when demanded so a
  // slow run still passes — the server stays the authority either way.
  if (await dialog.getByTestId("step-in-code").isVisible()) {
    await dialog.getByTestId("step-in-code").fill(await minter!.next());
  }
  await dialog.getByTestId("step-in-submit").click();

  // Into the admin surface of school B, banner up.
  await page.waitForURL("/");
  const banner = page.getByTestId("support-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(SEED_B.school);
  await expect(banner).toContainText(/as SafeRide/);
  await expect(banner.getByTestId("support-time-left")).toBeVisible();

  // The banner persists on every admin page, and the step-in resolves as
  // DIRECTOR: the Staff page (director-only) renders instead of bouncing.
  await page.goto("/students");
  await expect(page.getByTestId("support-banner")).toBeVisible();
  await page.goto("/staff");
  await expect(page.getByTestId("support-banner")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Staff" })).toBeVisible();
  // The sidebar's school card names the stepped-in school.
  await expect(page.getByTestId("active-school-card").first()).toContainText(SEED_B.school);

  // The school list stays reachable from the banner while stepped in.
  await page.getByTestId("support-school-list").click();
  await page.waitForURL(/\/provider$/);
  await expect(page.getByTestId("resume-step-in")).toBeVisible();
  await page.getByTestId("resume-step-in").click();
  await page.waitForURL("/");

  // Exit: back on the provider console, and the admin surface is closed —
  // a provider WITHOUT a live step-in never lands there.
  await page.getByTestId("support-step-out").click();
  await page.waitForURL(/\/provider$/);
  await expect(page.getByTestId("resume-step-in")).toHaveCount(0);
  await page.goto("/");
  await page.waitForURL(/\/provider$/);
});

test("audit lists provider actions with real names, filtered by school", async ({ page }) => {
  test.skip(!providerToken, "a confirmed provider session is required");

  await providerSignIn(page);
  await page.goto("/provider/audit");

  // The step-in/step-out just recorded at school B carry the provider's
  // REAL name (R25) — the school-side trail shows "SafeRide", never here.
  await expect(page.getByText(/provider-step-in/).first()).toBeVisible();
  await expect(
    page.getByTestId("audit-actor-name").filter({ hasText: "Kaya Provider" }).first(),
  ).toBeVisible();

  // Filter to school B: the step-in rows stay, scoped to that school.
  await page.getByTestId("audit-school-filter").click();
  await page.getByRole("option", { name: SEED_B.school }).click();
  await expect(page.getByText(/provider-step-in/).first()).toBeVisible();
  await expect(
    page.getByTestId("audit-actor-name").filter({ hasText: "Kaya Provider" }).first(),
  ).toBeVisible();
});

test("teardown through the product: reset-totp returns the seeded provider to unenrolled", async ({
  page,
}) => {
  test.skip(!providerToken || !minter, "a confirmed provider session is required");

  await providerSignIn(page);
  await page.goto("/provider/accounts");

  const ownRow = page.getByRole("row").filter({ hasText: PROVIDER.email });
  await expect(ownRow.getByText("Enrolled")).toBeVisible();

  // The peer-reset action on itself (the seed knows only this account). The
  // confirm dialog first; then the step-up prompt only when the session's
  // last code has gone stale (slow run) — fill it from the shared minter.
  await ownRow.getByRole("button", { name: `Reset second factor for ${PROVIDER.email}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Reset second factor" }).click();
  const stepUp = page.getByTestId("step-up-dialog");
  if (await stepUp.isVisible().catch(() => false)) {
    await stepUp.getByTestId("step-up-code").fill(await minter!.next());
    await stepUp.getByTestId("step-up-confirm").click();
  }

  // The reset lands and every provider route now answers the enrolment 409:
  // the session falls onto the enrolment interstitial — the same screen a
  // fresh sign-in would see. That IS the seed contract restored (unenrolled);
  // afterAll's SQL pass additionally restores the seed's fixed salt.
  await expect(page.getByTestId("totp-enrol-card")).toBeVisible({ timeout: 10_000 });

  // The old secret is dead: no further code from this minter may be used.
  minter = null;
  providerToken = null;
});
