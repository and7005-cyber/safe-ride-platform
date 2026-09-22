// GPS plan U14 — the ping stream and the screen wake lock, against scripted
// deps. R24: pings only during a run and only while the page is visible;
// R26: the cadence (every interval moving, every third stationary) and the
// six-fix batch; R28: session-mismatch pauses until the next accepted tap,
// too-soon skips one interval, a failed batch is never re-sent; R25: the
// wake lock is asked for on the tap, re-asked on return, released with the
// run, and a refusal degrades to the hint.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_PING_INTERVAL_S,
  MOVING_DISPLACEMENT_M,
  PING_MAX_BATCH,
  RunPings,
  STATIONARY_EVERY_N_INTERVALS,
  distanceM,
  pingIntervalMs,
  type PingBatch,
} from "@/features/driver/useRunPings";
import {
  WAKE_LOCK_HINT,
  WakeLockKeeper,
  showWakeLockHint,
  type WakeLockSentinelLike,
} from "@/features/driver/useWakeLock";
import { ActionEnvelopes } from "@/lib/actionEnvelope";
import { ApiError } from "@/lib/apiClient";
import { FixCapture, type Fix } from "@/lib/geo/fixCapture";

const INTERVAL_S = 10;
const INTERVAL_MS = INTERVAL_S * 1000;
const METRE = 1 / 111_195; // degrees of latitude

function fakeDocument() {
  const listeners: (() => void)[] = [];
  return {
    visibilityState: "visible" as DocumentVisibilityState,
    addEventListener: (_type: string, fn: () => void) => {
      listeners.push(fn);
    },
    fire() {
      for (const fn of listeners) fn();
    },
  };
}

function fix(over: Partial<Fix> = {}): Fix {
  return {
    lat: -1.2921,
    lng: 36.8219,
    accuracy_m: 20,
    captured_at: new Date().toISOString(),
    ...over,
  };
}

function build(over: { post?: (body: PingBatch) => Promise<unknown>; freshFix?: () => Fix | null } = {}) {
  const positionListeners = new Set<(fix: Fix) => void>();
  const post = vi.fn(over.post ?? (async () => ({ accepted: 1 })));
  const doc = fakeDocument();
  const stream = new RunPings({
    post,
    onPosition: (listener) => {
      positionListeners.add(listener);
      return () => positionListeners.delete(listener);
    },
    freshFix: over.freshFix,
    deviceId: () => "device-1",
    now: () => Date.now(),
    document: doc as unknown as Document,
  });
  return {
    stream,
    post,
    doc,
    emit(f: Fix) {
      for (const listener of positionListeners) listener(f);
    },
    subscribed: () => positionListeners.size,
  };
}

async function tick(ms = INTERVAL_MS) {
  await vi.advanceTimersByTimeAsync(ms);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-22T06:40:00.000Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("run lifecycle (R24)", () => {
  it("sends nothing before a run, starts with one, and stops the moment the context reports none", async () => {
    const { stream, post, emit, subscribed } = build();
    stream.sync(null, INTERVAL_S);
    emit(fix());
    await tick(3 * INTERVAL_MS);
    expect(post).not.toHaveBeenCalled();
    expect(subscribed()).toBe(0);

    stream.sync("run-1", INTERVAL_S);
    expect(stream.isTicking()).toBe(true);
    expect(subscribed()).toBe(1);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]![0]).toEqual({
      run_id: "run-1",
      fixes: [expect.objectContaining({ lat: -1.2921 })],
      device_id: "device-1",
    });

    stream.sync(null, INTERVAL_S);
    expect(stream.isTicking()).toBe(false);
    expect(subscribed()).toBe(0);
    emit(fix());
    await tick(3 * INTERVAL_MS);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("is idempotent across context polls and re-times the interval when the school changes it", async () => {
    const { stream, post, emit } = build();
    stream.sync("run-1", INTERVAL_S);
    stream.sync("run-1", INTERVAL_S);
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    // A shorter interval from School Settings: the next batch comes sooner.
    stream.sync("run-1", 5);
    emit(fix({ lat: -1.2921 + 100 * METRE }));
    await tick(5_000);
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("seeds the first batch with the watch's fresh fix when the run is confirmed, but not while hidden", async () => {
    const fresh = fix({ accuracy_m: 7 });
    const { stream, post } = build({ freshFix: () => fresh });
    stream.sync("run-1", INTERVAL_S);
    expect(stream.pendingFixes()).toBe(1);
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]![0].fixes).toEqual([fresh]);

    const hidden = build({ freshFix: () => fresh });
    hidden.doc.visibilityState = "hidden";
    hidden.stream.sync("run-2", INTERVAL_S);
    expect(hidden.stream.pendingFixes()).toBe(0);

    const stale = build({ freshFix: () => null });
    stale.stream.sync("run-3", INTERVAL_S);
    expect(stale.stream.pendingFixes()).toBe(0);
  });

  it("uses the default interval when the context serves none or nonsense", () => {
    expect(pingIntervalMs(undefined)).toBe(DEFAULT_PING_INTERVAL_S * 1000);
    expect(pingIntervalMs("7")).toBe(DEFAULT_PING_INTERVAL_S * 1000);
    expect(pingIntervalMs(-3)).toBe(DEFAULT_PING_INTERVAL_S * 1000);
    expect(pingIntervalMs(7)).toBe(7_000);
  });
});

describe("cadence and batch (R26)", () => {
  it("posts every interval while moving and every third interval while stationary", async () => {
    const { stream, post, emit } = build();
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1); // first batch: nothing accepted yet

    // Stationary: fresh fixes a few metres from the accepted one.
    for (let i = 1; i <= STATIONARY_EVERY_N_INTERVALS - 1; i++) {
      emit(fix({ lat: -1.2921 + 3 * i * METRE }));
      await tick();
      expect(post).toHaveBeenCalledTimes(1);
    }
    const parked = -1.2921 + 5 * METRE;
    emit(fix({ lat: parked }));
    await tick();
    expect(post).toHaveBeenCalledTimes(2); // the third interval

    // Moving: past the displacement threshold since the last ACCEPTED fix
    // (the parked one), interval after interval.
    const leg1 = parked + (MOVING_DISPLACEMENT_M + 10) * METRE;
    emit(fix({ lat: leg1 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(3);
    emit(fix({ lat: leg1 + (MOVING_DISPLACEMENT_M + 10) * METRE }));
    await tick();
    expect(post).toHaveBeenCalledTimes(4);
    // Under the threshold from the last accepted fix is stationary again.
    const leg2 = leg1 + (MOVING_DISPLACEMENT_M + 10) * METRE;
    emit(fix({ lat: leg2 + (MOVING_DISPLACEMENT_M - 10) * METRE }));
    await tick();
    expect(post).toHaveBeenCalledTimes(4);
  });

  it("sends nothing on an interval with no new fix", async () => {
    const { stream, post } = build();
    stream.sync("run-1", INTERVAL_S);
    await tick(4 * INTERVAL_MS);
    expect(post).not.toHaveBeenCalled();
  });

  it("caps a batch at six, keeping the newest, and clears the buffer once sent", async () => {
    const { stream, post, emit } = build();
    stream.sync("run-1", INTERVAL_S);
    for (let i = 0; i < 10; i++) emit(fix({ accuracy_m: i }));
    expect(stream.pendingFixes()).toBe(PING_MAX_BATCH);
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    const sent = post.mock.calls[0]![0].fixes;
    expect(sent).toHaveLength(PING_MAX_BATCH);
    expect(sent.map((f) => f.accuracy_m)).toEqual([4, 5, 6, 7, 8, 9]);
    expect(stream.pendingFixes()).toBe(0);
  });

  it("measures displacement on the great circle", () => {
    const a = { lat: -1.2921, lng: 36.8219 };
    expect(distanceM(a, { lat: a.lat + 100 * METRE, lng: a.lng })).toBeCloseTo(100, 0);
    expect(distanceM(a, a)).toBe(0);
  });
});

describe("visibility (R24, AE13)", () => {
  it("stops when the page is hidden, drops what was gathered, and resumes on return", async () => {
    const { stream, post, emit, doc } = build();
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);

    doc.visibilityState = "hidden";
    doc.fire();
    expect(stream.isTicking()).toBe(false);
    emit(fix({ lat: -1.3 }));
    await tick(3 * INTERVAL_MS);
    expect(post).toHaveBeenCalledTimes(1);
    expect(stream.pendingFixes()).toBe(0);

    doc.visibilityState = "visible";
    doc.fire();
    expect(stream.isTicking()).toBe(true);
    emit(fix({ lat: -1.3 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1]![0].fixes[0]!.lat).toBe(-1.3);
  });

  it("does not start ticking for a run reported while hidden, and starts on return", async () => {
    const { stream, post, emit, doc } = build();
    doc.visibilityState = "hidden";
    stream.sync("run-1", INTERVAL_S);
    expect(stream.isTicking()).toBe(false);
    emit(fix());
    await tick(2 * INTERVAL_MS);
    expect(post).not.toHaveBeenCalled();
    doc.visibilityState = "visible";
    doc.fire();
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("refusals (R28)", () => {
  it("pauses on session-mismatch until the next accepted tap, then resumes", async () => {
    let refuse = true;
    const { stream, post, emit } = build({
      post: async () => {
        if (refuse) throw new ApiError("bound elsewhere", 409, "session-mismatch");
        return { accepted: 1 };
      },
    });
    const seen: boolean[] = [];
    stream.subscribe(() => seen.push(stream.getSnapshot().suspended));
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    expect(stream.getSnapshot().suspended).toBe(true);
    expect(seen).toContain(true);

    // Paused: fresh fixes, no posts, however long.
    for (let i = 0; i < 4; i++) {
      emit(fix({ lat: -1.2921 + 200 * i * METRE }));
      await tick();
    }
    expect(post).toHaveBeenCalledTimes(1);

    refuse = false;
    stream.noteTapSucceeded();
    expect(stream.getSnapshot().suspended).toBe(false);
    emit(fix({ lat: -1.31 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("waits one interval after ping-too-soon", async () => {
    let calls = 0;
    const { stream, post, emit } = build({
      post: async () => {
        calls += 1;
        if (calls === 1) throw new ApiError("too soon", 429, "ping-too-soon");
        return { accepted: 1 };
      },
    });
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    emit(fix({ lat: -1.3 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(1); // the skipped interval
    await tick();
    expect(post).toHaveBeenCalledTimes(2);
    expect(stream.getSnapshot().suspended).toBe(false);
  });

  it("never re-sends a failed batch; the next interval carries fresh fixes only", async () => {
    let calls = 0;
    const { stream, post, emit } = build({
      post: async () => {
        calls += 1;
        if (calls === 1) throw new TypeError("Failed to fetch");
        return { accepted: 1 };
      },
    });
    stream.sync("run-1", INTERVAL_S);
    emit(fix({ accuracy_m: 1 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    await tick(); // nothing new: nothing sent, the failed batch included
    expect(post).toHaveBeenCalledTimes(1);
    emit(fix({ accuracy_m: 2 }));
    await tick();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1]![0].fixes.map((f) => f.accuracy_m)).toEqual([2]);
  });

  it("ignores the answer to a batch in flight when the run ended under it", async () => {
    let resolve!: () => void;
    const { stream, post, emit } = build({
      post: () => new Promise<unknown>((r) => {
        resolve = () => r({ accepted: 1 });
      }),
    });
    stream.sync("run-1", INTERVAL_S);
    emit(fix());
    await tick();
    expect(post).toHaveBeenCalledTimes(1);
    stream.sync(null, INTERVAL_S);
    resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(stream.getSnapshot()).toEqual({ active: false, suspended: false, lastAcceptedAt: null });
  });
});

describe("the fix source and the tap signal", () => {
  it("fixCapture.freshFix is the cached fix inside the cache window and null past it or outside a run", () => {
    const watchers = new Map<number, PositionCallback>();
    const geo = {
      watchPosition: vi.fn((ok: PositionCallback) => {
        watchers.set(1, ok);
        return 1;
      }),
      clearWatch: vi.fn(),
      getCurrentPosition: vi.fn(),
    } as unknown as Geolocation;
    const capture = new FixCapture({ geolocation: () => geo, now: () => Date.now(), document: null });
    expect(capture.freshFix()).toBeNull();
    capture.arm();
    watchers.get(1)!({
      coords: { latitude: -1.1, longitude: 36.9, accuracy: 12, altitude: null, altitudeAccuracy: null, heading: null, speed: null },
      timestamp: Date.now(),
    } as GeolocationPosition);
    expect(capture.freshFix()).toEqual({
      lat: -1.1, lng: 36.9, accuracy_m: 12, captured_at: expect.stringMatching(/^\d{4}-/),
    });
    vi.advanceTimersByTime(15_000);
    expect(capture.freshFix()).toBeNull();
    capture.syncRun(null);
    vi.advanceTimersByTime(60_000);
    capture.syncRun(null); // the arm window has lapsed: watch down, cache empty
    expect(capture.freshFix()).toBeNull();
  });

  it("fixCapture.onPosition delivers every fix the watch produces, as the request shape", () => {
    const watchers = new Map<number, PositionCallback>();
    const geo = {
      watchPosition: vi.fn((ok: PositionCallback) => {
        watchers.set(1, ok);
        return 1;
      }),
      clearWatch: vi.fn(),
      getCurrentPosition: vi.fn(),
    } as unknown as Geolocation;
    const capture = new FixCapture({ geolocation: () => geo, now: () => Date.now(), document: null });
    const seen: Fix[] = [];
    const off = capture.onPosition((f) => seen.push(f));
    capture.syncRun("run-1");
    watchers.get(1)!({
      coords: { latitude: -1.1, longitude: 36.9, accuracy: 12, altitude: null, altitudeAccuracy: null, heading: null, speed: null },
      timestamp: Date.now(),
    } as GeolocationPosition);
    expect(seen).toEqual([
      { lat: -1.1, lng: 36.9, accuracy_m: 12, captured_at: expect.stringMatching(/^\d{4}-/) },
    ]);
    off();
    watchers.get(1)!({
      coords: { latitude: -1.2, longitude: 36.9, accuracy: 12, altitude: null, altitudeAccuracy: null, heading: null, speed: null },
      timestamp: Date.now(),
    } as GeolocationPosition);
    expect(seen).toHaveLength(1);
  });

  it("actionEnvelopes.onSent fires once per accepted action with its path, never on a failure", async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith("/end")) throw new ApiError("refused", 409, null);
      return { ok: true };
    });
    const envelopes = new ActionEnvelopes({
      storage: null,
      post,
      uuid: () => "11111111-1111-4111-8111-111111111111",
      now: () => Date.now(),
      sleep: async () => {},
      deviceId: () => "device-1",
    });
    const sent: string[] = [];
    envelopes.onSent((path) => sent.push(path));
    const arrive = await envelopes.mint("/api/runs/driver/arrive", { run_id: "r" }, { runId: "r", fix: () => fix() });
    await envelopes.send(arrive);
    const end = await envelopes.mint("/api/runs/driver/end", { run_id: "r" }, { runId: "r", fix: () => fix() });
    await expect(envelopes.send(end)).rejects.toBeInstanceOf(ApiError);
    expect(sent).toEqual(["/api/runs/driver/arrive"]);
  });
});

// --- the wake lock (R25) ------------------------------------------------------

function fakeWakeLock(behaviour: "grant" | "deny" = "grant") {
  const sentinels: (WakeLockSentinelLike & { fireRelease: () => void; releaseCalls: number })[] = [];
  const request = vi.fn(async (_type: "screen") => {
    if (behaviour === "deny") throw new DOMException("refused", "NotAllowedError");
    let listener: (() => void) | null = null;
    const sentinel = {
      released: false,
      releaseCalls: 0,
      addEventListener: (_t: "release", fn: () => void) => {
        listener = fn;
      },
      release: async () => {
        sentinel.released = true;
        sentinel.releaseCalls += 1;
        listener?.();
      },
      fireRelease: () => {
        sentinel.released = true;
        listener?.();
      },
    };
    sentinels.push(sentinel);
    return sentinel;
  });
  return { api: { request }, request, sentinels };
}

describe("wake lock (R25)", () => {
  it("acquires on request, holds across polls, and releases with the run", async () => {
    const { api, request, sentinels } = fakeWakeLock();
    const doc = fakeDocument();
    const keeper = new WakeLockKeeper({ wakeLock: () => api, document: doc as unknown as Document });
    expect(await keeper.request()).toBe(true);
    expect(keeper.getSnapshot()).toEqual({ wanted: true, held: true, reason: null });
    // The layout's polls: no second request while held.
    keeper.setWanted(true);
    keeper.setWanted(true);
    expect(await keeper.request()).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
    expect(showWakeLockHint(keeper.getSnapshot())).toBe(false);

    keeper.setWanted(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(sentinels[0]!.releaseCalls).toBe(1);
    expect(keeper.getSnapshot()).toEqual({ wanted: false, held: false, reason: null });
  });

  it("re-acquires on visibilitychange to visible while a run wants it", async () => {
    const { api, request, sentinels } = fakeWakeLock();
    const doc = fakeDocument();
    const keeper = new WakeLockKeeper({ wakeLock: () => api, document: doc as unknown as Document });
    await keeper.request();
    // The browser drops it when the page goes away.
    doc.visibilityState = "hidden";
    doc.fire();
    sentinels[0]!.fireRelease();
    expect(keeper.getSnapshot().held).toBe(false);
    expect(request).toHaveBeenCalledTimes(1);
    doc.visibilityState = "visible";
    doc.fire();
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(2);
    expect(keeper.getSnapshot().held).toBe(true);
  });

  it("NotAllowedError degrades to the hint, asks again only on return, and a missing API reads unsupported", async () => {
    const { api, request } = fakeWakeLock("deny");
    const doc = fakeDocument();
    const keeper = new WakeLockKeeper({ wakeLock: () => api, document: doc as unknown as Document });
    expect(await keeper.request()).toBe(false);
    expect(keeper.getSnapshot()).toEqual({ wanted: true, held: false, reason: "denied" });
    expect(showWakeLockHint(keeper.getSnapshot())).toBe(true);
    expect(WAKE_LOCK_HINT).toMatch(/Keep your screen on during the run/);
    // Polls do not hammer a refusing browser.
    keeper.setWanted(true);
    keeper.setWanted(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    doc.fire(); // visible again: one more try
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(2);

    const none = new WakeLockKeeper({ wakeLock: () => null, document: doc as unknown as Document });
    expect(await none.request()).toBe(false);
    expect(none.getSnapshot()).toEqual({ wanted: true, held: false, reason: "unsupported" });
    expect(showWakeLockHint(none.getSnapshot())).toBe(true);
  });

  it("does not ask while hidden and releases a lock granted after the run ended", async () => {
    const { api, request, sentinels } = fakeWakeLock();
    const doc = fakeDocument();
    doc.visibilityState = "hidden";
    const keeper = new WakeLockKeeper({ wakeLock: () => api, document: doc as unknown as Document });
    expect(await keeper.request()).toBe(false);
    expect(request).not.toHaveBeenCalled();
    expect(showWakeLockHint(keeper.getSnapshot())).toBe(false);

    doc.visibilityState = "visible";
    const late: WakeLockSentinelLike & { release: ReturnType<typeof vi.fn> } = {
      released: false,
      release: vi.fn(async () => {
        late.released = true;
      }),
    };
    let grant!: () => void;
    request.mockImplementationOnce(() => new Promise((resolve) => {
      grant = () => resolve(late as any);
    }));
    const pending = keeper.request();
    keeper.setWanted(false);
    grant();
    expect(await pending).toBe(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(keeper.getSnapshot().held).toBe(false);
    // The lock that arrived after the run ended was let go at once.
    expect(late.release).toHaveBeenCalledTimes(1);
    expect(sentinels).toHaveLength(0);
  });
});
