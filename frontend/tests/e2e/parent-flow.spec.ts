import { expect, test } from "@playwright/test";
import { PARENT, emailLogin } from "./helpers";

// Parent journey: home, tracking, profile, push state.

test("parent home lists children with status and ETA", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
  await expect(page.getByText("Brian Achieng")).toBeVisible();
  await expect(page.getByText("Faith Achieng")).toBeVisible();
  // The bus-less child renders without driver actions.
  await expect(page.getByText("Grace Njeri")).toBeVisible();
});

test("parent track page shows the route map with own stop highlighted", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/track");

  await expect(page.locator(".leaflet-container")).toBeVisible();
  // Stops are now named by home address (#14); the parent's own stop is shown unmasked.
  await expect(page.getByText(/Kilimani/).first()).toBeVisible();
  await expect(page.getByText("Your stop").first()).toBeVisible();
  await expect(page.getByText("School", { exact: true }).first()).toBeVisible();
});

test("parent profile shows account, children, and push state", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/profile");

  await expect(page.getByText("Amina Parent")).toBeVisible();
  await expect(page.getByText(PARENT.email)).toBeVisible();
  await expect(page.getByText("My Children")).toBeVisible();
  await expect(page.getByText("Brian Achieng")).toBeVisible();

  // Local stack has no FCM/VAPID config, so the push toggle reports exactly that.
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Push not configured on this server/ }),
  ).toBeDisabled();
});
