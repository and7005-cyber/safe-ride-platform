import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getToken,
  setToken,
  onUnauthorized,
  onSchoolScoped404,
  sendsSchoolHeader,
} from "@/lib/apiClient";
import { clearActiveSchoolId, setActiveSchoolId } from "@/lib/school";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  onSchoolScoped404(null);
});

describe("apiClient token storage", () => {
  it("round-trips a token", () => {
    setToken("abc123");
    expect(getToken()).toBe("abc123");
  });

  it("clears the token when set to null", () => {
    setToken("abc123");
    setToken(null);
    expect(getToken()).toBeNull();
  });

  it("returns null when no token is stored", () => {
    expect(getToken()).toBeNull();
  });
});

describe("apiClient DELETE bodies", () => {
  // Cancel-a-Ride withdrawal (U13) is the first DELETE with a JSON body
  // ({student_id, scope}); pre-existing deletes must stay body-free.
  it("sends a JSON body and Content-Type when a body is provided", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok": true}', { status: 200 }));

    const { api } = await import("@/lib/apiClient");
    await api.del("/api/parent-portal/cancel-ride", { student_id: "s1", scope: "morning" });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.method).toBe("DELETE");
    expect(init?.body).toBe(JSON.stringify({ student_id: "s1", scope: "morning" }));
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    fetchMock.mockRestore();
  });

  it("keeps body-less DELETEs body-free with no Content-Type", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok": true}', { status: 200 }));

    const { api } = await import("@/lib/apiClient");
    await api.del("/api/students/s1");

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.method).toBe("DELETE");
    expect(init?.body).toBeUndefined();
    expect(init?.headers as Record<string, string>).not.toHaveProperty("Content-Type");
    fetchMock.mockRestore();
  });
});

describe("apiClient 401 handling", () => {
  it("clears the token and notifies on a 401 response", async () => {
    setToken("expired");
    const handler = vi.fn();
    onUnauthorized(handler);

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 401 }));

    const { api } = await import("@/lib/apiClient");
    await expect(api.get("/api/auth/me")).rejects.toThrow(/session has expired/i);

    expect(getToken()).toBeNull();
    expect(handler).toHaveBeenCalledOnce();
    fetchMock.mockRestore();
  });

  it("surfaces the server message for a 401 from a credential endpoint", async () => {
    setToken("still-valid");
    const handler = vi.fn();
    onUnauthorized(handler);

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Invalid email or password" }), { status: 401 }),
      );

    const { api } = await import("@/lib/apiClient");
    await expect(
      api.post("/api/auth/login", { email: "x@y.z", password: "wrong" }),
    ).rejects.toThrow("Invalid email or password");

    // A failed login must not nuke an existing session or trigger sign-out.
    expect(getToken()).toBe("still-valid");
    expect(handler).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });
});

// U12 — the school scope header, request cancellation, and the centralized
// membership-lost detection.

function okFetchMock() {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response('{"ok": true}', { status: 200 }));
}

describe("apiClient X-School-Id header", () => {
  it("attaches the header on staff-surface calls when a school is active", async () => {
    setActiveSchoolId("school-a");
    const fetchMock = okFetchMock();

    const { api } = await import("@/lib/apiClient");
    await api.get("/api/students");

    const [, init] = fetchMock.mock.calls[0]!;
    expect((init?.headers as Record<string, string>)["X-School-Id"]).toBe("school-a");
    fetchMock.mockRestore();
  });

  it("sends no header when the store is cleared", async () => {
    setActiveSchoolId("school-a");
    clearActiveSchoolId();
    const fetchMock = okFetchMock();

    const { api } = await import("@/lib/apiClient");
    await api.get("/api/students");

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.headers as Record<string, string>).not.toHaveProperty("X-School-Id");
    fetchMock.mockRestore();
  });

  it("never sends the header to auth, driver, parent-portal or push routes", async () => {
    // Driver routes 403 the header by design; the auth surface (offers
    // included) is account-level; the parent portal and push registration
    // resolve their own scope. The rule is also exported for unit use.
    expect(sendsSchoolHeader("/api/runs/driver/context")).toBe(false);
    expect(sendsSchoolHeader("/api/incidents/driver")).toBe(false);
    expect(sendsSchoolHeader("/api/auth/offers/x/accept")).toBe(false);
    expect(sendsSchoolHeader("/api/parent-portal/children")).toBe(false);
    expect(sendsSchoolHeader("/api/push/subscribe")).toBe(false);
    expect(sendsSchoolHeader("/api/students")).toBe(true);
    expect(sendsSchoolHeader("/api/staff")).toBe(true);

    setActiveSchoolId("school-a");
    const fetchMock = okFetchMock();
    const { api } = await import("@/lib/apiClient");
    await api.get("/api/runs/driver/context");
    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.headers as Record<string, string>).not.toHaveProperty("X-School-Id");
    fetchMock.mockRestore();
  });
});

describe("apiClient AbortSignal passthrough", () => {
  it("passes the caller's signal to fetch", async () => {
    const fetchMock = okFetchMock();
    const controller = new AbortController();

    const { api } = await import("@/lib/apiClient");
    await api.get("/api/students", undefined, { signal: controller.signal });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init?.signal).toBe(controller.signal);
    fetchMock.mockRestore();
  });

  it("rejects when the signal is already aborted (cancellation works end to end)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch"); // real fetch honours signals
    fetchMock.mockImplementation((_url, init) => {
      if (init?.signal?.aborted) {
        return Promise.reject(new DOMException("Aborted", "AbortError"));
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    const controller = new AbortController();
    controller.abort();

    const { api } = await import("@/lib/apiClient");
    await expect(
      api.get("/api/students", undefined, { signal: controller.signal }),
    ).rejects.toThrow(/abort/i);
    fetchMock.mockRestore();
  });
});

describe("apiClient membership-lost detection", () => {
  it("notifies the handler on a 404 from a school-scoped call", async () => {
    setActiveSchoolId("school-a");
    const handler = vi.fn();
    onSchoolScoped404(handler);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }),
      );

    const { api } = await import("@/lib/apiClient");
    await expect(api.get("/api/students")).rejects.toThrow("Not found");

    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith("school-a");
    fetchMock.mockRestore();
  });

  it("stays quiet on a 404 without an active school (nothing school-scoped)", async () => {
    clearActiveSchoolId();
    const handler = vi.fn();
    onSchoolScoped404(handler);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }),
      );

    const { api } = await import("@/lib/apiClient");
    await expect(api.get("/api/students")).rejects.toThrow("Not found");

    expect(handler).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });

  it("stays quiet on a 404 from a non-school path even with a school active", async () => {
    setActiveSchoolId("school-a");
    const handler = vi.fn();
    onSchoolScoped404(handler);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }),
      );

    const { api } = await import("@/lib/apiClient");
    await expect(api.post("/api/auth/offers/gone/accept")).rejects.toThrow("Not found");

    expect(handler).not.toHaveBeenCalled();
    fetchMock.mockRestore();
  });
});

describe("apiClient structured error details", () => {
  it("surfaces the message of an object detail (password-change-required 409)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "password-change-required",
            message: "You must change your temporary password first",
          },
        }),
        { status: 409 },
      ),
    );

    const { api } = await import("@/lib/apiClient");
    await expect(api.get("/api/students")).rejects.toThrow(
      "You must change your temporary password first",
    );
    fetchMock.mockRestore();
  });
});
