// The global mail flag (hardening DL1) rides on the public config response.
// A failed read is retried and never kept as "mail off" for the session; the
// push plumbing keeps its tolerant fallback (no channel, mail off).
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, api: { ...actual.api, get: mocks.get } };
});

// lib/push keeps the config promise for the page's life: every test loads a
// fresh copy (and the query library with it, so provider and hook match).
async function load() {
  vi.resetModules();
  const query = await import("@tanstack/react-query");
  const { useMailEnabled } = await import("@/lib/publicConfig");
  const push = await import("@/lib/push");
  // React Query's default retry policy, as the app uses it; only the wait
  // between tries is cut.
  const client = new query.QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
  const seen: (boolean | null)[] = [];
  function Probe() {
    const mail = useMailEnabled();
    seen.push(mail);
    return <p data-testid="mail">{String(mail)}</p>;
  }
  const mount = () =>
    render(
      <query.QueryClientProvider client={client}>
        <Probe />
      </query.QueryClientProvider>,
    );
  return { mount, push, seen };
}

const offline = () => new TypeError("Failed to fetch");

beforeEach(() => mocks.get.mockReset());
afterEach(() => cleanup());

describe("useMailEnabled", () => {
  it("a first failed read recovers on retry to mail on, never saying off", async () => {
    mocks.get
      .mockRejectedValueOnce(offline())
      .mockResolvedValueOnce({ vapidPublicKey: null, mailEnabled: true });
    const { mount, seen } = await load();
    mount();
    expect(screen.getByTestId("mail")).toHaveTextContent("null");
    await waitFor(() => expect(screen.getByTestId("mail")).toHaveTextContent("true"));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenCalledWith("/api/push/config");
    expect(seen).not.toContain(false);
  });

  it("reads off once every retry failed, and a later screen reads it again", async () => {
    mocks.get.mockRejectedValue(offline());
    const { mount } = await load();
    const first = mount();
    await waitFor(() => expect(screen.getByTestId("mail")).toHaveTextContent("false"));
    // The default policy: the first try and three retries.
    expect(mocks.get).toHaveBeenCalledTimes(4);
    first.unmount();

    mocks.get.mockResolvedValue({ vapidPublicKey: null, mailEnabled: true });
    mount();
    await waitFor(() => expect(screen.getByTestId("mail")).toHaveTextContent("true"));
  });

  it("keeps a real answer for the session", async () => {
    mocks.get.mockResolvedValue({ vapidPublicKey: null, mailEnabled: false });
    const { mount } = await load();
    const first = mount();
    await waitFor(() => expect(screen.getByTestId("mail")).toHaveTextContent("false"));
    first.unmount();
    mount();
    await act(async () => {});
    expect(screen.getByTestId("mail")).toHaveTextContent("false");
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });
});

describe("the push plumbing's config read", () => {
  it("falls back to no channel and mail off, and asks again next time", async () => {
    mocks.get.mockRejectedValueOnce(offline());
    const { push } = await load();
    await expect(push.fetchPushConfig()).resolves.toEqual({
      firebase: null,
      firebaseVapidKey: null,
      vapidPublicKey: null,
      mailEnabled: false,
    });
    mocks.get.mockResolvedValue({ vapidPublicKey: "BKey", mailEnabled: true });
    await expect(push.fetchPushConfig()).resolves.toMatchObject({
      vapidPublicKey: "BKey",
      mailEnabled: true,
    });
    // A success is kept: the strict read shares it without a new request.
    await expect(push.fetchPushConfigStrict()).resolves.toMatchObject({ mailEnabled: true });
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("the strict read rejects so its caller can retry", async () => {
    mocks.get.mockRejectedValueOnce(offline());
    const { push } = await load();
    await expect(push.fetchPushConfigStrict()).rejects.toThrow("Failed to fetch");
    mocks.get.mockResolvedValue({ mailEnabled: true });
    await expect(push.fetchPushConfigStrict()).resolves.toMatchObject({ mailEnabled: true });
  });
});
