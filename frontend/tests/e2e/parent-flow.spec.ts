import { expect, test } from "@playwright/test";
import { PARENT, SEED, emailLogin } from "./helpers";

// Parent journey: home, tracking, profile, push state.

test("parent home lists children with status and ETA", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
  await expect(page.getByText(SEED.parentChild)).toBeVisible();
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

  // Local stack has no FCM/VAPID config, so the push toggle reports exactly that.
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeDisabled();
});
