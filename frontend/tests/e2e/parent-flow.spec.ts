import { expect, test } from "@playwright/test";
import { PARENT, SEED, cardContaining, clearCancellationState, emailLogin } from "./helpers";

// Parent journey: home, tracking, profile, push state, Cancel-a-Ride.

// Labels the derived display_status can render as (R36).
const STATUS_BADGE_LABEL = /^(At home|At School|On the bus|Dropped off|Absent)$/;

test("parent home lists children with status and ETA", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
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
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/track");

  await expect(page.getByTestId("track-map")).toBeVisible();
  await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });
  // Stops are now named by home address (#14); the parent's own stop is shown unmasked.
  await expect(page.getByText(SEED.parentChildStop).first()).toBeVisible();
  await expect(page.getByText("Your stop").first()).toBeVisible();
  await expect(page.getByText("School", { exact: true }).first()).toBeVisible();
});

test("parent profile shows account, children, and push state", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
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
    await emailLogin(page, PARENT.email, PARENT.password);

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
