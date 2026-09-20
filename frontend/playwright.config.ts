import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  // The suites share one seeded database; serial execution keeps run
  // lifecycles (one active run per bus per day) deterministic.
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    permissions: ["geolocation"],
    // A realistic accuracy: Playwright's default is 0, which the plausibility
    // safeguard (GPS plan U12) flags on every fix a spec posts.
    geolocation: { latitude: -1.2921, longitude: 36.8219, accuracy: 20 },
  },
  webServer: {
    command: "npm run dev -- --host 0.0.0.0",
    reuseExistingServer: true,
    url: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
  },
});
