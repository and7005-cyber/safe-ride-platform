import { expect, test, type Page } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  DRIVER,
  SCHOOL_A_ID,
  SEED,
  apiCancelRide,
  apiDriverToken,
  apiToken,
  authHeaders,
  cardContaining,
  clearCancellationState,
  confirmDelete,
  signInAs,
  fieldInput,
  pickSelectOption,
  purgeRun,
  schoolHeaders,
  uniqueName,
} from "./helpers";

// Admin console CRUD across every entity. Entities created here carry an
// "E2E" name prefix and are deleted again inside each test; a safety sweep
// before the suite removes leftovers from earlier aborted runs.

test.beforeAll(async ({ request }) => {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  // U12: staff-surface calls carry the school scope; this suite works in the
  // seeded school A. School create/delete no longer exists, so the sweep has
  // no schools entry.
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const sweep: Array<{ list: string; del: (id: string) => string; name?: string }> = [
    { list: "/api/fleet/routes", del: (id) => `/api/fleet/routes/${id}` },
    { list: "/api/students", del: (id) => `/api/students/${id}` },
    { list: "/api/fleet/buses", del: (id) => `/api/fleet/buses/${id}` },
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
  await signInAs(page, ADMIN, SCHOOL_A_ID);
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
  // Availability, not status (U9): a bus's status is derived from its run now,
  // and the form's Status control is gone. Availability is the one thing no
  // derivation can produce — whether the bus is in the workshop is not a
  // function of its runs — so it stays office-set and overrides everything.
  await pickSelectOption(dialog(page), "Availability", "In service");
  await dialog(page).getByRole("button", { name: "Save" }).click();
  const created = page.getByRole("row", { name: new RegExp(name) });
  await expect(created).toBeVisible();
  // A bus with no run today derives to idle.
  await expect(created.getByText("Idle")).toBeVisible();

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

test("admin edits the active school's settings (no create or delete, U12/R23)", async ({
  page,
  request,
}) => {
  // The Schools page is gone: the console works inside ONE school whose
  // settings (name, bells, location) are edited here. Save a new afternoon
  // bell, verify it persisted, then restore the original value.
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const before = await (await request.get(`${API_URL}/api/fleet/school`, { headers })).json();

  await adminLogin(page);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "School Settings" })).toBeVisible();
  // The school's non-editable code is shown; there is no Add and no Delete.
  await expect(page.getByTestId("school-code")).toContainText(before.code ?? "");
  await expect(page.getByRole("button", { name: /Add School/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Delete/ })).toHaveCount(0);

  await page.getByTestId("afternoon-bell").fill("16:05");
  await page.getByRole("button", { name: "Save" }).click();
  // .first(): the toast text also lands in sonner's aria-live announcer.
  await expect(page.getByText("School settings saved").first()).toBeVisible();

  const after = await (await request.get(`${API_URL}/api/fleet/school`, { headers })).json();
  expect(after.afternoon_bell).toBe("16:05");
  expect(after.id).toBe(before.id);

  // Restore so later suites see the seeded bell again.
  await page.getByTestId("afternoon-bell").fill(before.afternoon_bell ?? "");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("School settings saved").first()).toBeVisible();
});

test("admin can create and delete a route attached to a bus and school", async ({ page, request }) => {
  const name = uniqueName("E2E Route");
  // Seeded buses already hold a route of each type; a same-type route on one
  // of them now 409s (R1). Attach the route to a fresh bus instead.
  const busName = uniqueName("E2E RouteBus");
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(adminToken, SCHOOL_A_ID);
  const busResp = await request.post(`${API_URL}/api/fleet/buses`, {
    headers,
    data: { name: busName, plate_number: "E2E 100", capacity: 20, status: "idle" },
  });
  expect(busResp.ok()).toBeTruthy();
  const busId = (await busResp.json()).id;

  try {
  await adminLogin(page);
  await page.goto("/routes");

  await page.getByRole("button", { name: "Add Route" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  // Spec 6 / R19: multi-trip — the route editor exposes a Trip number, and
  // spec 1 / R3 the gate-anchor time. Both are optional (trip defaults to 1).
  await expect(fieldInput(dialog(page), "Trip number")).toBeVisible();
  await expect(dialog(page).getByTestId("route-gate-anchor")).toBeVisible();
  await pickSelectOption(dialog(page), "Type", "Afternoon");
  await pickSelectOption(dialog(page), "Bus", busName);
  // U12: no School field — the route lands in the tab's active school.
  await dialog(page).getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(name)).toBeVisible();

  // Every route card carries a map preview (R22) or its key-less/stop-less
  // placeholder (R23) — never a broken map pane.
  await expect(
    cardContaining(page, name)
      .locator('[data-testid="route-map-preview"] .gm-style, [data-testid="route-map-placeholder"]')
      .first()
  ).toBeVisible({ timeout: 15_000 });

  // Routes render as cards; the trash button is the card's last icon button.
  await cardContaining(page, name).getByRole("button").last().click();
  await confirmDelete(page);
  await expect(page.getByText(name)).toHaveCount(0);
  } finally {
    await request.delete(`${API_URL}/api/fleet/buses/${busId}`, { headers });
  }
});

test("admin can create, edit, and delete a student", async ({ page }) => {
  const name = uniqueName("E2E Student");
  const renamed = `${name} Jr`;
  await adminLogin(page);
  await page.goto("/students");

  await page.getByRole("button", { name: "Add Student" }).click();
  await fieldInput(dialog(page), "Name").fill(name);
  await fieldInput(dialog(page), "Grade").fill("Grade 4");
  // Spec 5 / R10: the redundant "Home location" field is gone — a single
  // PlacePicker (label "Home", testid student-address, with map-picking inside)
  // is the only home control.
  await expect(dialog(page).getByTestId("student-address")).toBeVisible();
  await expect(dialog(page).locator('label:text-is("Home location")')).toHaveCount(0);
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
      // U11: the split Home address + Home location fields are now one
      // PlacePicker (label "Home"); its address input carries the student-address
      // testid. A picked pin reverse-geocodes into it.
      await expect(dialog(page).getByTestId("student-address")).not.toHaveValue("");
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

// Bulk upload (U10: R16-R18): the two-step validate → review flow. The upload
// is scoped to a school, rows are triaged (located / confirm pin / not
// located), and an ambiguous row's proposed pin is accepted with one click
// before anything is committed. The ambiguous tier rides the keyless
// fallback geocoder resolving a well-known place name ("Nairobi") — the same
// mock-free posture as the planner test's real-address geocoding above.
test("bulk upload triages rows and imports after a one-click pin confirm", async ({ page, request }) => {
  const nameA = uniqueName("E2E BulkA");
  const nameB = uniqueName("E2E BulkB");
  const csv = [
    "name,grade,parent_name,parent_phone,parent_email,home_address,home_lat,home_lng",
    `${nameA},Grade 2,E2E Bulk Parent,+254711222444,e2e-bulk-a@test.local,Kileleshwa,-1.2820,36.7780`,
    `${nameB},Grade 3,E2E Bulk Parent,+254711222555,e2e-bulk-b@test.local,Nairobi,,`,
  ].join("\n");

  await adminLogin(page);
  await page.goto("/students");
  await page.getByRole("button", { name: "Bulk Upload" }).click();

  // U12: the upload is scoped to the tab's active school — no picker, the
  // chooser is ready straight away.
  await expect(dialog(page).getByRole("button", { name: "Choose file" })).toBeEnabled();
  await dialog(page)
    .locator('input[type="file"]')
    .setInputFiles({ name: "students.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });

  // Step 1 triaged without inserting: the coords row is located, the
  // address-only row proposes a pin and waits for confirmation; the commit
  // button is gated until every proposal is resolved.
  const triage = dialog(page).getByTestId("bulk-triage");
  await expect(triage).toBeVisible({ timeout: 20_000 });
  await expect(dialog(page).getByTestId("bulk-triage-summary")).toHaveText(
    /1 located · 1 to confirm · 0 not located/,
  );
  await expect(triage.getByText(nameB)).toBeVisible();
  await expect(dialog(page).getByTestId("bulk-commit")).toBeDisabled();

  // One-click confirm of the proposed pin (row index 1 = the ambiguous row).
  await dialog(page).getByTestId("bulk-confirm-1").click();
  await expect(dialog(page).getByTestId("bulk-triage-summary")).toHaveText(
    /2 located · 0 to confirm · 0 not located/,
  );

  // Step 2 commits both rows.
  await expect(dialog(page).getByTestId("bulk-commit")).toBeEnabled();
  await dialog(page).getByTestId("bulk-commit").click();
  const result = dialog(page).getByTestId("bulk-result");
  await expect(result).toBeVisible({ timeout: 20_000 });
  await expect(result.getByText("2", { exact: true })).toBeVisible();
  await expect(result.getByText(/students inserted/)).toBeVisible();
  await dialog(page).getByRole("button", { name: "Done" }).click();

  // Both students landed on the roster.
  await expect(page.getByRole("row", { name: new RegExp(nameA) })).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(nameB) })).toBeVisible();

  // API cleanup (the beforeAll sweep also catches aborted runs).
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const students = await request.get(`${API_URL}/api/students`, { headers });
  for (const row of await students.json()) {
    if ([nameA, nameB].includes(row.name)) {
      await request.delete(`${API_URL}/api/students/${row.id}`, { headers });
    }
  }
});

// Aggregate pin map (U11/R19): every pin for the school on one audited view —
// the dialog reads ONLY the /pin-map endpoint (each fetch writes a
// 'pin-map-viewed' audit row server-side; asserted in
// backend/tests/integration/test_pin_map_audit.py) — and a named marker
// deep-links to the student's normal editor so a mis-placed pin is fixed in
// the regular flow. The `pin-marker-…` testid is carried by the map marker
// AND by the key-less fallback row, so the assertion holds either way.
test("pin map shows a named pin and jumps to the student editor", async ({ page, request }) => {
  const name = uniqueName("E2E PinKid");
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);

  // A student with a placed pin at the seeded school, staged via API — the
  // request scope stamps the school (U12).
  const created = await request.post(`${API_URL}/api/students`, {
    headers,
    data: {
      name, grade: "Grade 2", parent_name: "E2E Pin Parent",
      parent_phone: "+254711222666", parent_email: "e2e-pin-parent@test.local",
      home_address: "E2E Pin Lane, Nairobi", home_lat: -1.2921, home_lng: 36.8219,
      provenance: "imported", route_ids: [],
    },
  });
  expect(created.ok()).toBeTruthy();
  const studentId = (await created.json()).id;

  try {
    await adminLogin(page);
    await page.goto("/students");
    await page.getByRole("button", { name: "Pin map" }).click();
    // U12: the dialog shows the ACTIVE school's pins — no picker.

    // The audited aggregate answers with the student's named pin.
    await expect(dialog(page).getByTestId("pin-map-summary")).not.toHaveText(
      "Loading pins…", { timeout: 15_000 },
    );
    const marker = dialog(page).getByTestId(`pin-marker-${studentId}`);
    await expect(marker).toBeVisible({ timeout: 15_000 });
    await expect(marker).toContainText(name);

    // Clicking the pin lands in the NORMAL edit flow: same dialog, same
    // PlacePicker a mis-placed pin is fixed with — no parallel editor.
    await marker.click();
    const editDialog = page.getByRole("dialog").filter({ hasText: "Edit Student" });
    await expect(editDialog).toBeVisible();
    await expect(fieldInput(editDialog, "Name")).toHaveValue(name);
    await expect(editDialog.getByTestId("student-address")).toBeVisible();
    await editDialog.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  } finally {
    await request.delete(`${API_URL}/api/students/${studentId}`, { headers });
  }
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

  // U6/R33: the Parents page lists only parents linked to THIS school's
  // students — an unlinked signup is invisible here. Stage a student carrying
  // the parent's email so the account is linked (and later editable) at A.
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(adminToken, SCHOOL_A_ID);
  const studentResp = await request.post(`${API_URL}/api/students`, {
    headers,
    data: {
      name: uniqueName("E2E ParentAcct Kid"), grade: "Grade 1",
      parent_name: fullName, parent_phone: "+254711222444", parent_email: email,
      home_address: "Parent Lane, Nairobi", route_ids: [],
    },
  });
  expect(studentResp.ok()).toBeTruthy();
  const studentId = (await studentResp.json()).id;

  try {
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
  } finally {
    await request.delete(`${API_URL}/api/students/${studentId}`, { headers });
  }
});

test("creating a student with a registered parent's email links it to that parent", async ({ page, request }) => {
  // Parent ↔ student assignment happens in the student form now (R12): a
  // student carrying a registered parent's email is auto-linked on save.
  const adminToken = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(adminToken, SCHOOL_A_ID);

  const parentEmail = `e2e-assign-${Date.now()}@test.local`;
  const parentName = uniqueName("E2E AssignParent");
  const signup = await request.post(`${API_URL}/api/auth/signup`, {
    data: { email: parentEmail, password: "ParentPass1!", full_name: parentName, role: "parent" },
  });
  expect(signup.ok()).toBeTruthy();

  const studentName = uniqueName("E2E AssignKid");
  try {
    await adminLogin(page);
    await page.goto("/students");

    await page.getByRole("button", { name: "Add Student" }).click();
    await fieldInput(dialog(page), "Name").fill(studentName);
    await fieldInput(dialog(page), "Grade").fill("Grade 1");
    // Parent 1 slot: the registered parent's email drives the link.
    const parent1 = dialog(page).getByTestId("parent1-group");
    await fieldInput(parent1, "Name").fill(parentName);
    await fieldInput(parent1, "Phone").fill("+254711222333");
    await fieldInput(parent1, "Email").fill(parentEmail);
    await dialog(page).getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("row", { name: new RegExp(studentName) })).toBeVisible();

    // The parent's registered row now lists the student among its children.
    const parents = await request.get(`${API_URL}/api/accounts/parents`, { headers });
    expect(parents.ok()).toBeTruthy();
    const parentRow = (await parents.json()).find((p: any) => p.email === parentEmail);
    expect(parentRow?.status).toBe("registered");
    expect(parentRow?.students).toContain(studentName);
  } finally {
    const students = await request.get(`${API_URL}/api/students`, { headers });
    if (students.ok()) {
      const row = (await students.json()).find((s: any) => s.name === studentName);
      if (row) await request.delete(`${API_URL}/api/students/${row.id}`, { headers });
    }
    const parents = await request.get(`${API_URL}/api/accounts/parents`, { headers });
    if (parents.ok()) {
      const row = (await parents.json()).find((p: any) => p.email === parentEmail);
      if (row?.id) await request.delete(`${API_URL}/api/accounts/parents/${row.id}`, { headers });
    }
  }
});

test("admin can add and delete a manual run record", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/runs");

  await page.getByRole("button", { name: "Add Run" }).click();
  await pickSelectOption(dialog(page), "Bus", SEED.busOption);
  await pickSelectOption(dialog(page), "Route", /Express 1 — Afternoon/);
  await pickSelectOption(dialog(page), "Type", "Afternoon");
  await pickSelectOption(dialog(page), "Status", "Completed");
  await fieldInput(dialog(page), "Date").fill("2030-01-01");
  await dialog(page).getByRole("button", { name: "Save" }).click();

  const row = page.getByRole("row", { name: /2030-01-01/ });
  await expect(row).toBeVisible();

  // Clicking the row (not its action buttons) opens the read-only Run Report.
  await row.getByRole("cell", { name: "2030-01-01" }).click();
  await expect(dialog(page).getByText("Run Report")).toBeVisible();
  await expect(dialog(page).getByText("Express 1 — Afternoon")).toBeVisible();
  await expect(dialog(page).getByText("Students", { exact: true })).toBeVisible();
  await dialog(page).getByRole("button", { name: "Close" }).click();
  await expect(dialog(page)).toHaveCount(0);

  // The pencil still opens the edit form — not the report (stopPropagation).
  await row.getByRole("button").first().click();
  await expect(dialog(page).getByText("Edit Run")).toBeVisible();
  await expect(dialog(page).getByText("Run Report")).toHaveCount(0);
  await dialog(page).getByRole("button", { name: "Cancel" }).click();
  await expect(dialog(page)).toHaveCount(0);

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

// Route planner: calculate → save → reset (R17/R19/R20). CSV import (R21) is
// skipped here on purpose — its parser is covered by tests/unit/plannerCsv.test.ts.
test("route planner returns a Google traffic-aware route, saves it, and resets", async ({ page, request }) => {
  const routeName = uniqueName("E2E Planned");
  await adminLogin(page);
  await page.goto("/fleet-map");
  await expect(page.getByText("Route planner")).toBeVisible();
  // Spec 1 / R3: the planner exposes the gate-anchor time the schedule is
  // optimised against (arrival/departure at the school gate).
  await expect(page.getByTestId("gate-anchor")).toBeVisible();

  // Two real Nairobi addresses (free text — the backend geocodes them).
  await page.getByTestId("address-input-0").fill("Yaya Centre, Nairobi");
  await page.getByRole("button", { name: "Add stop" }).click();
  await page.getByTestId("address-input-1").fill("Sarit Centre, Westlands, Nairobi");

  await page.getByTestId("get-route-options").click();

  // An ordered, enriched route comes back with totals and stops. The two
  // addresses plus the school-gate anchor (U12: the plan is always solved
  // against the tab's active school, so the gate rides along as a stop).
  await expect(page.getByTestId("route-result")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Optimised (traffic-aware)" })).toBeVisible();
  await expect(page.getByTestId("route-distance")).not.toHaveText("—");
  await expect(page.getByTestId("route-stops").locator("li")).toHaveCount(3);
  await expect(page.getByTestId("route-stops").getByText("School", { exact: true })).toBeVisible();

  // Reorder (drag-to-reorder uses the same backend path) recomputes the route.
  await page.getByRole("button", { name: "Move down" }).first().click();
  await expect(page.getByTestId("route-distance")).not.toHaveText("—");
  await expect(page.getByTestId("save-to-routes")).toBeEnabled({ timeout: 20_000 });

  // Save the selected option as a route (R17): name + required school.
  await page.getByTestId("save-to-routes").click();
  await fieldInput(dialog(page), "Route name").fill(routeName);
  // U12: no School field — the route lands in the tab's active school.
  await dialog(page).getByTestId("confirm-save-route").click();

  // Success closes the dialog, confirms with a Routes link, and resets the planner (R19).
  await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByText("Route saved", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "View routes" })).toBeVisible();
  await expect(page.getByTestId("route-result")).toHaveCount(0);
  await expect(page.getByTestId("address-input-0")).toHaveValue("");

  // The route persisted with its ordered custom stops.
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = schoolHeaders(token, SCHOOL_A_ID);
  const listed = await request.get(`${API_URL}/api/fleet/routes`, { headers });
  expect(listed.ok()).toBeTruthy();
  const saved = (await listed.json()).find((r: { name: string }) => r.name === routeName);
  expect(saved).toBeTruthy();
  expect(saved.custom_stops).toBeTruthy();
  expect(saved.route_stops.length).toBeGreaterThan(0);

  // Reset clears planner state (R20): rows emptied and results gone.
  await page.getByTestId("address-input-0").fill("Somewhere, Nairobi");
  await page.getByTestId("reset-planner").click();
  await expect(page.getByTestId("address-input-0")).toHaveValue("");
  await expect(page.getByTestId("route-result")).toHaveCount(0);

  // API cleanup (the beforeAll sweep also catches aborted runs).
  await request.delete(`${API_URL}/api/fleet/routes/${saved.id}`, { headers });
});

// Cross-surface honesty of a parent cancellation (U14: R17, R19; AE1/AE4):
// a partial-scope cancellation never rewrites the day status, the office is
// alerted, and the Unassigned filter isolates route-less students.
test("a parent cancellation shows a scoped badge and an office alert without changing the student's status", async ({
  page,
  request,
}) => {
  await apiCancelRide(request, SEED.parentChild, "afternoon");
  try {
    await adminLogin(page);
    await page.goto("/students");

    const row = page.getByRole("row", { name: new RegExp(SEED.parentChild) });
    // Display honesty (R19): a partial cancellation gates that run's roster and
    // never rewrites the displayed day status. The selector pins the status
    // cell (index 6 — the School column left with the single-school console,
    // U12) — a day-absent student would read "Absent today" in both the name
    // badge and the status cell, and this assertion must fail for that shape.
    //
    // The undisturbed value is "At home", not "At school": since U3 the status
    // is derived from participation, and a child with none today is simply not
    // in the system's care. "At school" was the raw column's leftover from
    // whenever it was last written, which is the staleness the derivation
    // removed.
    await expect(row.getByRole("cell").nth(6).getByText("At home")).toBeVisible();
    // The name cell carries the scope-labelled absence badge (U10).
    await expect(row.getByText("Absent (PM)")).toBeVisible();
    await expect(row.getByText("Absent today")).toHaveCount(0);

    // The office channel: an unread "Ride Cancellation" alert naming the
    // covered route's bus (R17) — the parent appears only in the description.
    await page.goto("/alerts");
    const alert = cardContaining(page, "Ride Cancellation");
    await expect(alert).toBeVisible({ timeout: 10_000 });
    await expect(alert.getByText("New", { exact: true })).toBeVisible();
    await expect(alert.getByText(SEED.driverBus)).toBeVisible();
    await expect(
      alert.getByText(new RegExp(`${SEED.parentChild}: afternoon ride cancelled by parent`)),
    ).toBeVisible();

    // Unassigned filter (R3/R4; AE1): exactly the seeded route-less child.
    await page.goto("/students");
    await page.getByRole("combobox").first().click();
    await page.getByRole("option", { name: "Unassigned" }).click();
    const dataRows = page.locator("tbody tr");
    await expect(dataRows).toHaveCount(1);
    await expect(dataRows.first()).toContainText(SEED.buslessChild);
    await expect(dataRows.first()).toContainText("Unassigned");
  } finally {
    await clearCancellationState(request, SEED.parentChild);
  }
});

// Manual stop ordering (U14: R11, R13; AE3): arrow reorder persists across
// reload under a "Manual order" chip; Recalculate hands the route back to auto.
test("admin reorders route stops with the arrows and the manual order persists", async ({ page }) => {
  await adminLogin(page);
  await page.goto("/routes");
  const card = cardContaining(page, SEED.driverMorningRoute);

  // Seeded state: auto mode, three location groups then the school gate.
  // Assertions track location-group order only — sibling rows inside one
  // group share a stop_order and carry no defined intra-group order (U7).
  await expect(card.getByTestId("route-mode-chip")).toHaveText("Auto");
  const stops = card.locator("ol > li");
  await expect(stops).toHaveCount(4);
  await expect(stops.nth(0)).toContainText("Kilimani");
  await expect(stops.nth(1)).toContainText("Lavington");

  // Move the first group down one slot; the server flips the route to manual.
  await card.getByRole("button", { name: "Move down" }).first().click();
  await expect(stops.nth(0)).toContainText("Lavington");
  await expect(stops.nth(1)).toContainText("Kilimani");
  await expect(card.getByTestId("route-mode-chip")).toHaveText("Manual order");

  // The order is persisted server-side, not a client-side illusion.
  await page.reload();
  await expect(card.getByTestId("route-mode-chip")).toHaveText("Manual order");
  await expect(stops.nth(0)).toContainText("Lavington");
  await expect(stops.nth(1)).toContainText("Kilimani");
  await expect(stops.nth(3)).toContainText("School gate");

  // Recalculate is the explicit exit: the route returns to auto ordering
  // (also the cleanup — later suites expect the seeded route in auto mode).
  await card.getByTestId("recalculate-order").click();
  await expect(card.getByTestId("route-mode-chip")).toHaveText("Auto", { timeout: 20_000 });
});

// Office resolution of a stuck run (U14: R12, R14, R26). The gate can refuse to
// close a run, so the office needs a way out that records what is true — the
// children nobody accounted for — and then tracks the phone calls it owes,
// because no automated message goes to those families.

test("the office force-closes a stuck run and discharges the contact obligation", async ({
  page,
  request,
}) => {
  const driverToken = await apiDriverToken(request);
  const driverHeaders = authHeaders(driverToken);
  const context = await (
    await request.get(`${API_URL}/api/runs/driver/context`, { headers: driverHeaders })
  ).json();
  const afternoon = context.routes.find((r: any) => r.type === "afternoon");
  const started = await request.post(`${API_URL}/api/runs/driver/start`, {
    headers: driverHeaders,
    data: { route_id: afternoon.id },
  });
  expect(started.ok()).toBeTruthy();
  const runId = (await started.json()).id;

  try {
    await adminLogin(page);
    await page.goto("/runs");
    const row = page.getByRole("row", { name: new RegExp(SEED.driverAfternoonRoute) }).first();

    // The copy has to say both things it is easy to conflate: unaccounted is not
    // a location, and nobody is messaged automatically.
    await row.getByTestId(`force-close-${runId}`).click();
    const confirmBox = page.getByRole("dialog");
    await expect(confirmBox.getByText("Force-close this run?")).toBeVisible();
    await expect(confirmBox.getByText(/not the same as saying where they are/)).toBeVisible();
    await expect(confirmBox.getByText(/No parent is notified automatically/)).toBeVisible();
    await confirmBox.getByRole("button", { name: "Force-close" }).click();

    // The call list lands in front of whoever did it, naming each child.
    const report = page.getByRole("dialog");
    await expect(report.getByText("Unaccounted children — phone their families")).toBeVisible();
    const firstCall = report.getByRole("button", { name: "Mark called" }).first();
    await expect(firstCall).toBeVisible();
    const outstanding = await report.getByRole("button", { name: "Mark called" }).count();
    await firstCall.click();
    await expect(report.getByText("Family called").first()).toBeVisible();
    await expect(report.getByRole("button", { name: "Mark called" })).toHaveCount(outstanding - 1);

    // Dismissed and reloaded, the obligation is still findable on the list — a
    // one-shot dialog would lose it the moment the office user was pulled away.
    await page.keyboard.press("Escape");
    await expect(report).toHaveCount(0);
    await page.reload();
    const reloaded = page.getByRole("row", { name: new RegExp(SEED.driverAfternoonRoute) }).first();
    await expect(reloaded.getByTestId("contact-pending")).toContainText(
      `${outstanding - 1} to call`,
    );
  } finally {
    purgeRun(runId);
  }
});

test("the buses page offers availability and no status control", async ({ page }) => {
  // R23: the stored bus status is retired — derived from the bus's run now — so
  // the form must not offer a control that writes a column nothing reads.
  await adminLogin(page);
  await page.goto("/buses");
  await page.getByRole("button", { name: "Add Bus" }).click();
  const form = dialog(page);
  await expect(form.locator('div:has(> label:text-is("Availability"))')).toBeVisible();
  await expect(form.locator('div:has(> label:text-is("Status"))')).toHaveCount(0);
  await form.getByRole("button", { name: "Cancel" }).click();
});
