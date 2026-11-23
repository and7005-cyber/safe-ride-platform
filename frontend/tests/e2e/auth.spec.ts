import { expect, test } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  DRIVER,
  PARENT,
  emailLogin,
  lastMailLink,
  linkPath,
  pinLogin,
  uniqueName,
} from "./helpers";

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

test("a new parent signs up, reads 'Check your email', signs in and sees the verify card", async ({
  page,
}) => {
  const email = `e2e-parent-${Date.now()}@test.local`;
  await page.goto("/auth");
  await page.getByRole("button", { name: "Sign up", exact: true }).click();
  await page.locator("#fullName").fill(uniqueName("E2E Parent"));
  await page.locator("#email").fill(email);
  await page.locator("#password").fill("Passw0rd!");
  await page.getByRole("button", { name: "Create Account" }).click();

  // Release 1b (DL2): no session from sign-up; the card stays on "Check your
  // email" with the address, and Sign in prefills it.
  const done = page.getByTestId("signup-done");
  await expect(done).toContainText("Check your email");
  await expect(done).toContainText(email);
  await expect(page).toHaveURL(/\/auth/);
  await done.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("#email")).toHaveValue(email);
  await page.locator("#password").fill("Passw0rd!");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();

  // Home while unverified (DL9): the verify card, not the empty state.
  await page.waitForURL("/parent");
  const card = page.getByTestId("verify-email-card");
  await expect(card).toContainText(`We sent a link to ${email}`);
  await expect(card.getByRole("button", { name: "Resend link" })).toBeVisible();
  await expect(page.getByText("No children are linked to your account yet.")).toHaveCount(0);
});

test("sign-up offers parent only: the role choice is gone (U13)", async ({ page }) => {
  // Staff join through their school's offers and drivers are set up by the
  // school office — the form has no role select and no driver option at all.
  // domcontentloaded: under the full parallel run the dev server can serve
  // the last asset slowly; this assertion set needs the DOM, not the "load"
  // event, and the expect below still auto-waits for hydration.
  await page.goto("/auth", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Sign up", exact: true }).click();

  await expect(page.getByText(/This creates a parent account/)).toBeVisible();
  await expect(page.getByText("I am a")).toHaveCount(0);
  await expect(page.locator("#role")).toHaveCount(0);
  await expect(page.getByText("Driver", { exact: true })).toHaveCount(0);
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
  // DL11: a persistent in-card state, not a vanishing toast.
  const sent = page.getByTestId("forgot-sent");
  await expect(sent).toContainText(`If an account exists for ${email}`);
  await expect(sent).toContainText("It works once and lasts 30 minutes.");

  // Local dev exposes the last reset link for the email-less flow.
  const link = await lastMailLink(request, "reset");

  await page.goto(linkPath(link));
  await page.locator("#password").fill("SecondPass2!");
  await page.locator("#confirm").fill("SecondPass2!");
  await page.getByRole("button", { name: "Update Password" }).click();
  // Release 1b: the reset lands signed in (and verifies the address).
  await page.waitForURL("/parent");
  await expect(page.getByTestId("verify-email-card")).toHaveCount(0);

  // The link is single-use: opening it again shows the invalid card.
  await page.goto(linkPath(link));
  await page.locator("#password").fill("ThirdPass3!");
  await page.locator("#confirm").fill("ThirdPass3!");
  await page.getByRole("button", { name: "Update Password" }).click();
  await expect(page.getByText("Reset link invalid")).toBeVisible();

  // Old password no longer works; the new one does.
  const oldLogin = await request.post(`${API_URL}/api/auth/login`, {
    data: { email, password: "FirstPass1!" },
  });
  expect(oldLogin.status()).toBe(401);

  await page.evaluate(() => localStorage.removeItem("saferide-token"));
  await emailLogin(page, email, "SecondPass2!");
  await expect(page).toHaveURL("/parent");
});
