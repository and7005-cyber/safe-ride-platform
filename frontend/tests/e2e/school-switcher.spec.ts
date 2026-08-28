import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  API_URL,
  DIRECTOR_A,
  DIRECTOR_B,
  SCHOOL_A_ID,
  SCHOOL_B_ID,
  SEED,
  SEED_B,
  apiToken,
  fieldInput,
  pickSelectOption,
  schoolHeaders,
  signInAs,
} from "./helpers";

// U12 — memberships, offers, the choose-school landing, the sidebar switcher,
// and cross-school isolation (AE6/AE7), exercised as ONE journey because the
// journey itself is the product flow: no seeded two-school account exists, so
// the spec creates the second membership the only way the product allows —
// director B offers the role on the Staff page, director A accepts it on the
// offers screen — and only then can switch schools.
//
// Assumes the seeded local stack: DIRECTOR_A is director at school A only
// (Greenfield Academy, GFA-001, students incl. Faith Achieng), DIRECTOR_B is
// the sole director at school B (IT Second School, ITS-002, student Ben
// Barasa), both from backend/db/seeds/003_local_snapshot.sql.

/** Remove any membership/offer for director A at school B (self-healing setup
 * and teardown — the spec must leave the seeded single-school world behind). */
async function removeDirectorAFromSchoolB(request: APIRequestContext) {
  const token = await apiToken(request, DIRECTOR_B.email, DIRECTOR_B.password);
  const headers = schoolHeaders(token, SCHOOL_B_ID);
  const staffResp = await request.get(`${API_URL}/api/staff`, { headers });
  if (!staffResp.ok()) return;
  const staff = await staffResp.json();
  const member = staff.members.find((m: any) => m.email === DIRECTOR_A.email);
  if (member) {
    await request.delete(`${API_URL}/api/staff/${member.userId}`, { headers });
  }
  const offer = staff.offers.find((o: any) => o.email === DIRECTOR_A.email);
  if (offer) {
    await request.delete(`${API_URL}/api/staff/offers/${offer.id}`, { headers });
  }
}

test.beforeEach(async ({ request }) => {
  await removeDirectorAFromSchoolB(request);
});

test.afterEach(async ({ request }) => {
  await removeDirectorAFromSchoolB(request);
});

test("offer → accept → choose → switch: two schools stay isolated on screen", async ({
  page,
}) => {
  // --- Director B offers director A a role, on the Staff page ------------
  await signInAs(page, DIRECTOR_B, SCHOOL_B_ID);
  await page.goto("/staff");
  await page.getByTestId("staff-add").click();
  const dialog = page.getByRole("dialog");
  await fieldInput(dialog, "Email").fill(DIRECTOR_A.email);
  await fieldInput(dialog, "Full name").fill("Dora Director");
  await pickSelectOption(dialog, "Role", "Director");
  await page.getByTestId("staff-save").click();
  // An existing account is OFFERED the role (anti-enumeration: same answer
  // for every existing-account state) — the row shows as offered.
  await expect(
    page.getByRole("row").filter({ hasText: DIRECTOR_A.email }).getByText("Offered"),
  ).toBeVisible();

  // --- Director A signs in and meets the offer before anything else ------
  await signInAs(page, DIRECTOR_A);
  const offerCard = page.locator("[data-testid^='offer-']");
  await expect(offerCard).toBeVisible();
  // The card names the school, its non-editable code, the role and the
  // offerer — the anti-phishing surface (U12).
  await expect(offerCard).toContainText(SEED_B.school);
  await expect(offerCard).toContainText("ITS-002");
  await expect(offerCard).toContainText("Director");
  await expect(offerCard).toContainText("Derek Director");
  await offerCard.getByRole("button", { name: "Accept" }).click();

  // --- Two memberships, no school in this tab: the chooser lands ----------
  const chooseA = page.getByTestId(`choose-school-${SCHOOL_A_ID}`);
  const chooseB = page.getByTestId(`choose-school-${SCHOOL_B_ID}`);
  await expect(chooseA).toBeVisible();
  await expect(chooseB).toBeVisible();
  await expect(chooseA).toContainText("GFA-001");
  await expect(chooseB).toContainText("ITS-002");
  await chooseA.getByRole("button").click();

  // --- Working in school A --------------------------------------------------
  await expect(page.getByTestId("active-school-card")).toContainText(SEED.school);
  await expect(page.getByTestId("active-school-card")).toContainText("GFA-001");
  await page.goto("/students");
  await expect(page.getByText(SEED.parentChild).first()).toBeVisible();
  await expect(page.getByText(SEED_B.student)).toHaveCount(0);

  // --- Switch to school B from the sidebar switcher ------------------------
  await page.getByTestId("school-switcher").click();
  await page.getByTestId(`switch-school-${SCHOOL_B_ID}`).click();
  // Switching lands on the dashboard, scoped to B.
  await page.waitForURL("/");
  await expect(page.getByTestId("active-school-card")).toContainText(SEED_B.school);
  await expect(page.getByTestId("active-school-card")).toContainText("ITS-002");

  // --- AE7: nothing of school A is rendered anywhere on B's screens --------
  await page.goto("/students");
  await expect(page.getByText(SEED_B.student).first()).toBeVisible();
  await expect(page.getByText(SEED.parentChild)).toHaveCount(0);
  await expect(page.getByText(SEED.afternoonRideMate)).toHaveCount(0);
  // The whole document — not just the table — carries no school A student.
  expect(await page.locator("body").textContent()).not.toContain(SEED.parentChild);

  // Buses too: B's bus only, none of A's fleet.
  await page.goto("/buses");
  await expect(page.getByText(SEED_B.bus).first()).toBeVisible();
  await expect(page.getByText(SEED.driverBus, { exact: true })).toHaveCount(0);

  // --- Switch back to A: B's data is gone from the screen -------------------
  await page.goto("/");
  await page.getByTestId("school-switcher").click();
  await page.getByTestId(`switch-school-${SCHOOL_A_ID}`).click();
  await page.waitForURL("/");
  await expect(page.getByTestId("active-school-card")).toContainText(SEED.school);
  await page.goto("/students");
  await expect(page.getByText(SEED.parentChild).first()).toBeVisible();
  await expect(page.getByText(SEED_B.student)).toHaveCount(0);
});
