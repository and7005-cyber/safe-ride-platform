import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  API_URL,
  PARENT,
  SEED,
  apiDriverToken,
  apiToken,
  authHeaders,
  emailLogin,
  endActiveRun,
} from "./helpers";

// Cross-role notification pipeline: driver run events fan out to typed parent
// notifications (the same records FCM/Web Push would deliver) and surface in
// the parent Alerts feed with unread state.

async function startMorningRun(request: APIRequestContext): Promise<string> {
  const token = await apiDriverToken(request);
  const headers = authHeaders(token);
  const context = await request.get(`${API_URL}/api/runs/driver/context`, { headers });
  const routes = (await context.json()).routes;
  const morning = routes.find((r: any) => r.type === "morning");
  const started = await request.post(`${API_URL}/api/runs/driver/start`, {
    headers,
    data: { route_id: morning.id },
  });
  expect(started.ok()).toBeTruthy();
  return (await started.json()).id;
}

test.afterEach(async ({ request }) => {
  await endActiveRun(request);
});

test("a driver run produces typed notifications in the parent alerts feed", async ({
  page,
  request,
}) => {
  const runId = await startMorningRun(request);
  const driverToken = await apiDriverToken(request);
  const driverHeaders = authHeaders(driverToken);

  // Drive past the first stop and board the parent's child.
  await request.post(`${API_URL}/api/runs/driver/arrive`, {
    headers: driverHeaders,
    data: { run_id: runId },
  });
  const parentToken = await apiToken(request, PARENT.email, PARENT.password);
  const children = await (
    await request.get(`${API_URL}/api/parent-portal/children`, {
      headers: authHeaders(parentToken),
    })
  ).json();
  const child = children.find((c: any) => c.name === SEED.parentChild);
  await request.post(`${API_URL}/api/runs/driver/boarding`, {
    headers: driverHeaders,
    data: { student_id: child.id, on_bus: true },
  });
  // Complete the run at the school gate.
  await request.post(`${API_URL}/api/runs/driver/end`, {
    headers: driverHeaders,
    data: { run_id: runId },
  });

  // The parent sees every stage in the alerts feed.
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/alerts");
  await expect(page.getByText("Bus On The Way").first()).toBeVisible();
  await expect(page.getByText("Boarded the Bus").first()).toBeVisible();
  await expect(page.getByText("Arrived at School").first()).toBeVisible();
  await expect(page.getByText(new RegExp(`has boarded ${SEED.driverBus}`)).first()).toBeVisible();
  await expect(page.getByText(/has reached school safely/).first()).toBeVisible();

  // Period filter (R33): these run events carry run_type = morning, so they
  // survive the Morning toggle and vanish under Afternoon (null-run_type rows
  // such as incidents only show under All).
  const feedCards = page.locator("div[class*='bg-card']");
  await page.getByRole("button", { name: "Morning", exact: true }).click();
  await expect(feedCards.filter({ hasText: "Boarded the Bus" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Afternoon", exact: true }).click();
  await expect(feedCards.filter({ hasText: "Boarded the Bus" })).toHaveCount(0);
  await page.getByRole("button", { name: "All", exact: true }).click();

  // Type filter (R33): narrowing to the boarded label leaves only boarded rows.
  await page.getByRole("combobox", { name: "Filter by type" }).click();
  await page.getByRole("option", { name: "Boarded the Bus" }).click();
  await expect(feedCards.filter({ hasText: "Boarded the Bus" }).first()).toBeVisible();
  await expect(feedCards.filter({ hasText: "Bus On The Way" })).toHaveCount(0);
  await expect(feedCards.filter({ hasText: "Arrived at School" })).toHaveCount(0);

  // History (R35): the 7-day window includes everything just created.
  await page.getByRole("tab", { name: "History" }).click();
  await expect(feedCards.filter({ hasText: "Boarded the Bus" }).first()).toBeVisible();
});

test("opening the alerts page marks notifications as read", async ({ page, request }) => {
  await startMorningRun(request); // generates fresh unread notifications
  const parentToken = await apiToken(request, PARENT.email, PARENT.password);

  // The run-started rows are written by a BackgroundTask after the start
  // response, so poll instead of asserting a single snapshot.
  await expect
    .poll(
      async () => {
        const before = await (
          await request.get(`${API_URL}/api/push/notifications/unread-count`, {
            headers: authHeaders(parentToken),
          })
        ).json();
        return before.count;
      },
      { timeout: 10_000, message: "run-started notifications should arrive" },
    )
    .toBeGreaterThan(0);

  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/alerts");
  await expect(page.getByText("Bus On The Way").first()).toBeVisible();

  await expect
    .poll(
      async () => {
        const after = await (
          await request.get(`${API_URL}/api/push/notifications/unread-count`, {
            headers: authHeaders(parentToken),
          })
        ).json();
        return after.count;
      },
      { timeout: 10_000 },
    )
    .toBe(0);
});

test("an incident report notifies parents on that bus", async ({ page, request }) => {
  const driverToken = await apiDriverToken(request);
  const marker = `E2E breakdown ${Date.now()}`;
  await request.post(`${API_URL}/api/incidents/driver`, {
    headers: authHeaders(driverToken),
    data: { type: "breakdown", description: marker },
  });

  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/alerts");
  await expect(page.getByText(marker).first()).toBeVisible();
  await expect(page.getByText("Vehicle Breakdown").first()).toBeVisible();

  // The notification feed recorded it for push delivery too.
  const parentToken = await apiToken(request, PARENT.email, PARENT.password);
  const feed = await (
    await request.get(`${API_URL}/api/push/notifications`, {
      headers: authHeaders(parentToken),
    })
  ).json();
  const entry = feed.find((n: any) => n.body === marker);
  expect(entry).toBeTruthy();
  expect(entry.type).toBe("incident");
});

test("notification types are scoped to the right parent", async ({ request }) => {
  // A parent with no children on the demo driver's bus must see none of its run alerts.
  const runId = await startMorningRun(request);
  const driverToken = await apiDriverToken(request);
  await request.post(`${API_URL}/api/runs/driver/end`, {
    headers: authHeaders(driverToken),
    data: { run_id: runId },
  });

  const signupEmail = `e2e-scoped-${Date.now()}@test.local`;
  await request.post(`${API_URL}/api/auth/signup`, {
    data: { email: signupEmail, password: "Scoped123!", full_name: "Scoped Parent", role: "parent" },
  });
  const token = await apiToken(request, signupEmail, "Scoped123!");
  const feed = await (
    await request.get(`${API_URL}/api/push/notifications`, { headers: authHeaders(token) })
  ).json();
  expect(feed).toHaveLength(0);
});
