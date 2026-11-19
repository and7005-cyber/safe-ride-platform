import { afterEach, describe, expect, it, vi } from "vitest";

// Hardening U16: production builds get the Cloud-styled Map ID through
// VITE_GOOGLE_MAPS_MAP_ID (deploy-frontend.sh reads it from the parameter
// store); without it the app keeps Google's demo Map ID, which local
// development and the tests run on. MAP_ID is read once at module load, so each
// case stubs the variable and imports a fresh copy of the module.

const DEMO_MAP_ID = "4504f8b37365c3d0";

async function loadMapId(): Promise<string> {
  vi.resetModules();
  return (await import("@/lib/googleMaps")).MAP_ID;
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("MAP_ID", () => {
  it("falls back to the demo Map ID when VITE_GOOGLE_MAPS_MAP_ID is unset", async () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_MAP_ID", undefined);
    expect(await loadMapId()).toBe(DEMO_MAP_ID);
  });

  it("falls back to the demo Map ID when VITE_GOOGLE_MAPS_MAP_ID is empty", async () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_MAP_ID", "");
    expect(await loadMapId()).toBe(DEMO_MAP_ID);
  });

  it("uses VITE_GOOGLE_MAPS_MAP_ID when the build sets it", async () => {
    vi.stubEnv("VITE_GOOGLE_MAPS_MAP_ID", "0123456789abcdef");
    expect(await loadMapId()).toBe("0123456789abcdef");
  });
});
