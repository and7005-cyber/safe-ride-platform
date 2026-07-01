import { expect, test, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  DRIVER,
  SEED,
  apiToken,
  authHeaders,
  cardContaining,
  confirmDelete,
  emailLogin,
  fieldInput,
  pickSelectOption,
  uniqueName,
} from "./helpers";

// Admin console CRUD across every entity. Entities created here carry an
// "E2E" name prefix and are deleted again inside each test; a safety sweep
// before the suite removes leftovers from earlier aborted runs.

test.beforeAll(async ({ request }) => {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = authHeaders(token);
  const sweep: Array<{ list: string; del: (id: string) => string; name?: string }> = [
    { list: "/api/fleet/routes", del: (id) => `/api/fleet/routes/${id}` },
    { list: "/api/students", del: (id) => `/api/students/${id}` },
    { list: "/api/fleet/buses", del: (id) => `/api/fleet/buses/${id}` },
    { list: "/api/fleet/schools", del: (id) => `/api/fleet/schools/${id}` },
    { list: "/api/accounts/drivers", del: (id) => `/api/accounts/drivers/${id}` },
  ];
  for (const entity of sweep) {
    const response = await request.get(`${API_URL}${entity.list}`, { headers });
    if (!response.ok()) continue;
    for (const row of await response.json()) {
      const label = row.name ?? row.full_name ?? "";
      if (label.startsWith("E2E ")) {
        await request.delete(`${API_URL}${entity.del(row.id)}`, { headers });
      }
    }
  }
});

async function adminLogin(page: Page) {
  await emailLogin(page, ADMIN.email, ADMIN.password);
}

function dialog(page: Page) {
  return page.getByRole("dialog");
}

test("admin can create, edit, search, and delete a bus", async ({ page }) => {
  const name = uniqueName("E2E Bus");
  const renamed = `${name} Renamed`;
  await adminLogin(page);
  await page.goto("/buses");

  await page.getByRole("button", { name: "Add Bus" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  await fieldInput(dialog(page), "Plate number").fill("KZZ 999E");
  await fieldInput(dialog(page), "Capacity").fill("18");
  await pickSelectOption(dialog(page), "Status", "Active");
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  // Search narrows the table to the new bus.
  await page.getByPlaceholder("Search buses, plates, drivers…").fill("KZZ 999E");
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();
  await page.getByPlaceholder("Search buses, plates, drivers…").fill("");

  const row = page.getByRole("row", { name: new RegExp(name) });
  await row.getByRole("button").first().click(); // pencil
  await fieldInput(dialog(page), "Name").fill(renamed);
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toBeVisible();

  await page.getByRole("row", { name: new RegExp(renamed) }).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toHaveCount(0);
});

test("admin can create and delete a school with a map location", async ({ page }) => {
  const name = uniqueName("E2E School");
  await adminLogin(page);
  await page.goto("/schools");

  await page.getByRole("button", { name: "Add School" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  await fieldInput(dialog(page), "Address").fill("1 Test Lane, Nairobi");
  await fieldInput(dialog(page), "Phone").fill("+254700999999");
  await dialog(page).getByTestId("map-picker").click({ position: { x: 150, y: 120 } });
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(name)).toBeVisible();

  await cardContaining(page, name).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByText(name)).toHaveCount(0);
});

test("admin can create and delete a route attached to a bus and school", async ({ page }) => {
  const name = uniqueName("E2E Route");
  await adminLogin(page);
  await page.goto("/routes");

  await page.getByRole("button", { name: "Add Route" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  await pickSelectOption(dialog(page), "Type", "Afternoon");
  await pickSelectOption(dialog(page), "Bus", SEED.busOption);
  await pickSelectOption(dialog(page), "School", new RegExp(SEED.school));
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(name)).toBeVisible();

  // Routes render as cards; the trash button is the card's last icon button.
  await cardContaining(page, name).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByText(name)).toHaveCount(0);
});

test("admin can create, edit, and delete a student", async ({ page }) => {
  const name = uniqueName("E2E Student");
  const renamed = `${name} Jr`;
  await adminLogin(page);
  await page.goto("/students");

  await page.getByRole("button", { name: "Add Student" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  await fieldInput(dialog(page), "Grade").fill("Grade 4");
  // Parent contact fields live in two labeled groups (Parent 1 / Parent 2).
  const parent1 = dialog(page).getByTestId("parent1-group");
  const parent2 = dialog(page).getByTestId("parent2-group");
  await fieldInput(parent1, "Name").fill("E2E Parent Contact");
  await fieldInput(parent1, "Phone").fill("+254711111111");

  // No email in either slot: save is blocked with an inline invariant message.
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(dialog(page).getByText("At least one parent email is required")).toBeVisible();
  await expect(dialog(page)).toBeVisible();

  // A Parent 2 email satisfies the cross-slot invariant (parent 1 phone + parent 2 email).
  await fieldInput(parent2, "Email").fill("e2e-parent2@test.local");

  // Clicking the map fills the address via reverse geocoding — asserted only
  // when Google Maps loaded and the geocoder actually resolved (mock-free).
  const mapReady = await dialog(page)
    .locator(".gm-style")
    .first()
    .waitFor({ state: "visible", timeout: 10_000 })
    .then(() => true)
    .catch(() => false);
  if (mapReady) {
    const reverse = page
      .waitForResponse((r) => r.url().includes("/api/fleet/reverse-geocode"), { timeout: 10_000 })
      .catch(() => null);
    await dialog(page).getByTestId("map-picker").click({ position: { x: 150, y: 120 } });
    const response = await reverse;
    if (response && (await response.json()).found) {
      await expect(fieldInput(dialog(page), "Home address")).not.toHaveValue("");
    }
  }

  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  const row = page.getByRole("row", { name: new RegExp(name) });
  // Actions are [mark-absent, edit, delete]; the pencil is the second button.
  await row.getByRole("button").nth(1).click();
  // Status is live data — the dialog exposes no status select (R7).
  await expect(dialog(page).locator('label:text-is("Status")')).toHaveCount(0);
  await fieldInput(dialog(page), "Name").fill(renamed);
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toBeVisible();

  await page.getByRole("row", { name: new RegExp(renamed) }).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toHaveCount(0);
});

test("admin can create and delete a driver account with a PIN", async ({ page }) => {
  const name = uniqueName("E2E Driver");
  const email = `e2e-driver-${Date.now()}@test.local`;
  await adminLogin(page);
  await page.goto("/drivers");

  await page.getByRole("button", { name: "Add Driver" }).click();
  await fieldInput(dialog(page), "Full name").fill(name);
  await fieldInput(dialog(page), "Email").fill(email);
  await fieldInput(dialog(page), "Password").fill("DriverPass1!");
  await dialog(page).getByRole("button", { name: "Generate" }).click();
  await dialog(page).getByRole("button", { name: "Save" }).click();

  // The PIN is revealed once after saving so the admin can share it.
  const reveal = page.getByRole("dialog");
  await expect(reveal.getByText("Driver PIN set")).toBeVisible();
  await reveal.getByRole("button", { name: "Done" }).click();

  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  await page.getByRole("row", { name: new RegExp(name) }).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});

test("admin can edit and delete a registered parent account", async ({ page, request }) => {
  const email = `e2e-parent-acct-${Date.now()}@test.local`;
  const fullName = uniqueName("E2E ParentAcct");
  const renamed = `${fullName} Edited`;
  const signup = await request.post(`${API_URL}/api/auth/signup`, {
    data: { email, password: "ParentPass1!", full_name: fullName, role: "parent" },
  });
  expect(signup.ok()).toBeTruthy();

  await adminLogin(page);
  await page.goto("/parents");
  await expect(page.getByRole("row", { name: new RegExp(fullName) })).toBeVisible();

  const row = page.getByRole("row", { name: new RegExp(fullName) });
  await row.getByRole("button").first().click();
  await fieldInput(dialog(page), "Full name").fill(renamed);
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toBeVisible();

  await page.getByRole("row", { name: new RegExp(renamed) }).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByRole("row", { name: new RegExp(renamed) })).toHaveCount(0);
});

test("admin can link and unlink a parent-student assignment", async ({ page, request }) => {
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = authHeaders(adminToken);
  const studentName = uniqueName("E2E AssignKid");
  const created = await request.post(`${API_URL}/api/students`, {
    headers,
    data: { name: studentName, grade: "Grade 1", parent_name: "", parent_phone: "" },
  });
  expect(created.ok()).toBeTruthy();
  const studentId = (await created.json()).id;

  try {
    await adminLogin(page);
    await page.goto("/parent-assignments");

    await page.getByText("Select parent").click();
    await page.getByRole("option", { name: new RegExp(SEED.parentName) }).click();
    await page.getByText("Select student").click();
    await page.getByRole("option", { name: new RegExp(studentName) }).click();
    await page.getByRole("button", { name: "Assign" }).click();
    await expect(page.getByRole("row", { name: new RegExp(studentName) })).toBeVisible();

    await page.getByRole("row", { name: new RegExp(studentName) }).getByRole("button").last().click();
    await confirmDelete(page);
    await expect(page.getByRole("row", { name: new RegExp(studentName) })).toHaveCount(0);
  } finally {
    await request.delete(`${API_URL}/api/students/${studentId}`, { headers });
  }
});

test("admin can add and delete a manual run record", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/runs");

  await page.getByRole("button", { name: "Add Run" }).click();
  await pickSelectOption(dialog(page), "Bus", SEED.busOption);
  await pickSelectOption(dialog(page), "Type", "Afternoon");
  await pickSelectOption(dialog(page), "Status", "Completed");
  await fieldInput(dialog(page), "Date").fill("2030-01-01");
  await dialog(page).getByRole("button", { name: "Save" }).click();

  const row = page.getByRole("row", { name: /2030-01-01/ });
  await expect(row).toBeVisible();
  await row.getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByRole("row", { name: /2030-01-01/ })).toHaveCount(0);
});

test("admin can acknowledge and delete a driver incident", async ({ page, request }) => {
  // A driver files an incident through the API...
  const pinLoginResponse = await request.post(`${API_URL}/api/auth/pin-login`, {
    data: { pin: DRIVER.pin },
  });
  const driverToken = (await pinLoginResponse.json()).token;
  const marker = uniqueName("E2E incident");
  const reported = await request.post(`${API_URL}/api/incidents/driver`, {
    headers: authHeaders(driverToken),
    data: { type: "other", description: marker },
  });
  expect(reported.ok()).toBeTruthy();

  // ...and the admin sees it, acknowledges it, and clears it.
  await adminLogin(page);
  await page.goto("/alerts");
  await expect(page.getByText(marker)).toBeVisible();

  await cardContaining(page, marker).getByRole("button", { name: /Ack/ }).click();
  await expect(cardContaining(page, marker).getByText("Acknowledged")).toBeVisible();

  await cardContaining(page, marker).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByText(marker)).toHaveCount(0);
});

test("dashboard and fleet map render live fleet data", async ({ page }) => {
  await adminLogin(page);
  await expect(page.getByText("Active Buses")).toBeVisible();
  await expect(page.getByText("Fleet Status")).toBeVisible();
  await expect(page.getByText(SEED.driverBus).first()).toBeVisible();

  await page.goto("/fleet-map");
  await expect(page.getByText("Live bus positions")).toBeVisible();
  // Embedded Google Map renders (container + Google's own .gm-style wrapper).
  await expect(page.getByTestId("fleet-map")).toBeVisible();
  await expect(page.locator(".gm-style").first()).toBeVisible({ timeout: 15_000 });
});

test("route planner returns a Google traffic-aware route", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/fleet-map");
  await expect(page.getByText("Route planner")).toBeVisible();

  // Two real Nairobi addresses (free text — the backend geocodes them).
  await page.getByTestId("address-input-0").fill("Yaya Centre, Nairobi");
  await page.getByRole("button", { name: "Add stop" }).click();
  await page.getByTestId("address-input-1").fill("Sarit Centre, Westlands, Nairobi");

  await page.getByTestId("get-route-options").click();

  // An ordered, enriched route comes back with totals and stops.
  await expect(page.getByTestId("route-result")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Optimised (traffic-aware)" })).toBeVisible();
  await expect(page.getByTestId("route-distance")).not.toHaveText("—");
  await expect(page.getByTestId("route-stops").locator("li")).toHaveCount(2);

  // Reorder (drag-to-reorder uses the same backend path) recomputes the route.
  await page.getByRole("button", { name: "Move down" }).first().click();
  await expect(page.getByTestId("route-distance")).not.toHaveText("—");
});
