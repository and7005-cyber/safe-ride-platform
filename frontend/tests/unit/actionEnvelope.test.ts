// GPS plan U6 — the idempotent action envelope (R3, R33 client half): one key
// per tap, the fix frozen at the first attempt and re-sent unchanged on a
// retry, the two server conflicts, and what survives a reload.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ActionEnvelopes,
  DEVICE_ID_KEY,
  ENVELOPE_STORAGE_KEY,
  ENVELOPE_TTL_MS,
  IDEMPOTENCY_HEADER,
  IN_FLIGHT_MAX_RETRIES,
  IN_FLIGHT_RETRY_DELAY_MS,
  actionFingerprint,
  getDeviceId,
  isInFlightConflict,
  isMismatchConflict,
  isRetryableFailure,
  isUuidV4,
  randomUuid,
} from "@/lib/actionEnvelope";
import { ApiError } from "@/lib/apiClient";

const FIX = { lat: -1.2921, lng: 36.8219, accuracy_m: 20, captured_at: "2026-09-20T09:40:12.345+03:00" };
const LATER_FIX = { ...FIX, lat: -1.3, captured_at: "2026-09-20T09:41:00.000+03:00" };
const ARRIVE = "/api/runs/driver/arrive";
const BOARD = "/api/runs/driver/boarding";

function harness(over: Partial<ConstructorParameters<typeof ActionEnvelopes>[0]> = {}) {
  const post = vi.fn();
  let n = 0;
  const deps = {
    storage: localStorage as Storage | null,
    post,
    uuid: () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`,
    now: () => Date.now(),
    sleep: vi.fn(() => Promise.resolve()),
    deviceId: () => "11111111-1111-4111-8111-111111111111",
    ...over,
  };
  return { store: new ActionEnvelopes(deps), post, deps };
}

const inFlight = () => new ApiError("Still executing", 409, "idempotency-in-flight");
const mismatch = () => new ApiError("Different payload", 409, "idempotency-mismatch");
const network = () => new TypeError("Failed to fetch");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-20T06:40:00.000Z"));
});

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe("minting and sending", () => {
  it("mints a v4 key, freezes the fix in the body with the device id, sends the key in the header, and forgets on success", async () => {
    const { store, post } = harness();
    post.mockResolvedValue({ ok: true, stops_completed: 1 });
    const fix = vi.fn(async () => FIX);
    const envelope = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 1 }, { runId: "r1", fix });

    expect(isUuidV4(envelope.key)).toBe(true);
    expect(envelope.body).toEqual({
      run_id: "r1",
      expected_stop_order: 1,
      fix: FIX,
      device_id: "11111111-1111-4111-8111-111111111111",
    });
    expect(envelope.run_id).toBe("r1");
    expect(JSON.parse(localStorage.getItem(ENVELOPE_STORAGE_KEY)!)).toMatchObject({
      [actionFingerprint(ARRIVE, "r1", { run_id: "r1", expected_stop_order: 1 })]: { key: envelope.key },
    });

    await expect(store.send(envelope)).resolves.toEqual({ ok: true, stops_completed: 1 });
    expect(post).toHaveBeenCalledWith(ARRIVE, envelope.body, { [IDEMPOTENCY_HEADER]: envelope.key });
    expect(store.pending()).toEqual([]);
    expect(localStorage.getItem(ENVELOPE_STORAGE_KEY)).toBeNull();
  });

  it("a retry of the same action re-sends the same envelope: same key, same fix, the capture not repeated", async () => {
    const { store, post } = harness();
    post.mockRejectedValueOnce(network()).mockResolvedValueOnce({ ok: true });
    const fix = vi.fn().mockResolvedValueOnce(FIX).mockResolvedValueOnce(LATER_FIX);

    const first = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });
    await expect(store.send(first)).rejects.toThrow("Failed to fetch");
    expect(store.pending()).toHaveLength(1);

    const second = await store.mint(BOARD, { on_bus: true, student_id: "s1" }, { runId: "r1", fix });
    expect(second).toBe(first);
    expect(fix).toHaveBeenCalledTimes(1);
    await expect(store.send(second)).resolves.toEqual({ ok: true });
    expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
    expect(post.mock.calls[1]![1]).toMatchObject({ fix: FIX });
    expect(store.pending()).toEqual([]);
  });

  it("after the server has answered, the same action is a new tap with a new key and a fresh fix", async () => {
    const { store, post } = harness();
    post.mockResolvedValue({ ok: true });
    const fix = vi.fn().mockResolvedValueOnce(FIX).mockResolvedValueOnce(LATER_FIX);
    const first = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });
    await store.send(first);
    const second = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });
    expect(second.key).not.toBe(first.key);
    expect(second.body).toMatchObject({ fix: LATER_FIX });
    expect(fix).toHaveBeenCalledTimes(2);
  });

  it("different students, runs or routes are different envelopes; property order is not", async () => {
    const { store } = harness();
    const fix = () => FIX;
    const a = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });
    const b = await store.mint(BOARD, { student_id: "s2", on_bus: true }, { runId: "r1", fix });
    const c = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r2", fix });
    const d = await store.mint("/api/runs/driver/absent", { student_id: "s1" }, { runId: "r1", fix });
    expect(new Set([a.key, b.key, c.key, d.key]).size).toBe(4);
    expect(actionFingerprint(BOARD, "r1", { student_id: "s1", on_bus: true, event_id: undefined })).toBe(
      actionFingerprint(BOARD, "r1", { on_bus: true, student_id: "s1" }),
    );
    expect(actionFingerprint(BOARD, "r1", { student_id: "s1", on_bus: true, event_id: "e1" })).not.toBe(
      actionFingerprint(BOARD, "r1", { student_id: "s1", on_bus: true }),
    );
  });
});

describe("the server's answers", () => {
  it("an in-flight conflict retries the same envelope after a short delay and returns the eventual result", async () => {
    const { store, post, deps } = harness();
    post.mockRejectedValueOnce(inFlight()).mockRejectedValueOnce(inFlight()).mockResolvedValueOnce({ ok: 1 });
    const envelope = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 2 }, { runId: "r1", fix: () => FIX });
    await expect(store.send(envelope)).resolves.toEqual({ ok: 1 });
    expect(post).toHaveBeenCalledTimes(3);
    for (const call of post.mock.calls) {
      expect(call).toEqual([ARRIVE, envelope.body, { [IDEMPOTENCY_HEADER]: envelope.key }]);
    }
    expect(deps.sleep).toHaveBeenCalledTimes(2);
    expect(deps.sleep).toHaveBeenCalledWith(IN_FLIGHT_RETRY_DELAY_MS);
    expect(store.pending()).toEqual([]);
  });

  it("in-flight retries are bounded; when exhausted the envelope stays pending and nothing re-mints", async () => {
    const { store, post } = harness();
    post.mockRejectedValue(inFlight());
    const envelope = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 2 }, { runId: "r1", fix: () => FIX });
    await expect(store.send(envelope)).rejects.toMatchObject({ code: "idempotency-in-flight" });
    expect(post).toHaveBeenCalledTimes(1 + IN_FLIGHT_MAX_RETRIES);
    expect(store.pending().map((e) => e.key)).toEqual([envelope.key]);
    const again = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 2 }, { runId: "r1", fix: () => LATER_FIX });
    expect(again).toBe(envelope);
  });

  it("a mismatch drops the envelope and surfaces the error; the next tap mints a new key", async () => {
    const { store, post } = harness();
    post.mockRejectedValueOnce(mismatch()).mockResolvedValueOnce({ ok: true });
    const envelope = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 2 }, { runId: "r1", fix: () => FIX });
    await expect(store.send(envelope)).rejects.toMatchObject({ code: "idempotency-mismatch" });
    expect(post).toHaveBeenCalledTimes(1);
    expect(store.pending()).toEqual([]);
    const next = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 2 }, { runId: "r1", fix: () => LATER_FIX });
    expect(next.key).not.toBe(envelope.key);
    expect(next.body).toMatchObject({ fix: LATER_FIX });
  });

  it("a 5xx keeps the envelope for the retry; an acknowledged refusal drops it", async () => {
    const { store, post } = harness();
    const fix = () => FIX;
    post.mockRejectedValueOnce(new ApiError("Bad gateway", 502));
    const kept = await store.mint(ARRIVE, { run_id: "r1", expected_stop_order: 3 }, { runId: "r1", fix });
    await expect(store.send(kept)).rejects.toThrow("Bad gateway");
    expect(store.pending().map((e) => e.key)).toEqual([kept.key]);

    post.mockRejectedValueOnce(new ApiError("Not everyone is accounted for", 409, "closure-refused"));
    const refused = await store.mint("/api/runs/driver/end", { run_id: "r1" }, { runId: "r1", fix });
    await expect(store.send(refused)).rejects.toThrow("Not everyone is accounted for");
    expect(store.pending().map((e) => e.key)).toEqual([kept.key]);

    post.mockRejectedValueOnce(new ApiError("Forbidden", 403));
    const forbidden = await store.mint(BOARD, { student_id: "s9", on_bus: true }, { runId: "r1", fix });
    await expect(store.send(forbidden)).rejects.toThrow("Forbidden");
    expect(store.pending().map((e) => e.key)).toEqual([kept.key]);

    // The session-expiry path throws a plain Error: also an answer.
    post.mockRejectedValueOnce(new Error("Your session has expired. Please sign in again."));
    const expired = await store.mint(BOARD, { student_id: "s8", on_bus: true }, { runId: "r1", fix });
    await expect(store.send(expired)).rejects.toThrow(/session has expired/);
    expect(store.pending().map((e) => e.key)).toEqual([kept.key]);
  });

  it("classifies failures", () => {
    expect(isRetryableFailure(network())).toBe(true);
    expect(isRetryableFailure(new DOMException("Aborted", "AbortError"))).toBe(true);
    expect(isRetryableFailure(new ApiError("down", 503))).toBe(true);
    expect(isRetryableFailure(inFlight())).toBe(true);
    expect(isRetryableFailure(mismatch())).toBe(false);
    expect(isRetryableFailure(new ApiError("no", 409, "run-already-completed"))).toBe(false);
    expect(isRetryableFailure(new ApiError("no", 400))).toBe(false);
    expect(isRetryableFailure(new Error("session expired"))).toBe(false);
    expect(isInFlightConflict(inFlight())).toBe(true);
    expect(isInFlightConflict(mismatch())).toBe(false);
    expect(isMismatchConflict(mismatch())).toBe(true);
    expect(isMismatchConflict(new ApiError("x", 400, "idempotency-mismatch"))).toBe(false);
  });
});

describe("persistence and pruning", () => {
  it("pending envelopes survive a reload: a second store over the same storage re-sends them unchanged", async () => {
    const first = harness();
    first.post.mockRejectedValue(network());
    const envelope = await first.store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => FIX });
    await expect(first.store.send(envelope)).rejects.toThrow();

    const second = harness();
    second.post.mockResolvedValue({ ok: true });
    const reused = await second.store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => LATER_FIX });
    expect(reused).toEqual(envelope);
    await second.store.send(reused);
    expect(second.post).toHaveBeenCalledWith(BOARD, envelope.body, { [IDEMPOTENCY_HEADER]: envelope.key });
    expect(localStorage.getItem(ENVELOPE_STORAGE_KEY)).toBeNull();
  });

  it("prunes another run's envelopes, Start Run envelopes once a run is active, and stale ones", async () => {
    const { store } = harness();
    const fix = () => FIX;
    const start = await store.mint("/api/runs/driver/start", { route_id: "route-1" }, { runId: null, fix });
    const oldRun = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r0", fix });
    const thisRun = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });

    store.prune(null); // no run yet: only the Start Run envelope belongs
    expect(store.pending().map((e) => e.key)).toEqual([start.key]);

    const again = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix });
    expect(again.key).not.toBe(thisRun.key);
    store.prune("r1"); // the run started: the Start Run envelope goes
    expect(store.pending().map((e) => e.key)).toEqual([again.key]);
    expect(store.pending().map((e) => e.key)).not.toContain(oldRun.key);

    vi.advanceTimersByTime(ENVELOPE_TTL_MS);
    store.prune("r1");
    expect(store.pending()).toEqual([]);
  });

  it("a stale pending envelope is not reused", async () => {
    const { store } = harness();
    const first = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => FIX });
    vi.advanceTimersByTime(ENVELOPE_TTL_MS);
    const second = await store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => LATER_FIX });
    expect(second.key).not.toBe(first.key);
  });

  it("reads back only well-formed envelopes and works without storage", async () => {
    localStorage.setItem(ENVELOPE_STORAGE_KEY, "{not json");
    expect(harness().store.pending()).toEqual([]);

    localStorage.setItem(
      ENVELOPE_STORAGE_KEY,
      JSON.stringify({
        good: { key: "00000000-0000-4000-8000-00000000abcd", path: BOARD, body: { a: 1 }, run_id: "r1", created_at: Date.now() },
        bad: { key: "not-a-uuid", path: BOARD, body: {}, run_id: "r1", created_at: Date.now() },
        worse: "nope",
      }),
    );
    expect(harness().store.pending().map((e) => e.key)).toEqual(["00000000-0000-4000-8000-00000000abcd"]);

    const memory = harness({ storage: null });
    memory.post.mockRejectedValueOnce(network()).mockResolvedValueOnce({ ok: true });
    const envelope = await memory.store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => FIX });
    await expect(memory.store.send(envelope)).rejects.toThrow();
    const reused = await memory.store.mint(BOARD, { student_id: "s1", on_bus: true }, { runId: "r1", fix: () => LATER_FIX });
    expect(reused).toBe(envelope);
  });
});

describe("device id and uuids", () => {
  it("mints one stable device id per browser and keeps it in storage", () => {
    const first = getDeviceId(localStorage);
    expect(isUuidV4(first)).toBe(true);
    expect(getDeviceId(localStorage)).toBe(first);
    expect(localStorage.getItem(DEVICE_ID_KEY)).toBe(first);
    localStorage.setItem(DEVICE_ID_KEY, "garbage");
    expect(isUuidV4(getDeviceId(localStorage))).toBe(true);
    expect(isUuidV4(getDeviceId(null))).toBe(true);
  });

  it("randomUuid is v4 with and without crypto.randomUUID", () => {
    expect(isUuidV4(randomUuid())).toBe(true);
    const noRandomUUID = { getRandomValues: (a: Uint8Array) => globalThis.crypto.getRandomValues(a) } as unknown as Crypto;
    expect(isUuidV4(randomUuid(noRandomUUID))).toBe(true);
    expect(isUuidV4(randomUuid(undefined))).toBe(true);
    const throwing = {
      randomUUID: () => {
        throw new TypeError("insecure context");
      },
      getRandomValues: (a: Uint8Array) => globalThis.crypto.getRandomValues(a),
    } as unknown as Crypto;
    expect(isUuidV4(randomUuid(throwing))).toBe(true);
    expect(randomUuid()).not.toBe(randomUuid());
  });
});
