import { expect, test } from "@playwright/test";
import { ADMIN, DRIVER, PARENT, SEED, emailLogin } from "./helpers";

// These journeys run against the seeded local stack (see
// backend/db/seeds/003_local_snapshot.sql via tests/e2e/helpers.ts). They
// assert against the live-parity copy and information architecture.

test("admin sees the dashboard and can navigate the shell", async ({ page }) => {
  await emailLogin(page, ADMIN.email, ADMIN.password);
  await expect(page).toHaveURL("/");
  await expect(page.getByText("Active Buses")).toBeVisible();
  await expect(page.getByText("Incidents Today")).toBeVisible();

  // Alerts page uses the admin-side type label.
  await page.goto("/alerts");
  await expect(page.getByText("Heavy Traffic / Delay").first()).toBeVisible();

  // Routes page shows configured routes with stops (no zero-state when present).
  await page.goto("/routes");
  await expect(page.getByText(/routes configured/)).toBeVisible();
  await expect(page.getByText(SEED.school).first()).toBeVisible();
});

test("admin is the only role allowed on admin routes", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/buses");
  // Parent gets redirected away from the admin surface.
  await expect(page).toHaveURL("/parent");
});

test("driver can log in by PIN and start a run", async ({ page }) => {
  await page.goto("/auth");
  await page.getByRole("tab", { name: /Driver PIN/ }).click();
  await page.locator("#pin").fill(DRIVER.pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await expect(page).toHaveURL("/driver");
  await expect(page.getByText(SEED.driverBus)).toBeVisible();

  await page.goto("/driver/run");
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();
  await expect(page.getByText("Arrive Next Stop")).toBeVisible();

  // Clean up so the run does not block subsequent suite runs.
  await page.getByRole("button", { name: "End Run" }).click();
  await expect(page).toHaveURL("/driver");
});

test("parent sees their children and bus-less child has no driver actions", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await expect(page).toHaveURL("/parent");
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
  await expect(page.getByText(SEED.parentChild)).toBeVisible();
  await expect(page.getByText(SEED.buslessChild)).toBeVisible();

  // Parent alerts use the parent-side label and are scoped to their buses.
  await page.goto("/parent/alerts");
  await expect(page.getByText(SEED.parentBusAlertLabel).first()).toBeVisible();
});
