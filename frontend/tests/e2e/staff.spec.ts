import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  API_URL,
  DIRECTOR_A,
  DIRECTOR_B,
  PARENT,
  SCHOOL_A_ID,
  SCHOOL_B_ID,
  SEED,
  apiToken,
  emailLogin,
  fieldInput,
  pickSelectOption,
  schoolHeaders,
  signInAs,
} from "./helpers";

// U12 — the Staff page (R5, R6, R10, R12/AE16/AE23): create with a
// reveal-once temporary password, the forced password change on first
// sign-in, coordinator capability hiding, offer/cancel for existing emails,
// removal, and the last-director lock. Assumes the seeded stack: DIRECTOR_A
// is a director at school A (which also has admin@test.com as a second
// director, so removals there never trip the last-director lock), DIRECTOR_B
// is the SOLE director at school B, and PARENT is a registered parent
// account (backend/db/seeds/003_local_snapshot.sql).

async function openStaffDialog(page: Page) {
  await page.getByTestId("staff-add").click();
  return page.getByRole("dialog");
}

/** API removal of a school-A staff member by email (self-healing teardown). */
async function removeStaffAtA(request: APIRequestContext, email: string) {
  const token = await apiToken(request, DIRECTOR_A.email, DIRECTOR_A.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const staffResp = await request.get(`${API_URL}/api/staff`, { headers });
  if (!staffResp.ok()) return;
  const staff = await staffResp.json();
  const member = staff.members.find((m: any) => m.email === email);
  if (member) {
    await request.delete(`${API_URL}/api/staff/${member.userId}`, { headers });
  }
  const offer = staff.offers.find((o: any) => o.email === email);
  if (offer) {
    await request.delete(`${API_URL}/api/staff/offers/${offer.id}`, { headers });
  }
}

test("director creates a coordinator: temp password once, forced change, capability hiding, removal", async ({
  page,
  request,
}) => {
  // Unique per run: staff accounts have no delete-account endpoint, only
  // membership removal — a reused email would answer "offered" instead of
  // "created" and never show the password dialog.
  const email = `e2e-staff-${Date.now()}@saferide.test`;
  const name = "E2E Coordinator";

  try {
    // --- Create (director A) --------------------------------------------
    await signInAs(page, DIRECTOR_A, SCHOOL_A_ID);
    await page.goto("/staff");
    const dialog = await openStaffDialog(page);
    await fieldInput(dialog, "Email").fill(email);
    await fieldInput(dialog, "Full name").fill(name);
    await pickSelectOption(dialog, "Role", "Coordinator");
    await page.getByTestId("staff-save").click();

    // The temporary password is revealed exactly once (AE23).
    const reveal = page.getByTestId("temp-password-dialog");
    await expect(reveal).toBeVisible();
    const tempPassword = (await reveal.getByTestId("temp-password").textContent())!.trim();
    expect(tempPassword.length).toBeGreaterThanOrEqual(12);
    await reveal.getByRole("button", { name: "Done" }).click();
    await expect(reveal).toHaveCount(0);

    // The member row lists the role and an unset "password set on" (they
    // still hold the temporary password). Exact match: the name cell ("E2E
    // Coordinator") contains the same word and would trip strict mode.
    const row = page.getByRole("row").filter({ hasText: email });
    await expect(row.getByText("Coordinator", { exact: true })).toBeVisible();
    await expect(row).toContainText("—");

    // --- First sign-in forces the change screen (AE16) --------------------
    await page.evaluate(() => {
      localStorage.removeItem("saferide-token");
      sessionStorage.removeItem("saferide-school");
    });
    await emailLogin(page, email, tempPassword);
    // The card title (CardTitle renders a div, not a heading, app-wide).
    await expect(page.getByText("Change your password")).toBeVisible();

    // The CURRENT (temporary) password is required even here.
    await page.locator("#current-password").fill(tempPassword);
    await page.locator("#new-password").fill("Test1234");
    await page.locator("#confirm-password").fill("Test1234");
    await page.getByRole("button", { name: "Change password" }).click();

    // After the change the console loads, scoped to the single membership.
    await expect(page.getByTestId("active-school-card")).toContainText(SEED.school);

    // --- Coordinator capability hiding (U12/R8) ---------------------------
    // No Staff entry in the nav; a direct URL bounces to the dashboard.
    await expect(page.getByRole("link", { name: "Staff" })).toHaveCount(0);
    await page.goto("/staff");
    await page.waitForURL("/");

    // No delete controls on the record pages; the rows themselves render.
    await page.goto("/buses");
    await expect(page.getByText(SEED.busOption).first()).toBeVisible();
    await expect(page.getByTitle("Delete bus")).toHaveCount(0);
    await page.goto("/students");
    await expect(page.getByText(SEED.parentChild).first()).toBeVisible();
    await expect(page.getByTitle("Delete student")).toHaveCount(0);
    await page.goto("/drivers");
    await expect(page.getByTitle("Delete driver")).toHaveCount(0);
    await page.goto("/alerts");
    await expect(page.getByTitle("Delete alert")).toHaveCount(0);

    // --- Removal (director A) ---------------------------------------------
    await signInAs(page, DIRECTOR_A, SCHOOL_A_ID);
    await page.goto("/staff");
    await page.getByLabel(`Remove ${email}`).click();
    const confirmDialog = page.getByRole("dialog");
    await confirmDialog.getByRole("button", { name: "Remove" }).click();
    await expect(page.getByRole("row").filter({ hasText: email })).toHaveCount(0);
  } finally {
    await removeStaffAtA(request, email);
  }
});

test("offering a role to an existing account shows an offered row; cancel withdraws it", async ({
  page,
  request,
}) => {
  try {
    // Self-heal: an aborted earlier run may have left this offer open.
    await removeStaffAtA(request, PARENT.email);

    await signInAs(page, DIRECTOR_A, SCHOOL_A_ID);
    await page.goto("/staff");
    const dialog = await openStaffDialog(page);
    await fieldInput(dialog, "Email").fill(PARENT.email);
    await pickSelectOption(dialog, "Role", "Coordinator");
    await page.getByTestId("staff-save").click();

    // An existing email is OFFERED the role — same answer whatever the
    // account's state (anti-enumeration), so no password dialog appears.
    await expect(page.getByTestId("temp-password-dialog")).toHaveCount(0);
    const offerRow = page.getByRole("row").filter({ hasText: PARENT.email });
    await expect(offerRow.getByText("Offered")).toBeVisible();
    // The row names who offered it.
    await expect(offerRow).toContainText("Dora Director");

    // Cancel withdraws the offer.
    await page.getByLabel(`Cancel offer to ${PARENT.email}`).click();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel offer" }).click();
    await expect(page.getByRole("row").filter({ hasText: PARENT.email })).toHaveCount(0);
  } finally {
    await removeStaffAtA(request, PARENT.email);
  }
});

test("removing the last director is refused with the server's message", async ({
  page,
  request,
}) => {
  // Defensive: school B must be down to its one seeded director for the lock
  // to be the thing under test (the switcher spec cleans up after itself, but
  // an aborted run may have left director A active at B).
  const token = await apiToken(request, DIRECTOR_B.email, DIRECTOR_B.password);
  const headers = schoolHeaders(token, SCHOOL_B_ID);
  const staffResp = await request.get(`${API_URL}/api/staff`, { headers });
  expect(staffResp.ok()).toBeTruthy();
  const staff = await staffResp.json();
  for (const member of staff.members) {
    if (member.role === "director" && member.email !== DIRECTOR_B.email) {
      await request.delete(`${API_URL}/api/staff/${member.userId}`, { headers });
    }
  }

  await signInAs(page, DIRECTOR_B, SCHOOL_B_ID);
  await page.goto("/staff");
  await page.getByLabel(`Remove ${DIRECTOR_B.email}`).click();
  // Self-removal gets the "leave" wording; the server still refuses (AE4).
  await page.getByRole("dialog").getByRole("button", { name: "Leave school" }).click();
  await expect(page.getByText(/at least one director/i).first()).toBeVisible();
  // The row is still there — nothing was removed.
  await expect(
    page.getByRole("row").filter({ hasText: DIRECTOR_B.email }),
  ).toHaveCount(1);
});
