import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    permissions: ["geolocation"],
    geolocation: { latitude: -1.2921, longitude: 36.8219 },
  },
  webServer: {
    command: "npm run dev -- --host 0.0.0.0",
    reuseExistingServer: true,
    url: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
  },
});
