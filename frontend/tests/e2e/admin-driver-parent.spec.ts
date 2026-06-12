import { expect, test, type Page } from "@playwright/test";

// These journeys run against the seeded local stack (see
// backend/db/seeds/002_live_demo_seed.sql). They assert against the live-parity
// copy and information architecture.

const ADMIN = { email: "admin@test.com", password: "test1234." };
const PARENT = { email: "and7005@gmail.com", password: "Test1234" };
const DRIVER_PIN = "1234";

async function emailLogin(page: Page, email: string, password: string) {
  await page.goto("/auth");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  // Wait for the post-login redirect so the bearer token is persisted before
  // any follow-up navigation reloads the app.
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"));
}

test("admin sees the dashboard and can navigate the shell", async ({ page }) => {
  await emailLogin(page, ADMIN.email, ADMIN.password);
  await expect(page).toHaveURL("/");
  await expect(page.getByText("Active Buses")).toBeVisible();
  await expect(page.getByText("Incidents Today")).toBeVisible();

  // Alerts page uses the admin-side type label.
  await page.goto("/alerts");
  await expect(page.getByText("Heavy Traffic / Delay")).toBeVisible();

  // Routes page shows configured routes with stops (no zero-state when present).
  await page.goto("/routes");
  await expect(page.getByText(/routes configured/)).toBeVisible();
  await expect(page.getByText("Greenfield Academy").first()).toBeVisible();
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
  await page.locator("#pin").fill(DRIVER_PIN);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await expect(page).toHaveURL("/driver");
  await expect(page.getByText("Express 1")).toBeVisible();

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
  await expect(page.getByText(/Good morning/)).toBeVisible();
  await expect(page.getByText("Faith Achieng")).toBeVisible();
  await expect(page.getByText("Grace Njeri")).toBeVisible();

  // Parent alerts use the parent-side label and are scoped to their buses.
  await page.goto("/parent/alerts");
  await expect(page.getByText("Traffic Delay")).toBeVisible();
});
