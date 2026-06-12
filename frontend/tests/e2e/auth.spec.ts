import { expect, test } from "@playwright/test";
import { ADMIN, API_URL, DRIVER, PARENT, emailLogin, pinLogin, uniqueName } from "./helpers";

// Authentication journeys: login, errors, signup, logout, password reset.

test("wrong password shows the server message without a session-expired sign-out", async ({ page }) => {
  await page.goto("/auth");
  await page.locator("#email").fill(PARENT.email);
  await page.locator("#password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();

  await expect(page.getByText("Invalid email or password").first()).toBeVisible();
  await expect(page).toHaveURL(/\/auth/);
});

test("wrong PIN shows an error and stays on the auth page", async ({ page }) => {
  await page.goto("/auth");
  await page.getByRole("tab", { name: /Driver PIN/ }).click();
  await page.locator("#pin").fill("9999");
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();

  await expect(page.getByText(/Invalid PIN|Invalid email or password|Invalid/i).first()).toBeVisible();
  await expect(page).toHaveURL(/\/auth/);
});

test("each role lands on its home after login", async ({ page }) => {
  await emailLogin(page, ADMIN.email, ADMIN.password);
  await expect(page).toHaveURL("/");

  await page.goto("/parent/profile").catch(() => {});
  await emailLoginAfterSignOutViaStorage(page);

  await emailLogin(page, PARENT.email, PARENT.password);
  await expect(page).toHaveURL("/parent");

  await emailLoginAfterSignOutViaStorage(page);
  await pinLogin(page, DRIVER.pin);
  await expect(page).toHaveURL("/driver");
});

async function emailLoginAfterSignOutViaStorage(page: import("@playwright/test").Page) {
  await page.evaluate(() => localStorage.removeItem("saferide-token"));
}

test("parent can sign out from the profile page", async ({ page }) => {
  await emailLogin(page, PARENT.email, PARENT.password);
  await page.goto("/parent/profile");
  await page.getByRole("button", { name: "Sign Out", exact: true }).click();
  await page.waitForURL(/\/auth/);

  // The session is really gone: a protected page bounces back to /auth.
  await page.goto("/parent");
  await expect(page).toHaveURL(/\/auth/);
});

test("a new parent can sign up and reaches the parent home", async ({ page }) => {
  const email = `e2e-parent-${Date.now()}@test.local`;
  await page.goto("/auth");
  await page.getByRole("button", { name: "Sign up", exact: true }).click();
  await page.locator("#fullName").fill(uniqueName("E2E Parent"));
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("Passw0rd!");
  await page.getByRole("button", { name: "Create Account" }).click();

  // Signup signs the user in; new parents land on the parent home.
  await page.waitForURL("/parent");
  await expect(
    page.getByText("No children are linked to your account yet.").first(),
  ).toBeVisible({ timeout: 10_000 });
});

test("forgot password issues a usable reset link", async ({ page, request }) => {
  // Use a throwaway account so the seeded parent password stays canonical.
  const email = `e2e-reset-${Date.now()}@test.local`;
  const signup = await request.post(`${API_URL}/api/auth/signup`, {
    data: { email, password: "FirstPass1!", full_name: "Reset Tester", role: "parent" },
  });
  expect(signup.ok()).toBeTruthy();

  await page.goto("/auth");
  await page.getByText("Forgot password?").click();
  await page.locator("#email").fill(email);
  await page.getByRole("button", { name: "Send Reset Link" }).click();
  await expect(page.getByText(/Reset link sent/).first()).toBeVisible();

  // Local dev exposes the last reset link for the email-less flow.
  const linkResponse = await request.get(`${API_URL}/api/auth/dev/last-reset-link`);
  const { link } = await linkResponse.json();
  expect(link).toBeTruthy();

  await page.goto(link.replace(/^https?:\/\/[^/]+/, ""));
  await page.locator("#password").fill("SecondPass2!");
  await page.locator("#confirm").fill("SecondPass2!");
  await page.getByRole("button", { name: "Update Password" }).click();
  await page.waitForURL(/\/auth/);

  // Old password no longer works; the new one does.
  const oldLogin = await request.post(`${API_URL}/api/auth/login`, {
    data: { email, password: "FirstPass1!" },
  });
  expect(oldLogin.status()).toBe(401);

  await emailLogin(page, email, "SecondPass2!");
  await expect(page).toHaveURL("/parent");
});
