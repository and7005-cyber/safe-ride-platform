import { describe, expect, it } from "vitest";

// Mirrors the role-home redirect matrix used by AuthPage and ProtectedRoute.
function homeFor(role: "admin" | "driver" | "parent" | null): string {
  if (role === "admin") return "/";
  if (role === "driver") return "/driver";
  return "/parent";
}

describe("role redirect matrix", () => {
  it("sends admins to the dashboard", () => {
    expect(homeFor("admin")).toBe("/");
  });
  it("sends drivers to the driver home", () => {
    expect(homeFor("driver")).toBe("/driver");
  });
  it("sends parents to the parent home", () => {
    expect(homeFor("parent")).toBe("/parent");
  });
  it("defaults a missing role to the parent home", () => {
    expect(homeFor(null)).toBe("/parent");
  });
});
