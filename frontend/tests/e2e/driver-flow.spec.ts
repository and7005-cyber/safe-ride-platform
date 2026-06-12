import { expect, test } from "@playwright/test";
import { API_URL, DRIVER, apiDriverToken, authHeaders, endActiveRun, pinLogin } from "./helpers";

// Driver journey: PIN login, run lifecycle, GPS, boarding, incident report.

test.afterEach(async ({ request }) => {
  await endActiveRun(request); // never leave an in-progress run behind
});

test("driver home shows the assigned bus and stat tiles", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await expect(page.getByText(/Hello,/)).toBeVisible();
  await expect(page.getByText("Express 1")).toBeVisible();
  await expect(page.getByText("Stops")).toBeVisible();
  await expect(page.getByText("Students")).toBeVisible();
  await expect(page.getByText("Depart")).toBeVisible();
});

test("driver completes a full run: start, GPS, arrive, board, end", async ({ page, request }) => {
  await pinLogin(page, DRIVER.pin);

  await page.goto("/driver/run");
  await page.getByRole("button", { name: "Start Run" }).click();
  await expect(page.getByText("Run in progress")).toBeVisible();

  // Browser geolocation (granted in playwright.config) streams positions to
  // the backend; the bus becomes live on the admin fleet map.
  await expect
    .poll(
      async () => {
        const token = await apiDriverToken(request);
        const context = await request.get(`${API_URL}/api/runs/driver/context`, {
          headers: authHeaders(token),
        });
        return (await context.json()).bus?.current_lat;
      },
      { timeout: 15_000, message: "driver GPS should reach the backend" },
    )
    .not.toBeNull();

  // Reach the first stop, then board a student there.
  await page.getByRole("button", { name: "Arrive Next Stop" }).click();
  await expect(page.getByText(/1\/\d+ stops completed/)).toBeVisible();

  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();
  const boardButton = page.getByRole("button", { name: "Board", exact: true }).first();
  await expect(boardButton).toBeEnabled();
  await boardButton.click();
  await expect(page.getByText("On bus").first()).toBeVisible();

  // Boarded counter moved off zero (the count renders just above its label).
  await expect(
    page.getByText("Boarded", { exact: true }).locator("xpath=preceding-sibling::span[1]"),
  ).not.toHaveText("0");

  await page.goto("/driver/run");
  await page.getByRole("button", { name: "End Run" }).click();
  await page.waitForURL("/driver");
});

test("driver can report an incident", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await page.goto("/driver/incident");
  await expect(page.getByText("New Incident Report")).toBeVisible();

  // Pick a type, describe, submit.
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "Heavy Traffic / Delay" }).click();
  await page.getByPlaceholder("Describe what happened…").fill("E2E traffic report from the driver flow spec.");
  await page.getByRole("button", { name: "Submit Report" }).click();
  await expect(page.getByText("Incident reported").first()).toBeVisible();
});

test("search filters the boarding list", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  await page.goto("/driver/boarding");
  await expect(page.getByText(/Students \(\d+\)/)).toBeVisible();

  await page.getByPlaceholder("Search students…").fill("Brian");
  await expect(page.getByText("Brian Achieng")).toBeVisible();
  await expect(page.getByText(/Students \(1\)/)).toBeVisible();
});
