import { expect, test } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  PARENT,
  SCHOOL_A_ID,
  SEED,
  apiToken,
  cardContaining,
  clearCancellationState,
  lastMailLink,
  linkPath,
  schoolHeaders,
  signInAs,
  uniqueName,
} from "./helpers";

// Parent journey: home, tracking, profile, push state, Cancel-a-Ride.

// Labels the derived display_status can render as (R36).
// The shipped vocabulary (U17). The parent app used to say "At School" and
// "On the bus" where admin said "At school" and "On bus"; one module now
// serves all three roles, and this regex tracks it.
const STATUS_BADGE_LABEL = /^(At home|On bus|At school|Dropped off|Absent today)$/;

test("parent home lists children with status and ETA", async ({ page }) => {
  await signInAs(page, PARENT);
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
  await expect(page.getByText(SEED.parentChild)).toBeVisible();
  // Each child card highlights the server-derived display_status (R36).
  await expect(
    cardContaining(page, SEED.parentChild).getByTestId("child-status-badge"),
  ).toHaveText(STATUS_BADGE_LABEL);
  // The bus-less child renders without driver actions.
  await expect(page.getByText(SEED.buslessChild)).toBeVisible();
});

test("parent track page shows the route map with own stop highlighted", async ({ page }) => {
  await signInAs(page, PARENT);
  await page.goto("/parent/track");

  // The multi-tenant seed links Amina across schools (F5): Ben Barasa at
  // school B sorts first and becomes the default child, so pick Faith
  // explicitly — the assertions below are about HER route and stop.
  await page.getByRole("combobox").first().click();
  await page.getByRole("option", { name: SEED.parentChild }).click();

  await expect(page.getByTestId("track-map")).toBeVisible();
  await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });
  // Stops are now named by home address (#14); the parent's own stop is shown unmasked.
  await expect(page.getByText(SEED.parentChildStop).first()).toBeVisible();
  await expect(page.getByText("Your stop").first()).toBeVisible();
  await expect(page.getByText("School", { exact: true }).first()).toBeVisible();
});

test("parent profile shows account, children, and push state", async ({ page }) => {
  await signInAs(page, PARENT);
  await page.goto("/parent/profile");

  await expect(page.getByText(SEED.parentName)).toBeVisible();
  await expect(page.getByText(PARENT.email)).toBeVisible();
  await expect(page.getByText("My Children")).toBeVisible();
  await expect(page.getByText(SEED.parentChild)).toBeVisible();
  // My Children rows carry the same derived status badge as Home (R36).
  await expect(page.getByTestId("child-status-badge").first()).toHaveText(STATUS_BADGE_LABEL);

  // The push toggle renders one of five state labels depending on browser push
  // support, server FCM/VAPID config, notification permission, and subscription
  // — all of which vary by machine and by what earlier specs did to the shared
  // browser's push state. Assert the control renders in ANY of its valid states
  // (the test's intent: the profile surfaces push state), not one hard-coded
  // state — the old "not configured" assumption broke once push was provisioned.
  await expect(
    page.getByRole("button", {
      name: /Push not supported on this device|Push not configured on this server|Notifications blocked in browser|Enable Push Notifications|Disable Push Notifications/,
    }),
  ).toBeVisible();
});

// Cancel-a-Ride journey (R14, R17, R18; AE4): cancel the afternoon ride from
// the child card, see the pending chip and the feed confirmation, withdraw.
test("parent cancels the afternoon ride, sees chip and confirmation, then withdraws", async ({
  page,
  request,
}) => {
  try {
    await signInAs(page, PARENT);

    // Cancel Faith's afternoon ride from her card.
    await cardContaining(page, SEED.parentChild).getByTestId("cancel-ride-button").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(`Cancel a ride — ${SEED.parentChild}`)).toBeVisible();
    await dialog.getByTestId("cancel-scope-afternoon").click();
    await dialog.getByTestId("cancel-confirm").click();
    await expect(dialog).toHaveCount(0); // closes only on server success

    // The pending chip renders from server state (no optimistic writes)...
    await expect(
      cardContaining(page, SEED.parentChild).getByTestId("cancellation-chip"),
    ).toHaveText("PM ride cancelled");
    // ...and survives a reload.
    await page.reload();
    await expect(
      cardContaining(page, SEED.parentChild).getByTestId("cancellation-chip"),
    ).toHaveText("PM ride cancelled");

    // The ride-cancelled confirmation surfaces under the feed's Afternoon
    // period chip (its run_type carries the cancelled scope).
    await page.goto("/parent/alerts");
    await page.getByRole("button", { name: "Afternoon", exact: true }).click();
    const confirmation = page
      .locator("div[class*='bg-card']")
      .filter({ hasText: "Ride Cancelled" })
      .filter({ hasText: `${SEED.parentChild}'s afternoon ride today has been cancelled` });
    await expect(confirmation.first()).toBeVisible({ timeout: 10_000 });

    // Withdraw restores the ride: a single-scope row withdraws directly
    // (no dialog) and the chip disappears with it.
    await page.goto("/parent");
    await cardContaining(page, SEED.parentChild).getByTestId("withdraw-cancellation").click();
    await expect(
      cardContaining(page, SEED.parentChild).getByTestId("cancellation-chip"),
    ).toHaveCount(0);
    await expect(
      cardContaining(page, SEED.parentChild).getByTestId("withdraw-cancellation"),
    ).toHaveCount(0);
  } finally {
    // Idempotent office-side restore: absence row (if the withdraw step never
    // ran) and the admin "Ride Cancellation" alert this journey raised.
    await clearCancellationState(request, SEED.parentChild);
  }
});

// Hardening Release 1b (U8; AE4, DL2, DL9, DL10): a new parent reaches a child
// only after confirming the address from their own signed-in session. The
// mail is opened in a different browser (a fresh context, signed out), which
// signs in on the verify page itself; the first tab — signed in and polling —
// shows the child without a reload.
test("a new parent signs up, verifies from another browser, and Home shows the child without reload", async ({
  page,
  browser,
  request,
}) => {
  const email = `e2e-verify-${Date.now()}@saferide.test`;
  const password = "Verify123!";
  const childName = uniqueName("E2E Verify Kid");
  const headers = schoolHeaders(await apiToken(request, ADMIN.email, ADMIN.password), SCHOOL_A_ID);
  const student = await request.post(`${API_URL}/api/students`, {
    headers,
    data: {
      name: childName, grade: "Grade 2",
      parent_name: "E2E Verify Parent", parent_phone: "+254711222555", parent_email: email,
      home_address: "Verify Lane, Nairobi", route_ids: [],
    },
  });
  expect(student.ok()).toBeTruthy();
  const studentId = (await student.json()).id;
  const other = await browser.newContext();

  try {
    // Tab 1: sign up, read "Check your email", sign in.
    await page.goto("/auth");
    await page.getByRole("button", { name: "Sign up", exact: true }).click();
    await page.locator("#fullName").fill("E2E Verify Parent");
    await page.locator("#email").fill(email);
    await page.locator("#password").fill(password);
    await page.getByRole("button", { name: "Create Account" }).click();
    await page.getByTestId("signup-done").getByRole("button", { name: "Sign in" }).click();
    await page.locator("#password").fill(password);
    await page.getByRole("button", { name: "Sign In", exact: true }).click();
    await page.waitForURL("/parent");
    // Nothing linked yet, even though the school's record carries the email.
    await expect(page.getByTestId("verify-email-card")).toBeVisible();
    await expect(page.getByText(childName)).toHaveCount(0);

    // Tab 2 (another browser): the emailed link, signed out → sign in there.
    const link = await lastMailLink(request, "verification");
    const second = await other.newPage();
    await second.goto(new URL(linkPath(link), page.url()).toString());
    await expect(second.getByTestId("verify-title")).toHaveText(
      "Sign in to finish verifying your email",
    );
    await second.locator("#verify-email").fill(email);
    await second.locator("#verify-password").fill(password);
    await second.getByRole("button", { name: "Sign in and verify" }).click();
    await expect(second.getByTestId("verify-title")).toHaveText("Email verified");
    await expect(second.getByText(/your children will appear on Home/)).toBeVisible();

    // Tab 1, no reload: the card flips away and the child appears.
    await expect(page.getByText(childName)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("verify-email-card")).toHaveCount(0);
  } finally {
    await other.close();
    await request.delete(`${API_URL}/api/students/${studentId}`, { headers });
    // The sign-up incident the verification raised at school A.
    const incidents = await request.get(`${API_URL}/api/incidents`, { headers });
    if (incidents.ok()) {
      for (const row of await incidents.json()) {
        if (String(row.description ?? "").includes(email)) {
          await request.delete(`${API_URL}/api/incidents/${row.id}`, { headers });
        }
      }
    }
  }
});
