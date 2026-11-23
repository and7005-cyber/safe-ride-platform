import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  SCHOOL_A_ID,
  apiToken,
  authHeaders,
  emailLogin,
  schoolHeaders,
  signInAs,
  signInAsDriver,
  signUpVerifiedParent,
  sqlDeleteAuditFor,
  uniqueName,
} from "./helpers";

// Hardening U9 — the office gets a locked-out parent back in (Parents page:
// Reset password; Confirm and reset on an Awaiting-signup row), and anyone
// signed in with a password can change it themselves.
//
// FRESH parent accounts per run, never the seeded parent: a reset signs the
// account out everywhere, and the seeded parent's cached token is shared by
// every later spec in this worker. Each family's student is deleted after the
// test, and so are the audit rows the office actions write (017's actions
// must not outlive a spec — helpers.sqlDeleteAuditFor).

const PARENT_PASSWORD = "ParentPass1!";
const RELAYED = /^[0-9A-HJKMNP-TV-Z]{16}$/;

// One admin login for the whole file (the per-account login budget).
let adminToken: string | null = null;

async function adminHeaders(request: APIRequestContext) {
  adminToken ??= await apiToken(request, ADMIN.email, ADMIN.password);
  return schoolHeaders(adminToken, SCHOOL_A_ID);
}

/** A school-A student whose Parent 1 slot carries `email`. */
async function createStudent(request: APIRequestContext, name: string, email: string) {
  const response = await request.post(`${API_URL}/api/students`, {
    headers: await adminHeaders(request),
    data: {
      name, grade: "Grade 2", parent_name: "E2E Reset Parent",
      parent_phone: "+254711000901", parent_email: email, route_ids: [],
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id as string;
}

async function cleanUp(
  request: APIRequestContext,
  studentId: string | null,
  parentId: string | null,
  email: string,
) {
  const headers = await adminHeaders(request);
  if (studentId) await request.delete(`${API_URL}/api/students/${studentId}`, { headers });
  // The office incident the linking rule raised names the address.
  const incidents = await request.get(`${API_URL}/api/incidents`, { headers });
  if (incidents.ok()) {
    for (const row of await incidents.json()) {
      if (String(row.description ?? "").includes(email)) {
        await request.delete(`${API_URL}/api/incidents/${row.id}`, { headers });
      }
    }
  }
  if (parentId) sqlDeleteAuditFor(parentId);
}

/** Read the revealed password: shown in fours, the ungrouped value in data-value. */
async function revealedPassword(page: Page): Promise<string> {
  const dialog = page.getByTestId("temp-password-dialog");
  await expect(dialog).toBeVisible();
  const shown = dialog.getByTestId("temp-password");
  const password = (await shown.getAttribute("data-value")) ?? "";
  expect(password).toMatch(RELAYED);
  await expect(shown).toHaveText(password.replace(/(.{4})(?=.)/g, "$1 "));
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(dialog).toBeHidden();
  return password;
}

/** The parent, in a browser of their own, signs in with what they heard
 * (lower case, in groups) and replaces it on the forced change screen. */
async function parentCompletesForcedChange(
  browser: Browser,
  email: string,
  temporary: string,
  newPassword: string,
): Promise<{ page: Page; close: () => Promise<void> }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  const heard = temporary.replace(/(.{4})(?=.)/g, "$1 ").toLowerCase();
  await emailLogin(page, email, heard);
  await expect(page.getByText("Your temporary password must be changed before you can continue.")).toBeVisible();
  await page.locator("#current-password").fill(heard);
  await page.locator("#new-password").fill(newPassword);
  await page.locator("#confirm-password").fill(newPassword);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page).toHaveURL("/parent");
  return { page, close: () => context.close() };
}

test("the office resets a parent's password; the parent completes the forced change and can change it again from Profile", async ({
  page,
  browser,
  request,
}) => {
  const email = `e2e-reset-${Date.now()}@saferide.test`;
  const child = uniqueName("E2E Reset Kid");
  let studentId: string | null = null;
  let parentId: string | null = null;
  try {
    studentId = await createStudent(request, child, email);
    // Verified at sign-up's end: the one school match links it, accepted.
    parentId = (await signUpVerifiedParent(request, {
      email, password: PARENT_PASSWORD, fullName: "E2E Reset Parent",
    })).id;

    await signInAs(page, ADMIN, SCHOOL_A_ID);
    await page.goto("/parents");
    const row = page.getByTestId(`parent-row-${parentId}`);
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: `Reset password for ${email}` }).click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toContainText("alerts on their phone stop until they sign in again");
    await confirmDialog.getByRole("button", { name: "Reset password" }).click();
    const temporary = await revealedPassword(page);
    await expect(page.getByTestId(`parent-temporary-${parentId}`)).toContainText(
      "Temporary password · expires",
    );

    const parent = await parentCompletesForcedChange(browser, email, temporary, "ParentPass2!");
    try {
      await expect(parent.page.getByText(child).first()).toBeVisible();
      // The temporary password is dead now.
      const stale = await request.post(`${API_URL}/api/auth/login`, {
        data: { email, password: temporary },
      });
      expect(stale.status()).toBe(401);

      // Self-service from Profile (DL13): a dialog, this session stays.
      await parent.page.goto("/parent/profile");
      await parent.page.getByTestId("profile-change-password").click();
      const dialog = parent.page.getByTestId("change-password-dialog");
      await expect(dialog).toContainText("You'll stay signed in here. Other devices will be signed out.");
      await dialog.locator("#current-password").fill("ParentPass2!");
      await dialog.locator("#new-password").fill("ParentPass3!");
      await dialog.locator("#confirm-password").fill("ParentPass3!");
      await dialog.getByRole("button", { name: "Change password" }).click();
      await expect(dialog).toBeHidden();
      await expect(parent.page.getByText("Password changed").first()).toBeVisible();
      await parent.page.goto("/parent");
      await expect(parent.page).toHaveURL("/parent");
    } finally {
      await parent.close();
    }

    // The office's row no longer shows a temporary password.
    await page.reload();
    await expect(page.getByTestId(`parent-row-${parentId}`)).toBeVisible();
    await expect(page.getByTestId(`parent-temporary-${parentId}`)).toHaveCount(0);
  } finally {
    await cleanUp(request, studentId, parentId, email);
  }
});

test("the office confirms an Awaiting-signup slot and the parent signs in with the temporary password", async ({
  page,
  browser,
  request,
}) => {
  const email = `e2e-confirm-${Date.now()}@saferide.test`;
  const child = uniqueName("E2E Confirm Kid");
  let studentId: string | null = null;
  let parentId: string | null = null;
  try {
    studentId = await createStudent(request, child, email);
    // Signed up, never verified (no working inbox): no link, and the slot
    // still reads Awaiting signup.
    const signup = await request.post(`${API_URL}/api/auth/signup`, {
      data: { email, password: PARENT_PASSWORD, full_name: "E2E Confirm Parent", role: "parent" },
    });
    expect(signup.ok()).toBeTruthy();
    const login = await request.post(`${API_URL}/api/auth/login`, {
      data: { email, password: PARENT_PASSWORD },
    });
    expect(login.ok()).toBeTruthy();
    parentId = String((await login.json()).user.id);

    await signInAs(page, ADMIN, SCHOOL_A_ID);
    await page.goto("/parents");
    const slot = page.getByRole("row").filter({ hasText: email });
    await expect(slot).toContainText("Awaiting signup");
    await slot.getByRole("button", { name: `Confirm and reset for ${email}` }).click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toContainText("Only confirm after speaking to this parent.");
    await confirmDialog.getByRole("button", { name: "Confirm and reset" }).click();
    const temporary = await revealedPassword(page);
    await expect(page.getByTestId(`parent-row-${parentId}`)).toContainText("Registered");

    // The password the parent chose at sign-up no longer works.
    const old = await request.post(`${API_URL}/api/auth/login`, {
      data: { email, password: PARENT_PASSWORD },
    });
    expect(old.status()).toBe(401);

    const parent = await parentCompletesForcedChange(browser, email, temporary, "ParentPass2!");
    try {
      // Confirmed and linked: the child is on Home, no verify card.
      await expect(parent.page.getByText(child).first()).toBeVisible();
      await expect(parent.page.getByText("Verify your email")).toHaveCount(0);
    } finally {
      await parent.close();
    }
  } finally {
    await cleanUp(request, studentId, parentId, email);
  }
});

test("Change password sits in the admin account menu and is absent from a PIN session", async ({
  page,
}) => {
  await signInAs(page, ADMIN, SCHOOL_A_ID);
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByTestId("menu-change-password").click();
  const dialog = page.getByTestId("change-password-dialog");
  await expect(dialog).toContainText("You'll stay signed in here. Other devices will be signed out.");
  await expect(dialog.getByRole("button", { name: "Sign out" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  // The page is still usable after the menu and the dialog closed.
  await page.getByRole("link", { name: "Parents" }).click();
  await expect(page).toHaveURL("/parents");

  // A driver's PIN session never held the password: no entry anywhere.
  await signInAsDriver(page);
  await expect(page.getByTestId("header-change-password")).toHaveCount(0);
  const me = await page.request.get(`${API_URL}/api/auth/me`, {
    headers: authHeaders(await page.evaluate(() => localStorage.getItem("saferide-token") ?? "")),
  });
  expect((await me.json()).signedInWithPin).toBe(true);
});
