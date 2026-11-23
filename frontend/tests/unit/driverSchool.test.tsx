// The phone's school binding (hardening U10; AE8, AE10; DL3, DL5). The store
// reads the code from a sign-in link, keeps the confirmed school across
// sign-out, and is cleared only by "Change school"; a signed-in driver with
// no binding adopts the session's school.
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, api: { ...actual.api, get: mocks.get, post: mocks.post } };
});
vi.mock("@/lib/push", () => ({
  disablePush: vi.fn(async () => {}),
  reattachPushSubscription: vi.fn(async () => {}),
}));

import { AuthProvider, useAuth } from "@/lib/auth";
import { setToken } from "@/lib/apiClient";
import {
  adoptSessionSchool,
  clearDriverSchool,
  driverCodeFromSearch,
  driverSignInLink,
  formatDriverCode,
  isDriverCode,
  looksLikeSchoolCode,
  lookupDriverSchool,
  normaliseDriverCode,
  readDriverSchool,
  storeDriverSchool,
} from "@/lib/driverSchool";

const SCHOOL = { schoolId: "5cae0000-0000-0000-0000-000000000001", name: "Greenfield Academy" };

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  mocks.get.mockReset();
  mocks.post.mockReset();
});

afterEach(() => cleanup());

describe("the driver code", () => {
  it.each(["GFA07K1Q", "gfa07k1q", "GFA0-7K1Q", " gfa0 7k1q ", "gfaO-7kIq", "GFAO7KLQ"])(
    "normalises %j to its stored form",
    (typed) => {
      expect(normaliseDriverCode(typed)).toBe("GFA07K1Q");
      expect(isDriverCode(normaliseDriverCode(typed))).toBe(true);
    },
  );

  it("refuses anything that is not eight symbols of the alphabet", () => {
    for (const value of ["", "GFA-001", "GFA07K1", "GFA07K1QX", "GFA07K1U", "GFA07K1*"]) {
      expect(isDriverCode(normaliseDriverCode(value))).toBe(false);
    }
  });

  it("shows the code in two groups of four and builds the office's link", () => {
    expect(formatDriverCode("GFA07K1Q")).toBe("GFA0-7K1Q");
    expect(driverSignInLink("https://saferidelive.co.ke/", "gfa0-7k1q")).toBe(
      "https://saferidelive.co.ke/auth?school=GFA07K1Q",
    );
  });

  it("recognises the public school code typed by mistake (DL5)", () => {
    expect(looksLikeSchoolCode("GFA-001")).toBe(true);
    expect(looksLikeSchoolCode("gfa001")).toBe(true);
    expect(looksLikeSchoolCode("GFA0-7K1Q")).toBe(false);
  });
});

describe("the binding store", () => {
  it("reads the code of a sign-in link", () => {
    expect(driverCodeFromSearch("?school=gfa0-7k1q")).toBe("GFA07K1Q");
    expect(driverCodeFromSearch(new URLSearchParams("school=SEC2ND8B&x=1"))).toBe("SEC2ND8B");
    expect(driverCodeFromSearch("?school=")).toBeNull();
    expect(driverCodeFromSearch("")).toBeNull();
  });

  it("looks the code up and answers id and name only", async () => {
    mocks.get.mockResolvedValue({ schoolId: SCHOOL.schoolId, name: SCHOOL.name });
    await expect(lookupDriverSchool("gfa0-7k1q")).resolves.toEqual(SCHOOL);
    expect(mocks.get).toHaveBeenCalledWith("/api/auth/schools/GFA07K1Q");
  });

  it("stores the confirmed school, reads it back, and clears it", () => {
    expect(readDriverSchool()).toBeNull();
    storeDriverSchool(SCHOOL);
    expect(readDriverSchool()).toEqual(SCHOOL);
    // Never the code itself: only the id and the name are kept.
    expect(JSON.parse(localStorage.getItem("saferide-driver-school")!)).toEqual(SCHOOL);
    clearDriverSchool();
    expect(readDriverSchool()).toBeNull();
  });

  it("reads a malformed value as not set up", () => {
    localStorage.setItem("saferide-driver-school", "{not json");
    expect(readDriverSchool()).toBeNull();
    localStorage.setItem("saferide-driver-school", JSON.stringify({ name: "No id" }));
    expect(readDriverSchool()).toBeNull();
  });
});

function Probe({ onReady }: { onReady: (auth: ReturnType<typeof useAuth>) => void }) {
  const auth = useAuth();
  onReady(auth);
  return null;
}

function renderAuthProvider() {
  let latest: ReturnType<typeof useAuth> | null = null;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <Probe onReady={(auth) => (latest = auth)} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return () => latest!;
}

const driverMe = (extra: Record<string, unknown> = {}) => ({
  id: "u-driver",
  email: "driver@saferide.test",
  role: "driver",
  memberships: [
    { schoolId: SCHOOL.schoolId, schoolName: SCHOOL.name, schoolCode: "GFA-001", role: "driver" },
  ],
  activeSchoolId: SCHOOL.schoolId,
  signedInWithPin: true,
  ...extra,
});

describe("sign-out and a live session (AE10)", () => {
  it("keeps the binding through sign-out", async () => {
    storeDriverSchool(SCHOOL);
    setToken("pin_token");
    mocks.get.mockResolvedValue(driverMe());
    mocks.post.mockResolvedValue({ ok: true });
    const auth = renderAuthProvider();
    await waitFor(() => expect(auth().user?.id).toBe("u-driver"));
    await act(async () => {
      await auth().signOut();
    });
    expect(auth().user).toBeNull();
    expect(localStorage.getItem("saferide-token")).toBeNull();
    expect(readDriverSchool()).toEqual(SCHOOL);
  });

  it("a signed-in driver with no binding adopts the session's school", async () => {
    setToken("pin_token");
    mocks.get.mockResolvedValue(driverMe());
    renderAuthProvider();
    await waitFor(() => expect(readDriverSchool()).toEqual(SCHOOL));
  });

  it("never replaces a binding the driver confirmed", () => {
    const other = { schoolId: "5cae0000-0000-0000-0000-000000000002", name: "IT Second School" };
    storeDriverSchool(other);
    expect(adoptSessionSchool({ ...driverMe(), memberships: driverMe().memberships })).toBeNull();
    expect(readDriverSchool()).toEqual(other);
  });

  it("leaves a staff member who also drives alone unless they signed in by PIN", () => {
    const hybrid = {
      activeSchoolId: SCHOOL.schoolId,
      signedInWithPin: false,
      memberships: [
        { schoolId: SCHOOL.schoolId, schoolName: SCHOOL.name, role: "director" },
        { schoolId: SCHOOL.schoolId, schoolName: SCHOOL.name, role: "driver" },
      ],
    };
    expect(adoptSessionSchool(hybrid)).toBeNull();
    expect(readDriverSchool()).toBeNull();
    expect(adoptSessionSchool({ ...hybrid, signedInWithPin: true })).toEqual(SCHOOL);
  });

  it("adopts nothing for an account without a driver membership", () => {
    expect(
      adoptSessionSchool({
        activeSchoolId: null,
        signedInWithPin: false,
        memberships: [{ schoolId: SCHOOL.schoolId, schoolName: SCHOOL.name, role: "coordinator" }],
      }),
    ).toBeNull();
    expect(readDriverSchool()).toBeNull();
  });
});
