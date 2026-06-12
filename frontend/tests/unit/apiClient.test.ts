import { afterEach, describe, expect, it, vi } from "vitest";
import { getToken, setToken, onUnauthorized } from "@/lib/apiClient";

afterEach(() => {
  localStorage.clear();
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
