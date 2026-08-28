import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  clearActiveSchoolId,
  getActiveSchoolId,
  setActiveSchoolId,
  subscribeActiveSchool,
  useActiveSchoolId,
} from "@/lib/school";

// U12 — the per-tab active school store. sessionStorage IS the per-tab
// mechanism: each tab has its own copy, so nothing here may ever touch
// localStorage (which is shared across tabs and would leak the school).

const KEY = "saferide-school";

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

describe("active school store", () => {
  it("defaults to null with nothing stored", () => {
    expect(getActiveSchoolId()).toBeNull();
  });

  it("round-trips through sessionStorage", () => {
    setActiveSchoolId("school-1");
    expect(getActiveSchoolId()).toBe("school-1");
    expect(sessionStorage.getItem(KEY)).toBe("school-1");
  });

  it("clears the stored value", () => {
    setActiveSchoolId("school-1");
    clearActiveSchoolId();
    expect(getActiveSchoolId()).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it("reads a value seeded directly into sessionStorage (e2e helper contract)", () => {
    // tests/e2e/helpers.ts signInAs() seeds this exact key before app boot.
    sessionStorage.setItem(KEY, "seeded-school");
    expect(getActiveSchoolId()).toBe("seeded-school");
  });

  it("is per-tab: never writes localStorage", () => {
    setActiveSchoolId("school-1");
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("notifies subscribers on set and clear, and stops after unsubscribe", () => {
    const seen: Array<string | null> = [];
    const unsubscribe = subscribeActiveSchool(() => seen.push(getActiveSchoolId()));

    setActiveSchoolId("school-1");
    setActiveSchoolId(null);
    expect(seen).toEqual(["school-1", null]);

    unsubscribe();
    setActiveSchoolId("school-2");
    expect(seen).toEqual(["school-1", null]);
  });

  it("survives a sessionStorage failure without throwing", () => {
    const spy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("storage unavailable");
      });
    expect(getActiveSchoolId()).toBeNull();
    spy.mockRestore();
  });
});

describe("useActiveSchoolId", () => {
  it("tracks the store through set/clear", () => {
    const { result } = renderHook(() => useActiveSchoolId());
    expect(result.current).toBeNull();

    act(() => setActiveSchoolId("school-9"));
    expect(result.current).toBe("school-9");

    act(() => clearActiveSchoolId());
    expect(result.current).toBeNull();
  });
});
