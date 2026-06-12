import { expect, test } from "@playwright/test";

// PWA installability and service-worker plumbing.

test("the web app manifest is served and complete", async ({ request, baseURL }) => {
  const response = await request.get(`${baseURL}/manifest.webmanifest`);
  expect(response.ok()).toBeTruthy();
  const manifest = await response.json();

  expect(manifest.name).toBe("SafeRide Kenya");
  expect(manifest.short_name).toBe("SafeRide");
  expect(manifest.display).toBe("standalone");
  expect(manifest.start_url).toBe("/");
  expect(manifest.theme_color).toBe("#206F4A");

  const sizes = manifest.icons.map((icon: any) => icon.sizes);
  expect(sizes).toContain("192x192");
  expect(sizes).toContain("512x512");
  expect(manifest.icons.some((icon: any) => icon.purpose === "maskable")).toBeTruthy();
});

test("manifest icons and favicon resolve", async ({ request, baseURL }) => {
  for (const path of [
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/icon-maskable-512.png",
    "/icons/apple-touch-icon.png",
    "/icons/favicon-32.png",
  ]) {
    const response = await request.get(`${baseURL}${path}`);
    expect(response.ok(), `${path} should be served`).toBeTruthy();
    expect(response.headers()["content-type"]).toContain("image/png");
  }
});

test("index.html declares the PWA metadata", async ({ page }) => {
  await page.goto("/auth");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.webmanifest");
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#206F4A");
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveAttribute(
    "href",
    "/icons/apple-touch-icon.png",
  );
});

test("the service worker registers and activates", async ({ page }) => {
  await page.goto("/auth");
  const state = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    return registration.active?.state ?? "none";
  });
  expect(["activated", "activating"]).toContain(state);
});

test("the service worker handles push payloads in both FCM and raw shapes", async ({
  request,
  baseURL,
}) => {
  const sw = await (await request.get(`${baseURL}/sw.js`)).text();
  expect(sw).toContain('addEventListener("push"');
  expect(sw).toContain('addEventListener("notificationclick"');
  // Tolerates both raw {title, body} and FCM {notification: {...}} payloads.
  expect(sw).toContain("raw.notification");
  expect(sw).toContain("raw.title");
});
