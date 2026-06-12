import { expect, type APIRequestContext, type Page } from "@playwright/test";

// Seeded local credentials (backend/db/seeds/002_live_demo_seed.sql).
export const ADMIN = { email: "admin@test.com", password: "test1234." };
export const PARENT = { email: "and7005@gmail.com", password: "Test1234" };
export const DRIVER = { email: "and7005@yahoo.it", password: "Test1234", pin: "1234" };

export const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:9001";

export async function emailLogin(page: Page, email: string, password: string) {
  await page.goto("/auth");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  // Wait for the post-login redirect so the bearer token is persisted before
  // any follow-up navigation reloads the app.
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"));
}

export async function pinLogin(page: Page, pin: string) {
  await page.goto("/auth");
  await page.getByRole("tab", { name: /Driver PIN/ }).click();
  await page.locator("#pin").fill(pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await page.waitForURL("/driver");
}

// API-side helpers for cross-role test setup/teardown ------------------------

export async function apiToken(
  request: APIRequestContext,
  email: string,
  password: string,
): Promise<string> {
  const response = await request.post(`${API_URL}/api/auth/login`, {
    data: { email, password },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).token;
}

export async function apiDriverToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API_URL}/api/auth/pin-login`, {
    data: { pin: DRIVER.pin },
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).token;
}

export function authHeaders(token: string) {
  return { Authorization: `Bearer ${token}` };
}

/** End the demo driver's active run if one exists (idempotent cleanup). */
export async function endActiveRun(request: APIRequestContext): Promise<void> {
  const token = await apiDriverToken(request);
  const context = await request.get(`${API_URL}/api/runs/driver/context`, {
    headers: authHeaders(token),
  });
  if (!context.ok()) return;
  const activeRun = (await context.json()).active_run;
  if (activeRun) {
    await request.post(`${API_URL}/api/runs/driver/end`, {
      headers: authHeaders(token),
      data: { run_id: activeRun.id },
    });
  }
}

export function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
}

// Admin dialog forms render <Label>Text</Label><Input/> without htmlFor, so
// fields are located through their shared container.
import type { Locator } from "@playwright/test";

export function fieldInput(scope: Locator, label: string): Locator {
  return scope.locator(`div:has(> label:text-is("${label}"))`).locator("input, textarea").first();
}

export async function pickSelectOption(scope: Locator, label: string, option: string | RegExp) {
  await scope.locator(`div:has(> label:text-is("${label}"))`).locator("button").first().click();
  await scope.page().getByRole("option", { name: option }).first().click();
}

/** The shadcn Card root (class bg-card) containing the given text. */
export function cardContaining(page: Page, text: string | RegExp): Locator {
  return page.locator("div[class*='bg-card']").filter({ hasText: text }).first();
}

