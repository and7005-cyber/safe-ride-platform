import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { ADMIN, API_URL, apiToken, authHeaders, signInAs, uniqueName } from "./helpers";

// U9 — the fleet-plan surface (F1–F4): confirm fleet → draft → review →
// apply, plus the R23 acknowledgment gate, the supersede warning, and the
// one-level restore toggle.
//
// Serial-safe by construction: every test builds its OWN school + fleet +
// students through the API ("E2E Plan" prefix) and applies plans only to that
// school, so the seeded Greenfield fixtures other spec files depend on are
// never claimed, re-routed, or notified. A safety sweep before the suite
// removes leftovers from earlier aborted runs; each test tears its fixture
// down again (school deletion cascades the plan documents).

const PREFIX = "E2E Plan";

interface PlanFixture {
  headers: { Authorization: string };
  school: any;
  buses: any[];
  students: any[];
}

async function createPlanFixture(
  request: APIRequestContext,
  opts: {
    buses: Array<{ capacity: number }>;
    students: Array<{ lat: number; lng: number }>;
  },
): Promise<PlanFixture> {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = authHeaders(token);

  const schoolResp = await request.post(`${API_URL}/api/fleet/schools`, {
    headers,
    data: {
      name: uniqueName(`${PREFIX} School`),
      address: "1 Plan Lane, Nairobi",
      phone: "+254700000001",
      lat: -1.3005,
      lng: 36.8102,
      morning_bell: "07:30",
      afternoon_bell: "15:30",
    },
  });
  expect(schoolResp.ok()).toBeTruthy();
  const school = await schoolResp.json();

  const buses: any[] = [];
  for (const [i, spec] of opts.buses.entries()) {
    const busResp = await request.post(`${API_URL}/api/fleet/buses`, {
      headers,
      data: {
        name: uniqueName(`${PREFIX} Bus ${i + 1}`),
        plate_number: `E2P ${100 + i}`,
        capacity: spec.capacity,
        availability: "in-service",
      },
    });
    expect(busResp.ok()).toBeTruthy();
    buses.push(await busResp.json());
  }

  const students: any[] = [];
  for (const [i, spec] of opts.students.entries()) {
    const studentResp = await request.post(`${API_URL}/api/students`, {
      headers,
      data: {
        name: uniqueName(`${PREFIX} Child ${i + 1}`),
        grade: "4",
        parent_name: "E2E Plan Parent",
        parent_phone: "+254700123456",
        parent_email: `e2e-plan-${Date.now()}-${i}@example.com`,
        home_address: `Plan stop ${i + 1}, Nairobi`,
        home_lat: spec.lat,
        home_lng: spec.lng,
        school_id: school.id,
        route_ids: [],
      },
    });
    expect(studentResp.ok()).toBeTruthy();
    students.push(await studentResp.json());
  }

  return { headers, school, buses, students };
}

async function destroyPlanFixture(request: APIRequestContext, fx: PlanFixture) {
  // Routes first (apply may have materialized some), then students, buses,
  // and the school (plan rows cascade with it). Best-effort — the beforeAll
  // sweep catches anything an aborted run leaves behind.
  const routes = await request.get(`${API_URL}/api/fleet/routes`, { headers: fx.headers });
  if (routes.ok()) {
    for (const row of await routes.json()) {
      if (row.school_id === fx.school.id) {
        await request.delete(`${API_URL}/api/fleet/routes/${row.id}`, { headers: fx.headers });
      }
    }
  }
  for (const s of fx.students) {
    await request.delete(`${API_URL}/api/students/${s.id}`, { headers: fx.headers });
  }
  for (const b of fx.buses) {
    await request.delete(`${API_URL}/api/fleet/buses/${b.id}`, { headers: fx.headers });
  }
  await request.delete(`${API_URL}/api/fleet/schools/${fx.school.id}`, { headers: fx.headers });
}

test.beforeAll(async ({ request }) => {
  const token = await apiToken(request, ADMIN.email, ADMIN.password);
  const headers = authHeaders(token);
  const sweep: Array<{ list: string; del: (id: string) => string }> = [
    { list: "/api/fleet/routes", del: (id) => `/api/fleet/routes/${id}` },
    { list: "/api/students", del: (id) => `/api/students/${id}` },
    { list: "/api/fleet/buses", del: (id) => `/api/fleet/buses/${id}` },
    { list: "/api/fleet/schools", del: (id) => `/api/fleet/schools/${id}` },
  ];
  for (const entity of sweep) {
    const response = await request.get(`${API_URL}${entity.list}`, { headers });
    if (!response.ok()) continue;
    for (const row of await response.json()) {
      if (String(row.name ?? "").startsWith(PREFIX)) {
        await request.delete(`${API_URL}${entity.del(row.id)}`, { headers });
      }
    }
  }
});

async function openPlanPage(page: Page, schoolName: string) {
  await signInAs(page, ADMIN);
  await page.goto("/fleet-plan");
  // More than one school exists (the seeded one plus the fixture), so the
  // selector renders; pick the fixture school by name.
  await page.getByTestId("plan-school-select").click();
  await page.getByRole("option", { name: schoolName }).click();
}

async function confirmFleet(page: Page, fx: PlanFixture) {
  for (const bus of fx.buses) {
    await page.getByRole("checkbox", { name: `Use ${bus.name}` }).check();
  }
  await page.getByTestId("plan-confirm-fleet").click();
  // The stepper advances to the draft step once the claim lands.
  await expect(page.getByTestId("plan-generate")).toBeVisible();
}

async function generateDraft(page: Page) {
  await page.getByTestId("plan-generate").click();
  // Review renders roster panels once the draft round-trip completes; the
  // solver caps itself at ~5s and the keyless local stack is far quicker.
  await expect(page.getByTestId(/^plan-bus-/).first()).toBeVisible({ timeout: 30_000 });
}

test("admin can confirm a fleet, draft, move a child, and apply — routes land on the Routes page", async ({
  page,
  request,
}) => {
  const fx = await createPlanFixture(request, {
    buses: [{ capacity: 15 }, { capacity: 15 }],
    students: [
      { lat: -1.291, lng: 36.8 },
      { lat: -1.295, lng: 36.79 },
      { lat: -1.305, lng: 36.825 },
      { lat: -1.31, lng: 36.83 },
    ],
  });
  try {
    await openPlanPage(page, fx.school.name);
    await confirmFleet(page, fx);
    await generateDraft(page);

    // Review edit (R9): move the first child — both legs by default.
    const child = fx.students[0];
    await page.getByRole("button", { name: `Move ${child.name}` }).first().click();
    const moveDialog = page.getByRole("dialog");
    await moveDialog.getByTestId("move-bus-select").click();
    await page.getByRole("option", { name: fx.buses[1].name }).click();
    await moveDialog.getByTestId("move-save").click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // The child now rides bus 2 on the morning leg.
    await expect(
      page
        .getByTestId(`plan-leg-${fx.buses[1].id}-morning`)
        .getByTestId("plan-stop-row")
        .filter({ hasText: child.name })
        .first(),
    ).toBeVisible();

    // Apply with zero gate items (nothing changed since drafting, nobody
    // unplaceable): the dialog needs no checkbox and applies straight away.
    await page.getByTestId("plan-to-apply").click();
    await page.getByTestId("plan-apply").click();
    const applyDialog = page.getByTestId("apply-dialog");
    await expect(applyDialog).toBeVisible();
    await expect(applyDialog.getByTestId("gate-list")).toHaveCount(0);
    await expect(applyDialog.getByTestId("ack-list")).toHaveCount(0);
    await applyDialog.getByTestId("apply-confirm").click();
    await expect(page.getByTestId("apply-result")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("apply-result")).toContainText("Plan applied");

    // The applied routes are live on the Routes page, plan-ordered.
    const routesResp = await request.get(`${API_URL}/api/fleet/routes`, {
      headers: fx.headers,
    });
    expect(routesResp.ok()).toBeTruthy();
    const schoolRoutes = (await routesResp.json()).filter(
      (r: any) => r.school_id === fx.school.id,
    );
    expect(schoolRoutes.length).toBeGreaterThanOrEqual(2);
    await page.goto("/routes");
    await expect(page.getByText(schoolRoutes[0].name).first()).toBeVisible();
    await expect(page.getByText("Plan order").first()).toBeVisible();
  } finally {
    await destroyPlanFixture(request, fx);
  }
});

test("applying with an unplaceable child requires ticking their name", async ({
  page,
  request,
}) => {
  // One 1-seat bus, two children: the solver seats one and names the other
  // unplaceable ("seats"), per leg.
  const fx = await createPlanFixture(request, {
    buses: [{ capacity: 1 }],
    students: [
      { lat: -1.291, lng: 36.8 },
      { lat: -1.305, lng: 36.825 },
    ],
  });
  try {
    await openPlanPage(page, fx.school.name);
    await confirmFleet(page, fx);
    await generateDraft(page);

    // The unplaceable panel names the child and the binding constraint.
    await expect(page.getByTestId("unplaceable-panel")).toBeVisible();

    await page.getByTestId("plan-to-apply").click();
    await page.getByTestId("plan-apply").click();
    const applyDialog = page.getByTestId("apply-dialog");
    await expect(applyDialog.getByTestId("ack-list")).toBeVisible();

    // R23: apply stays blocked until every (child, leg) is acknowledged by name.
    const acks = applyDialog.getByRole("checkbox", { name: /Acknowledge/ });
    await expect(applyDialog.getByTestId("apply-confirm")).toBeDisabled();
    const count = await acks.count();
    expect(count).toBeGreaterThanOrEqual(1);
    for (let i = 0; i < count; i++) {
      await acks.nth(i).check();
    }
    await expect(applyDialog.getByTestId("apply-confirm")).toBeEnabled();
    await applyDialog.getByTestId("apply-confirm").click();
    await expect(page.getByTestId("apply-result")).toBeVisible({ timeout: 30_000 });
  } finally {
    await destroyPlanFixture(request, fx);
  }
});

test("starting a second draft warns about superseding the open one", async ({
  page,
  request,
}) => {
  const fx = await createPlanFixture(request, {
    buses: [{ capacity: 15 }],
    students: [{ lat: -1.291, lng: 36.8 }],
  });
  try {
    await openPlanPage(page, fx.school.name);
    await confirmFleet(page, fx);
    await generateDraft(page);

    // Back to the draft step: the button now reads "Draft again" and sits
    // behind the supersede confirm (one open draft per school).
    await page.getByTestId("plan-step-draft").click();
    await page.getByRole("button", { name: /Draft again/ }).click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toContainText("supersedes");
    await expect(confirmDialog).toContainText("discarded");
    await confirmDialog.getByRole("button", { name: "Supersede and re-draft" }).click();
    await expect(page.getByTestId(/^plan-bus-/).first()).toBeVisible({ timeout: 30_000 });
  } finally {
    await destroyPlanFixture(request, fx);
  }
});

test("restore returns the prior routes and shows the one-level toggle copy", async ({
  page,
  request,
}) => {
  const fx = await createPlanFixture(request, {
    buses: [{ capacity: 15 }],
    students: [
      { lat: -1.291, lng: 36.8 },
      { lat: -1.305, lng: 36.825 },
    ],
  });
  try {
    await openPlanPage(page, fx.school.name);
    await confirmFleet(page, fx);
    await generateDraft(page);
    await page.getByTestId("plan-to-apply").click();
    await page.getByTestId("plan-apply").click();
    await page.getByTestId("apply-dialog").getByTestId("apply-confirm").click();
    await expect(page.getByTestId("apply-result")).toBeVisible({ timeout: 30_000 });

    const applied = await request.get(`${API_URL}/api/fleet/routes`, { headers: fx.headers });
    const appliedCount = (await applied.json()).filter(
      (r: any) => r.school_id === fx.school.id,
    ).length;
    expect(appliedCount).toBeGreaterThanOrEqual(2);

    // Restore sits behind a confirm stating the one-level rule; the preserved
    // plan captured the pre-apply live state (no routes), so restoring
    // returns the school to exactly that.
    await expect(page.getByTestId("restore-one-level")).toContainText(
      "Only one step back exists.",
    );
    await page.getByTestId("plan-restore").click();
    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toContainText("Only one step back exists.");
    await expect(confirmDialog).toContainText("becomes restorable in its place");
    await confirmDialog.getByRole("button", { name: "Restore" }).click();
    await expect(page.getByTestId("apply-result")).toContainText("Plan restored", {
      timeout: 30_000,
    });

    const restored = await request.get(`${API_URL}/api/fleet/routes`, { headers: fx.headers });
    const restoredCount = (await restored.json()).filter(
      (r: any) => r.school_id === fx.school.id,
    ).length;
    expect(restoredCount).toBe(0);
  } finally {
    await destroyPlanFixture(request, fx);
  }
});

test("a slot-in proposal appears for a mid-year enrolment and Accept places the child", async ({
  page,
  request,
}) => {
  // U12: with an applied plan live, a plannable enrolment lacking routes gets
  // a one-line proposal in the Proposals section; Accept applies just that
  // insertion — the child ends up linked on both legs.
  const fx = await createPlanFixture(request, {
    buses: [{ capacity: 15 }],
    students: [
      { lat: -1.291, lng: 36.8 },
      { lat: -1.305, lng: 36.825 },
    ],
  });
  try {
    await openPlanPage(page, fx.school.name);
    await confirmFleet(page, fx);
    await generateDraft(page);
    await page.getByTestId("plan-to-apply").click();
    await page.getByTestId("plan-apply").click();
    await page.getByTestId("apply-dialog").getByTestId("apply-confirm").click();
    await expect(page.getByTestId("apply-result")).toBeVisible({ timeout: 30_000 });

    // Mid-year enrolment through the API: resolved coordinates, no routes.
    const enrolResp = await request.post(`${API_URL}/api/students`, {
      headers: fx.headers,
      data: {
        name: uniqueName(`${PREFIX} Child New`),
        grade: "4",
        parent_name: "E2E Plan Parent",
        parent_phone: "+254700123499",
        parent_email: `e2e-plan-new-${Date.now()}@example.com`,
        home_address: "Plan stop new, Nairobi",
        home_lat: -1.298,
        home_lng: 36.815,
        school_id: fx.school.id,
        route_ids: [],
      },
    });
    expect(enrolResp.ok()).toBeTruthy();
    const newChild = await enrolResp.json();
    fx.students.push(newChild); // teardown removes it with the fixture

    // Generation runs in the backend's background tasks — wait for the
    // stored proposal before asserting the UI renders it.
    await expect
      .poll(
        async () => {
          const res = await request.get(
            `${API_URL}/api/fleet-plans/slot-ins?school_id=${fx.school.id}`,
            { headers: fx.headers },
          );
          if (!res.ok()) return 0;
          const body = await res.json();
          return body.proposals.filter((p: any) => p.student_id === newChild.id).length;
        },
        { timeout: 30_000 },
      )
      .toBe(1);

    // The Proposals section (visible whenever an applied plan exists) shows
    // the one-line statement; Accept places the child.
    await openPlanPage(page, fx.school.name);
    const proposals = page.getByTestId("plan-proposals");
    await expect(proposals).toBeVisible();
    await expect(proposals.getByText(newChild.name, { exact: false })).toBeVisible();
    await proposals.getByRole("button", { name: "Accept" }).click();
    await expect(proposals.getByTestId("proposals-empty")).toBeVisible({ timeout: 30_000 });

    // Placed: the child is linked on both legs of the applied plan.
    await expect
      .poll(async () => {
        const res = await request.get(`${API_URL}/api/students`, { headers: fx.headers });
        if (!res.ok()) return -1;
        const rows = await res.json();
        return rows.find((s: any) => s.id === newChild.id)?.route_ids?.length ?? -1;
      })
      .toBe(2);
  } finally {
    await destroyPlanFixture(request, fx);
  }
});
