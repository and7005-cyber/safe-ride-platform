import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  API_URL,
  DIRECTOR_B,
  SCHOOL_B_ID,
  SEED_B,
  apiToken,
  authHeaders,
  schoolHeaders,
  signInAs,
  uniqueName,
} from "./helpers";

// U13 — parent pending-link cards (R31/R32; AE22): staff at school B enter a
// student with a parent email that already has a SafeRide account → the
// parent sees ONE card naming school B (and NOTHING about the child), and
// answers it: Accept turns the link on and the child appears; "Not my child"
// (decline) asks for confirmation, clears the card, leaves the children list
// unchanged and raises B's mismatched-email alert.
//
// A FRESH parent account per run — the task's seed contract: the seeded
// parent (and7005@gmail.com) already holds ACCEPTED links at A and B, and a
// student entered for an already-accepted school links silently (no card),
// so seeded links are never touched here.
//
// ORDER MATTERS: the decline journey runs FIRST. Accepting at B flips the
// parent to "accepted at school B", after which any later student with this
// email at B auto-links with no pending card — decline-first is the only
// order that probes both paths at one school (and one school is what keeps
// the "exactly one card, naming B only" assertion honest).

const parentEmail = `e2e-pending-${Date.now()}@saferide.test`;
const parentPassword = "Pending1!";
const PARENT_FIXTURE = { email: parentEmail, password: parentPassword };

async function directorHeaders(request: APIRequestContext) {
  const token = await apiToken(request, DIRECTOR_B.email, DIRECTOR_B.password);
  return schoolHeaders(token, SCHOOL_B_ID);
}

/** Staff-side student entry at school B carrying the fixture parent's email
 * (the pending link is created by this write). Returns the student id. */
async function createStudentAtB(request: APIRequestContext, name: string): Promise<string> {
  const response = await request.post(`${API_URL}/api/students`, {
    headers: await directorHeaders(request),
    data: {
      name,
      grade: "Grade 4",
      parent_name: "E2E Pending Parent",
      parent_phone: "+254700999001",
      parent_email: parentEmail,
    },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).id;
}

async function deleteStudentAtB(request: APIRequestContext, studentId: string): Promise<void> {
  await request.delete(`${API_URL}/api/students/${studentId}`, {
    headers: await directorHeaders(request),
  });
}

/** Remove this run's mismatched-email alerts at B (idempotent teardown). */
async function clearMismatchAlerts(request: APIRequestContext): Promise<void> {
  const headers = await directorHeaders(request);
  const incidents = await request.get(`${API_URL}/api/incidents`, { headers });
  if (!incidents.ok()) return;
  for (const row of await incidents.json()) {
    if (String(row.description ?? "").startsWith(`Mismatched email: ${parentEmail}`)) {
      await request.delete(`${API_URL}/api/incidents/${row.id}`, { headers });
    }
  }
}

test.beforeAll(async ({ request }) => {
  // The fresh parent account (parent is the only self-service role, U13).
  const signup = await request.post(`${API_URL}/api/auth/signup`, {
    data: {
      email: parentEmail,
      password: parentPassword,
      full_name: "E2E Pending Parent",
      role: "parent",
    },
  });
  expect(signup.ok()).toBeTruthy();
  // No student carries this email yet, so signup auto-linking finds nothing:
  // the account starts with zero links by construction.
  const token = (await signup.json()).token as string;
  const children = await request.get(`${API_URL}/api/parent-portal/children`, {
    headers: authHeaders(token),
  });
  expect(await children.json()).toEqual([]);
  // Accounts have no delete endpoint (staff.spec's convention): the unique
  // per-run email is the cleanup story for the account row itself.
});

test("decline: one card naming school B only; 'Not my child' confirms, clears it and alerts the school", async ({
  page,
  request,
}) => {
  const studentName = uniqueName("E2E Pending Decline");
  const studentId = await createStudentAtB(request, studentName);

  try {
    await signInAs(page, PARENT_FIXTURE);
    await page.goto("/parent");

    // Exactly ONE pending card, naming school B — and NO child details of
    // any kind on it (AE22: the API sends none by design).
    const card = page.getByTestId(`pending-card-${SCHOOL_B_ID}`);
    await expect(card).toBeVisible();
    await expect(page.locator('[data-testid^="pending-card-"]')).toHaveCount(1);
    await expect(card).toContainText(SEED_B.school);
    await expect(card).toContainText(/wants to connect/);
    await expect(card).not.toContainText(studentName);
    await expect(card).not.toContainText("Grade 4");

    // "Not my child" goes through the confirm dialog, which says the school
    // will be alerted.
    await card.getByTestId("pending-decline").click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toContainText(/school will be alerted/i);
    await confirmDialog.getByRole("button", { name: "Not my child" }).click();

    // The card clears and the children list is unchanged (still empty).
    await expect(page.getByTestId(`pending-card-${SCHOOL_B_ID}`)).toHaveCount(0);
    await expect(page.getByText("No children are linked to your account yet.")).toBeVisible();
    await expect(page.getByText(studentName)).toHaveCount(0);

    // School B's Alerts feed carries the mismatched-email incident, naming
    // the EMAIL — never the child (the school knows its own students).
    await signInAs(page, DIRECTOR_B, SCHOOL_B_ID);
    await page.goto("/alerts");
    await expect(
      page.getByText(new RegExp(`Mismatched email: ${parentEmail}`)).first(),
    ).toBeVisible();
  } finally {
    await deleteStudentAtB(request, studentId);
    await clearMismatchAlerts(request);
  }
});

test("accept: the card connects the link and the child appears", async ({ page, request }) => {
  // A fresh link: the decline above removed the previous one, so the parent
  // still has NO accepted school at B and this student pends again.
  const studentName = uniqueName("E2E Pending Accept");
  const studentId = await createStudentAtB(request, studentName);

  try {
    await signInAs(page, PARENT_FIXTURE);
    await page.goto("/parent");

    const card = page.getByTestId(`pending-card-${SCHOOL_B_ID}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText(SEED_B.school);
    await expect(card).not.toContainText(studentName);

    await card.getByTestId("pending-accept").click();

    // Card gone, child on the list — the accepted link is what unlocks the
    // child's details.
    await expect(page.getByTestId(`pending-card-${SCHOOL_B_ID}`)).toHaveCount(0);
    await expect(page.getByText(studentName).first()).toBeVisible();
  } finally {
    await deleteStudentAtB(request, studentId);
  }
});
