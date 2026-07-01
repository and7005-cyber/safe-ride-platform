import { expect, test } from "@playwright/test";
import { PARENT, SEED, cardContaining, emailLogin } from "./helpers";

// Parent journey: home, tracking, profile, push state.

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

  // Local stack has no FCM/VAPID config, so the push toggle reports exactly that.
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeDisabled();
});
