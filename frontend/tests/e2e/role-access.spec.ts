import { expect, test } from "@playwright/test";
import { ADMIN, DRIVER, PARENT, emailLogin, pinLogin } from "./helpers";

// Role-based access control: every role is fenced into its own surface.

const ADMIN_ROUTES = ["/", "/fleet-map", "/buses", "/routes", "/students", "/runs", "/schools", "/parent-assignments", "/parents", "/drivers", "/alerts"];
const DRIVER_ROUTES = ["/driver", "/driver/run", "/driver/boarding", "/driver/incident"];
const PARENT_ROUTES = ["/parent", "/parent/track", "/parent/alerts", "/parent/profile"];

test("unauthenticated visitors are redirected to /auth from every protected route", async ({ page }) => {
  for (const route of [...ADMIN_ROUTES, ...DRIVER_ROUTES, ...PARENT_ROUTES]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /auth`).toHaveURL(/\/auth/);
  }
});

test("parent cannot reach admin or driver surfaces", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  for (const route of ["/buses", "/students", "/drivers", "/alerts", "/driver", "/driver/run"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /parent`).toHaveURL("/parent");
  }
});

test("driver cannot reach admin or parent surfaces", async ({ page }) => {
  await pinLogin(page, DRIVER.pin);
  for (const route of ["/buses", "/students", "/parents", "/parent", "/parent/alerts"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /driver`).toHaveURL("/driver");
  }
});

test("admin cannot reach driver or parent surfaces", async ({ page }) => {
  await emailLogin(page, ADMIN.email, ADMIN.password);
  for (const route of ["/driver", "/parent", "/parent/profile"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /`).toHaveURL("/");
  }
});

test("unknown routes show the 404 page", async ({ page }) => {
  await page.goto("/this-route-does-not-exist");
  await expect(page.getByText(/404|not found/i).first()).toBeVisible();
});
