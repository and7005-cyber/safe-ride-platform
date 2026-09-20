import { afterEach, describe, expect, it, vi } from "vitest";
import { reconcileSchoolStore, type AuthUser } from "@/lib/auth";
import { getActiveSchoolId, setActiveSchoolId } from "@/lib/school";
import { ApiError } from "@/lib/apiClient";
import {
  runWithStepUp,
  stepUpCodeNeeded,
  STEP_UP_FRESH_MINUTES,
} from "@/features/provider/providerHooks";

// U13 — the provider console's pure client logic: the step-up freshness
// pre-judgement, the retry-with-code contract, and the widened school-store
// rule (staff memberships OR a live provider step-in).

const NOW = new Date("2026-08-28T10:00:00Z");

function minutesAgo(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

describe("stepUpCodeNeeded", () => {
  it("asks for a code when the session never verified one", () => {
    expect(stepUpCodeNeeded(null, NOW)).toBe(true);
    expect(stepUpCodeNeeded(undefined, NOW)).toBe(true);
  });

  it("asks for a code on an unreadable timestamp", () => {
    expect(stepUpCodeNeeded("not-a-date", NOW)).toBe(true);
  });

  it("skips the code while the last one is fresh", () => {
    expect(stepUpCodeNeeded(minutesAgo(1), NOW)).toBe(false);
    expect(stepUpCodeNeeded(minutesAgo(14), NOW)).toBe(false);
  });

  it("treats exactly fifteen minutes as still fresh (the server's <=)", () => {
    expect(stepUpCodeNeeded(minutesAgo(STEP_UP_FRESH_MINUTES), NOW)).toBe(false);
  });

  it("asks for a code once the window has passed", () => {
    expect(stepUpCodeNeeded(minutesAgo(16), NOW)).toBe(true);
    expect(stepUpCodeNeeded(minutesAgo(24 * 60), NOW)).toBe(true);
  });
});

describe("runWithStepUp", () => {
  it("runs without a code while fresh and never prompts", async () => {
    const action = vi.fn().mockResolvedValue("done");
    const promptCode = vi.fn();
    const outcome = await runWithStepUp(action, {
      totpVerifiedAt: new Date().toISOString(),
      promptCode,
    });
    expect(outcome).toEqual({ cancelled: false, result: "done" });
    expect(action).toHaveBeenCalledWith(undefined);
    expect(promptCode).not.toHaveBeenCalled();
  });

  it("prompts up front when stale and passes the code through", async () => {
    const action = vi.fn().mockResolvedValue("done");
    const promptCode = vi.fn().mockResolvedValue("123456");
    const outcome = await runWithStepUp(action, { totpVerifiedAt: null, promptCode });
    expect(outcome).toEqual({ cancelled: false, result: "done" });
    expect(action).toHaveBeenCalledWith("123456");
  });

  it("cancels without calling the action when the prompt is dismissed", async () => {
    const action = vi.fn();
    const promptCode = vi.fn().mockResolvedValue(null);
    const outcome = await runWithStepUp(action, { totpVerifiedAt: null, promptCode });
    expect(outcome).toEqual({ cancelled: true });
    expect(action).not.toHaveBeenCalled();
  });

  it("retries once with a fresh code on totp-step-up-required", async () => {
    // The server stays the authority: a locally-fresh timestamp can still be
    // refused (clock skew, another device) — the refusal collects a code.
    const action = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("Enter a fresh code", 401, "totp-step-up-required"))
      .mockResolvedValueOnce("done");
    const promptCode = vi.fn().mockResolvedValue("654321");
    const outcome = await runWithStepUp(action, {
      totpVerifiedAt: new Date().toISOString(),
      promptCode,
    });
    expect(outcome).toEqual({ cancelled: false, result: "done" });
    expect(action).toHaveBeenNthCalledWith(1, undefined);
    expect(action).toHaveBeenNthCalledWith(2, "654321");
    expect(promptCode).toHaveBeenCalledWith("Enter a fresh code");
  });

  it("rethrows every other failure untouched", async () => {
    const action = vi.fn().mockRejectedValue(new ApiError("Invalid code", 401, null));
    const promptCode = vi.fn().mockResolvedValue("123456");
    await expect(
      runWithStepUp(action, { totpVerifiedAt: null, promptCode }),
    ).rejects.toThrow("Invalid code");
    expect(action).toHaveBeenCalledTimes(1);
  });
});

// --- the widened school-store rule -------------------------------------------

function makeUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "u1",
    email: "user@test.local",
    fullName: null,
    role: null,
    memberships: [],
    pendingOffers: [],
    activeSchoolId: null,
    provider: null,
    supportSession: null,
    mustChangePassword: false,
    hydrated: true,
    ...overrides,
  };
}

const PROVIDER = { isProvider: true, totpEnrolled: true, totpVerifiedAt: null };

function support(schoolId: string) {
  return {
    id: "ss1",
    schoolId,
    schoolName: "School",
    schoolCode: "SCH-001",
    reason: "ticket",
    startedAt: new Date().toISOString(),
  };
}

function membership(schoolId: string, role: "director" | "coordinator" | "driver") {
  return { schoolId, schoolName: "School", schoolCode: "SCH-001", role };
}

describe("reconcileSchoolStore (U13 widening)", () => {
  afterEach(() => {
    sessionStorage.clear();
  });

  it("mirrors a provider's live step-in into the store", () => {
    reconcileSchoolStore(makeUser({ provider: PROVIDER, supportSession: support("school-a") }));
    expect(getActiveSchoolId()).toBe("school-a");
  });

  it("moves a provider's tab onto a superseding step-in", () => {
    setActiveSchoolId("school-a");
    reconcileSchoolStore(makeUser({ provider: PROVIDER, supportSession: support("school-b") }));
    expect(getActiveSchoolId()).toBe("school-b");
  });

  it("clears the store for a provider with no live step-in", () => {
    setActiveSchoolId("school-a");
    reconcileSchoolStore(makeUser({ provider: PROVIDER, supportSession: null }));
    expect(getActiveSchoolId()).toBeNull();
  });

  it("still clears the store for a plain non-staff session", () => {
    setActiveSchoolId("school-a");
    reconcileSchoolStore(makeUser({ role: "parent" }));
    expect(getActiveSchoolId()).toBeNull();
  });

  it("still self-selects a lone staff membership", () => {
    reconcileSchoolStore(makeUser({ memberships: [membership("school-a", "director")] }));
    expect(getActiveSchoolId()).toBe("school-a");
  });

  it("still drops a school the staff account no longer holds", () => {
    setActiveSchoolId("school-gone");
    reconcileSchoolStore(
      makeUser({
        memberships: [membership("school-a", "director"), membership("school-b", "coordinator")],
      }),
    );
    expect(getActiveSchoolId()).toBeNull();
  });

  it("never treats a driver membership as school-console access", () => {
    setActiveSchoolId("school-a");
    reconcileSchoolStore(makeUser({ memberships: [membership("school-a", "driver")] }));
    expect(getActiveSchoolId()).toBeNull();
  });
});
