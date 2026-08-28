import { expect, test } from "@playwright/test";
import {
  ADMIN,
  COORDINATOR_A,
  DIRECTOR_A,
  PARENT,
  SCHOOL_A_ID,
  SEED,
  signInAs,
  signInAsDriver,
} from "./helpers";

// Role-based access control: every role is fenced into its own surface, and
// inside the staff surface the U12 matrix applies — directors manage staff
// and delete records, coordinators run the day-to-day without deletes.

const ADMIN_ROUTES = ["/", "/fleet-map", "/buses", "/routes", "/fleet-plan", "/students", "/runs", "/settings", "/staff", "/parents", "/drivers", "/alerts"];
const DRIVER_ROUTES = ["/driver", "/driver/run", "/driver/boarding", "/driver/incident"];
const PARENT_ROUTES = ["/parent", "/parent/track", "/parent/alerts", "/parent/profile"];

test("unauthenticated visitors are redirected to /auth from every protected route", async ({ page }) => {
  for (const route of [...ADMIN_ROUTES, ...DRIVER_ROUTES, ...PARENT_ROUTES]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /auth`).toHaveURL(/\/auth/);
  }
});

test("parent cannot reach admin or driver surfaces", async ({ page }) => {
  await signInAs(page, PARENT);
  for (const route of ["/buses", "/students", "/drivers", "/alerts", "/staff", "/settings", "/parent-assignments", "/driver", "/driver/run"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /parent`).toHaveURL("/parent");
  }
});

test("driver cannot reach admin or parent surfaces", async ({ page }) => {
  await signInAsDriver(page);
  for (const route of ["/buses", "/students", "/parents", "/staff", "/parent-assignments", "/parent", "/parent/alerts"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /driver`).toHaveURL("/driver");
  }
});

test("/parent-assignments redirects to the students page (page removed)", async ({ page }) => {
  // Unauthenticated visitors still end up fenced at /auth via /students.
  await page.goto("/parent-assignments");
  await expect(page).toHaveURL(/\/auth/);

  // Admins land on /students, where assignment now happens in the form (R12).
  await signInAs(page, ADMIN, SCHOOL_A_ID);
  await page.goto("/parent-assignments");
  await expect(page).toHaveURL("/students");
});

test("/schools redirects to the school settings page (page removed, U12)", async ({ page }) => {
  await signInAs(page, ADMIN, SCHOOL_A_ID);
  await page.goto("/schools");
  await expect(page).toHaveURL("/settings");
  await expect(page.getByRole("heading", { name: "School Settings" })).toBeVisible();
});

test("admin cannot reach driver or parent surfaces", async ({ page }) => {
  await signInAs(page, ADMIN, SCHOOL_A_ID);
  for (const route of ["/driver", "/parent", "/parent/profile"]) {
    await page.goto(route);
    await expect(page, `route ${route} should bounce to /`).toHaveURL("/");
  }
});

test("director sees the Staff entry and the delete controls (U12)", async ({ page }) => {
  await signInAs(page, DIRECTOR_A, SCHOOL_A_ID);
  await expect(page.getByRole("link", { name: "Staff" })).toBeVisible();
  await page.goto("/buses");
  await expect(page.getByText(SEED.busOption).first()).toBeVisible();
  expect(await page.getByTitle("Delete bus").count()).toBeGreaterThan(0);
  await page.goto("/staff");
  await expect(page).toHaveURL("/staff");
  await expect(page.getByRole("heading", { name: "Staff" })).toBeVisible();
});

test("coordinator gets no Staff entry, no deletes, and /staff bounces (U12/R8)", async ({ page }) => {
  await signInAs(page, COORDINATOR_A, SCHOOL_A_ID);

  // The nav offers no Staff entry; a direct URL bounces to the dashboard.
  await expect(page.getByRole("link", { name: "Staff" })).toHaveCount(0);
  await page.goto("/staff");
  await page.waitForURL("/");

  // Delete controls are absent across the record pages — while the records
  // themselves still render (the pages are otherwise fully usable).
  await page.goto("/buses");
  await expect(page.getByText(SEED.busOption).first()).toBeVisible();
  await expect(page.getByTitle("Delete bus")).toHaveCount(0);
  await page.goto("/routes");
  await expect(page.getByTitle("Delete route")).toHaveCount(0);
  await page.goto("/students");
  await expect(page.getByText(SEED.parentChild).first()).toBeVisible();
  await expect(page.getByTitle("Delete student")).toHaveCount(0);
  await page.goto("/drivers");
  await expect(page.getByTitle("Delete driver")).toHaveCount(0);
  await page.goto("/parents");
  await expect(page.getByTitle("Delete parent")).toHaveCount(0);
  await page.goto("/alerts");
  await expect(page.getByTitle("Delete alert")).toHaveCount(0);

  // Run History: completed runs offer no delete to a coordinator; only a
  // NON-completed run (open-run cleanup, AE27) may show one.
  await page.goto("/runs");
  const completedRows = page.getByRole("row").filter({ has: page.getByText("Completed", { exact: true }) });
  // Auto-waits for the query to land — a bare count() races the page load.
  await expect(completedRows.first()).toBeVisible();
  await expect(completedRows.first().getByTitle("Delete run")).toHaveCount(0);

  // The coordinator still works inside the active school card (no switcher —
  // a single membership renders the plain card).
  await expect(page.getByTestId("active-school-card")).toContainText(SEED.school);
});

test("unknown routes show the 404 page", async ({ page }) => {
  await page.goto("/this-route-does-not-exist");
  await expect(page.getByText(/404|not found/i).first()).toBeVisible();
});
