import { expect, test } from "@playwright/test";
import {
  ADMIN,
  API_URL,
  DRIVER,
  PARENT,
  SEED,
  emailLogin,
  lastMailLink,
  linkPath,
  pinLogin,
  signInAsDriver,
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
  // Hardening U10: the phone is set up for the school first (the link).
  await page.goto(`/auth?school=${DRIVER.driverCode}`);
  await page.getByRole("button", { name: "Yes, that's my school" }).click();
  await page.locator("#pin").fill("9999");
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();

  await expect(page.getByText("Invalid PIN").first()).toBeVisible();
  await expect(page).toHaveURL(/\/auth/);
});

// --- Driver sign-in bound to the school (hardening U10; AE8, AE10; DL3–DL5) --

test("a driver sets the phone up with the office's link, signs in by PIN, and the school stays after sign-out", async ({
  page,
}) => {
  await page.goto(`/auth?school=${DRIVER.driverCode.toLowerCase()}`);
  // The link opens the Driver PIN tab and asks before storing anything.
  await expect(page.getByRole("tab", { name: /Driver PIN/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Is this your school?")).toBeVisible();
  await expect(page.getByTestId("confirm-school-name")).toHaveText(SEED.school);
  await page.getByRole("button", { name: "Yes, that's my school" }).click();
  // Stored, and the code is stripped from the address.
  await expect(page).toHaveURL(/\/auth$/);
  await expect(page.getByTestId("driver-school-name")).toHaveText(SEED.school);

  await page.locator("#pin").fill(DRIVER.pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await page.waitForURL("/driver");

  // Sign out: the next visit opens on the PIN form under the school's name.
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/auth/);
  await expect(page.getByRole("tab", { name: /Driver PIN/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("driver-school-name")).toHaveText(SEED.school);
  await expect(page.getByLabel("Driver code")).toHaveCount(0);
});

test("a phone that is not set up asks for the driver code; Change school asks first; a wrong code says so", async ({
  page,
}) => {
  await page.goto("/auth");
  await page.getByRole("tab", { name: /Driver PIN/ }).click();
  await expect(page.getByText("Enter the driver code from your school office")).toBeVisible();
  await expect(page.locator("#pin")).toHaveCount(0);

  const code = page.getByLabel("Driver code");
  // The school code is a different code (DL5).
  await code.fill("GFA-001");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "That isn't a driver code. The school code in Settings is a different code.",
  );
  // An unknown (or replaced) driver code.
  await code.fill("ZZZZ-ZZZZ");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "That code didn't match a school. Ask your school office for the current code or link.",
  );
  // Typed however the driver reads it: lower case, O for 0, I for 1.
  await code.fill(DRIVER.driverCode.toLowerCase().replace("0", "o").replace("1", "i"));
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByTestId("confirm-school-name")).toHaveText(SEED.school);
  await page.getByRole("button", { name: "No, enter a different code" }).click();
  await expect(code).toHaveValue("");

  await code.fill(DRIVER.driverCode);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Yes, that's my school" }).click();
  await expect(page.getByTestId("driver-school-name")).toHaveText(SEED.school);

  // Change school is one stray tap from losing the code: it asks first.
  await page.getByRole("button", { name: "Change school" }).click();
  const ask = page.getByRole("alertdialog");
  await expect(ask).toContainText("You'll need the code again.");
  await ask.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByTestId("driver-school-name")).toHaveText(SEED.school);
  await page.getByRole("button", { name: "Change school" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Change school" }).click();
  await expect(page.getByLabel("Driver code")).toBeVisible();
  await expect(page.getByTestId("driver-school-name")).toHaveCount(0);
});

test("the school lock shows its message and counts down instead of 'Invalid PIN'", async ({
  page,
}) => {
  // The lock itself (twenty-one failures from one source at one school) is
  // proven against the database in test_driver_pin_scoping.py; here the
  // screen's answer to it, with the API's 429 stood in.
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Expose-Headers": "Retry-After, X-Request-Id",
  };
  await page.route("**/api/auth/pin-login", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    await route.fulfill({
      status: 429,
      headers: { ...cors, "Content-Type": "application/json", "Retry-After": "120" },
      body: JSON.stringify({
        detail: {
          code: "school-pin-locked",
          message: "Sign-in for your school is paused for a few minutes. Try again shortly.",
          retry_after_s: 120,
        },
      }),
    });
  });
  await page.goto(`/auth?school=${DRIVER.driverCode}`);
  await page.getByRole("button", { name: "Yes, that's my school" }).click();
  await page.locator("#pin").fill(DRIVER.pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();

  await expect(page.getByTestId("pin-locked")).toHaveText(
    "Sign-in for your school is paused for a few minutes. Try again in 2 minutes.",
  );
  await expect(page.getByRole("button", { name: /Sign In with PIN/ })).toBeDisabled();
  await expect(page.getByText("Invalid PIN")).toHaveCount(0);
  await expect(page).toHaveURL(/\/auth/);
});

test("a driver signed in on a phone that is not set up adopts the session's school (AE10)", async ({
  page,
}) => {
  await signInAsDriver(page);
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("saferide-driver-school")))
    .toContain(DRIVER.schoolId);
  // The next PIN sign-in asks for nothing: straight to the PIN form.
  await page.evaluate(() => localStorage.removeItem("saferide-token"));
  await page.goto("/auth");
  await expect(page.getByTestId("driver-school-name")).toHaveText(SEED.school);
  await page.locator("#pin").fill(DRIVER.pin);
  await page.getByRole("button", { name: /Sign In with PIN/ }).click();
  await page.waitForURL("/driver");
});

test("each role lands on its home after login", async ({ page }) => {
  await emailLogin(page, ADMIN.email, ADMIN.password);
  await expect(page).toHaveURL("/");

  await page.goto("/parent/profile").catch(() => {});
  await emailLoginAfterSignOutViaStorage(page);

  await emailLogin(page, PARENT.email, PARENT.password);
  await expect(page).toHaveURL("/parent");

  await emailLoginAfterSignOutViaStorage(page);
  await pinLogin(page, DRIVER.pin, DRIVER.driverCode);
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
