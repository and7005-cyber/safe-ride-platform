// GPS plan U6 — the run-scoped fix capture, against a scripted geolocation.
// R5: nothing before Start Run, nothing after End Run; R2: the 15 s cache,
// the bounded fallback, a reason when there is no fix; R4: the indicator
// keys off a real denial (or approximate fixes) and clears on a good fix.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  APPROXIMATE_FIX_COUNT,
  ARM_WINDOW_MS,
  CACHE_FRESH_MS,
  DEFAULT_FIX_CONFIG,
  FixCapture,
  isoWithOffset,
  queryPermissionState,
  reasonForError,
} from "@/lib/geo/fixCapture";

type Ok = PositionCallback;
type Err = PositionErrorCallback;

function fakeGeolocation() {
  const watchers = new Map<number, { ok: Ok; err: Err }>();
  let nextId = 1;
  let onCurrent: (ok: Ok, err: Err) => void = () => {};
  const geo = {
    watchPosition: vi.fn((ok: Ok, err: Err, _opts?: PositionOptions) => {
      const id = nextId++;
      watchers.set(id, { ok, err });
      return id;
    }),
    clearWatch: vi.fn((id: number) => {
      watchers.delete(id);
    }),
    getCurrentPosition: vi.fn((ok: Ok, err: Err, _opts?: PositionOptions) => onCurrent(ok, err)),
  };
  return {
    geo: geo as unknown as Geolocation,
    mocks: geo,
    watchers,
    answerCurrent(fn: (ok: Ok, err: Err) => void) {
      onCurrent = fn;
    },
    emit(pos: GeolocationPosition) {
      for (const w of [...watchers.values()]) w.ok(pos);
    },
    emitError(error: GeolocationPositionError) {
      for (const w of [...watchers.values()]) w.err(error);
    },
  };
}

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

function position(
  over: Partial<GeolocationCoordinates> & { timestamp?: number } = {},
): GeolocationPosition {
  const { timestamp, ...coords } = over;
  return {
    coords: {
      latitude: -1.2921,
      longitude: 36.8219,
      accuracy: 20,
      altitude: null,
      altitudeAccuracy: null,
      heading: null,
      speed: null,
      ...coords,
    },
    timestamp: timestamp ?? Date.now(),
  } as GeolocationPosition;
}

function geoError(code: number): GeolocationPositionError {
  return {
    code,
    message: "",
    PERMISSION_DENIED: 1,
    POSITION_UNAVAILABLE: 2,
    TIMEOUT: 3,
  } as GeolocationPositionError;
}

function build(geo: Geolocation | undefined, doc = fakeDocument()) {
  const capture = new FixCapture({
    geolocation: () => geo,
    now: () => Date.now(),
    document: doc as unknown as Document,
  });
  return { capture, doc };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-20T06:40:00.000Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("run lifecycle (R5)", () => {
  it("does not watch until a run is in progress and stops when it ends", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun(null);
    expect(fake.mocks.watchPosition).not.toHaveBeenCalled();

    capture.syncRun("run-1");
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(1);
    expect(fake.mocks.watchPosition.mock.calls[0]![2]).toMatchObject({ enableHighAccuracy: true });
    capture.syncRun("run-1"); // every poll
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(1);
    expect(capture.isWatching()).toBe(true);

    capture.syncRun(null);
    expect(fake.mocks.clearWatch).toHaveBeenCalledWith(1);
    expect(capture.isWatching()).toBe(false);
  });

  it("the Start Run tap arms the watch before the run exists; a failed start disarms it", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.arm();
    expect(capture.isWatching()).toBe(true);
    // A poll that answers "no run" while the start is in flight changes nothing.
    capture.syncRun(null);
    expect(capture.isWatching()).toBe(true);
    capture.disarm();
    expect(capture.isWatching()).toBe(false);

    // Confirmed by the context: the run owns the watch, disarm is a no-op.
    capture.arm();
    capture.syncRun("run-2");
    capture.disarm();
    expect(capture.isWatching()).toBe(true);
  });

  it("an arm nobody confirmed expires with the next poll", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.arm();
    vi.advanceTimersByTime(ARM_WINDOW_MS - 1);
    capture.syncRun(null);
    expect(capture.isWatching()).toBe(true);
    vi.advanceTimersByTime(2);
    capture.syncRun(null);
    expect(capture.isWatching()).toBe(false);
  });

  it("empties the cache when the run ends: the next run starts cold", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    fake.emit(position());
    expect(capture.lastFix()).not.toBeNull();
    capture.syncRun(null);
    expect(capture.lastFix()).toBeNull();

    capture.syncRun("run-2");
    fake.answerCurrent((ok) => ok(position({ accuracy: 12 })));
    const fix = await capture.capture();
    expect(fake.mocks.getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(fix).toMatchObject({ accuracy_m: 12 });
  });

  it("lives without a geolocation API: no watch, and every tap says unavailable", async () => {
    const { capture } = build(undefined);
    capture.syncRun("run-1");
    expect(capture.isWatching()).toBe(false);
    await expect(capture.capture()).resolves.toEqual({ reason: "unavailable" });
  });
});

describe("capture (R2)", () => {
  it("uses a cached fix under 15 s old and asks again past it", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    fake.emit(position({ latitude: -1.3, longitude: 36.8, accuracy: 18 }));

    vi.advanceTimersByTime(CACHE_FRESH_MS - 5_000);
    const cached = await capture.capture();
    expect(cached).toEqual({
      lat: -1.3,
      lng: 36.8,
      accuracy_m: 18,
      captured_at: isoWithOffset(Date.now() - (CACHE_FRESH_MS - 5_000)),
    });
    expect(fake.mocks.getCurrentPosition).not.toHaveBeenCalled();

    vi.advanceTimersByTime(5_001);
    fake.answerCurrent((ok) => ok(position({ accuracy: 9 })));
    const fresh = await capture.capture();
    expect(fake.mocks.getCurrentPosition).toHaveBeenCalledTimes(1);
    expect(fresh).toMatchObject({ accuracy_m: 9 });
    expect(fake.mocks.getCurrentPosition.mock.calls[0]![2]).toEqual({
      enableHighAccuracy: true,
      maximumAge: CACHE_FRESH_MS,
      timeout: DEFAULT_FIX_CONFIG.fix_wait_budget_s * 1000,
    });
  });

  it("resolves `timeout` at the budget when nothing answers; a late answer still warms the cache", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    let lateOk: Ok | null = null;
    fake.answerCurrent((ok) => {
      lateOk = ok;
    });

    let settled: unknown = null;
    const pending = capture.capture().then((v) => (settled = v));
    await vi.advanceTimersByTimeAsync(DEFAULT_FIX_CONFIG.fix_wait_budget_s * 1000 - 1);
    expect(settled).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(settled).toEqual({ reason: "timeout" });

    lateOk!(position({ accuracy: 7 }));
    expect(capture.lastFix()?.coords.accuracy).toBe(7);
    await expect(capture.capture()).resolves.toMatchObject({ accuracy_m: 7 });
    expect(fake.mocks.getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it("honours the budget the context serves and falls back to the default on nonsense", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    capture.setConfig({ fix_wait_budget_s: 2, fix_accuracy_cap_m: 300 });
    expect(capture.getConfig()).toEqual({ fix_wait_budget_s: 2, fix_accuracy_cap_m: 300 });
    fake.answerCurrent(() => {});

    let settled: unknown = null;
    const pending = capture.capture().then((v) => (settled = v));
    await vi.advanceTimersByTimeAsync(1_999);
    expect(settled).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(settled).toEqual({ reason: "timeout" });

    capture.setConfig({ fix_wait_budget_s: -1, fix_accuracy_cap_m: "wide" });
    expect(capture.getConfig()).toEqual(DEFAULT_FIX_CONFIG);
    capture.setConfig(undefined);
    expect(capture.getConfig()).toEqual(DEFAULT_FIX_CONFIG);
  });

  it("maps the browser's errors to reasons and marks only a real denial", async () => {
    for (const [code, reason] of [[1, "denied"], [2, "unavailable"], [3, "timeout"]] as const) {
      const fake = fakeGeolocation();
      const { capture } = build(fake.geo);
      capture.syncRun("run-1");
      fake.answerCurrent((_ok, err) => err(geoError(code)));
      await expect(capture.capture()).resolves.toEqual({ reason });
      expect(capture.getStatus().denied).toBe(code === 1);
    }
  });

  it("a throwing geolocation reads as unavailable", async () => {
    const fake = fakeGeolocation();
    fake.mocks.getCurrentPosition.mockImplementation(() => {
      throw new Error("not allowed");
    });
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    await expect(capture.capture()).resolves.toEqual({ reason: "unavailable" });
  });

  it("coarse: accuracy above the cap keeps the coordinates and adds the reason; the cap is configurable", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    fake.emit(position({ accuracy: 350 }));
    const coarse = await capture.capture();
    expect(coarse).toEqual({
      lat: -1.2921,
      lng: 36.8219,
      accuracy_m: 350,
      captured_at: isoWithOffset(Date.now()),
      reason: "coarse",
    });

    capture.setConfig({ fix_accuracy_cap_m: 400 });
    const usable = await capture.capture();
    expect(usable).not.toHaveProperty("reason");
    expect(usable).toMatchObject({ accuracy_m: 350 });

    // Exactly at the cap is still usable; a non-finite accuracy is left for
    // the server's plausibility path rather than called coarse here.
    capture.setConfig({ fix_accuracy_cap_m: 350 });
    expect(await capture.capture()).not.toHaveProperty("reason");
    fake.emit(position({ accuracy: Number.NaN }));
    expect(await capture.capture()).not.toHaveProperty("reason");
  });

  it("stamps captured_at from the position's own time, with the device offset; a bogus timestamp falls back to now", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    const taken = Date.now() - 3_000;
    fake.emit(position({ timestamp: taken }));
    expect(await capture.capture()).toMatchObject({ captured_at: isoWithOffset(taken) });

    fake.emit(position({ timestamp: 12_345 })); // seconds since 2001, not ms since 1970
    expect(await capture.capture()).toMatchObject({ captured_at: isoWithOffset(Date.now()) });
  });
});

describe("status (R4)", () => {
  it("a denial from the watch raises the indicator; a later fix clears it and replaces the dead watch", async () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    const listener = vi.fn();
    capture.subscribe(listener);
    capture.syncRun("run-1");
    fake.emitError(geoError(1));
    expect(capture.getStatus()).toEqual({ denied: true, approximate: false });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(capture.isWatching()).toBe(true);

    // The driver fixed the setting: the next tap's request succeeds.
    fake.answerCurrent((ok) => ok(position()));
    await capture.capture();
    expect(capture.getStatus()).toEqual({ denied: false, approximate: false });
    expect(fake.mocks.clearWatch).toHaveBeenCalledWith(1);
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(2);
  });

  it("three consecutive fixes at 1 km or worse raise the precise-location indicator; a better fix clears it", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    for (let i = 1; i < APPROXIMATE_FIX_COUNT; i++) {
      fake.emit(position({ accuracy: 1_000 }));
      expect(capture.getStatus().approximate).toBe(false);
    }
    fake.emit(position({ accuracy: 2_400 }));
    expect(capture.getStatus()).toEqual({ denied: false, approximate: true });
    // It stays up through further approximate fixes and goes on a good one.
    fake.emit(position({ accuracy: 1_500 }));
    expect(capture.getStatus().approximate).toBe(true);
    fake.emit(position({ accuracy: 30 }));
    expect(capture.getStatus()).toEqual({ denied: false, approximate: false });

    // The streak is consecutive: a good fix in between resets it.
    fake.emit(position({ accuracy: 1_000 }));
    fake.emit(position({ accuracy: 1_000 }));
    fake.emit(position({ accuracy: 40 }));
    fake.emit(position({ accuracy: 1_000 }));
    fake.emit(position({ accuracy: 1_000 }));
    expect(capture.getStatus().approximate).toBe(false);
  });

  it("an approximate fix still clears a denial (permission is evidently on)", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    capture.syncRun("run-1");
    fake.emitError(geoError(1));
    fake.emit(position({ accuracy: 1_200 }));
    expect(capture.getStatus()).toEqual({ denied: false, approximate: false });
  });

  it("tolerates unavailable and timeout errors from the watch: nothing changes, the watch stays up", () => {
    const fake = fakeGeolocation();
    const { capture } = build(fake.geo);
    const listener = vi.fn();
    capture.subscribe(listener);
    capture.syncRun("run-1");
    fake.emitError(geoError(2));
    fake.emitError(geoError(3));
    expect(capture.getStatus()).toEqual({ denied: false, approximate: false });
    expect(listener).not.toHaveBeenCalled();
    expect(fake.mocks.clearWatch).not.toHaveBeenCalled();
    expect(capture.isWatching()).toBe(true);
  });
});

describe("visibilitychange", () => {
  it("restarts the watch when the page comes back, and only while a run is watched", () => {
    const fake = fakeGeolocation();
    const { capture, doc } = build(fake.geo);
    capture.syncRun("run-1");
    doc.fire();
    expect(fake.mocks.clearWatch).toHaveBeenCalledWith(1);
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(2);
    expect(capture.isWatching()).toBe(true);

    doc.visibilityState = "hidden";
    doc.fire();
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(2);

    doc.visibilityState = "visible";
    capture.syncRun(null);
    doc.fire();
    expect(fake.mocks.watchPosition).toHaveBeenCalledTimes(2);
    expect(capture.isWatching()).toBe(false);
  });
});

describe("helpers", () => {
  it("reasonForError maps the three codes", () => {
    expect(reasonForError({ code: 1 })).toBe("denied");
    expect(reasonForError({ code: 2 })).toBe("unavailable");
    expect(reasonForError({ code: 3 })).toBe("timeout");
    expect(reasonForError({ code: 42 })).toBe("unavailable");
  });

  it("isoWithOffset writes ISO 8601 with the device offset and round-trips the instant", () => {
    const ms = Date.UTC(2026, 8, 20, 6, 40, 12, 345);
    const text = isoWithOffset(ms);
    expect(text).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$/);
    expect(new Date(text).getTime()).toBe(ms);
  });

  it("queryPermissionState passes the three states through and says unknown otherwise", async () => {
    const nav = (state: string) => ({ permissions: { query: async () => ({ state }) } });
    expect(await queryPermissionState(nav("granted"))).toBe("granted");
    expect(await queryPermissionState(nav("denied"))).toBe("denied");
    expect(await queryPermissionState(nav("prompt"))).toBe("prompt");
    expect(await queryPermissionState(nav("something"))).toBe("unknown");
    expect(await queryPermissionState({})).toBe("unknown");
    expect(await queryPermissionState(undefined)).toBe("unknown");
    expect(
      await queryPermissionState({
        permissions: {
          query: async () => {
            throw new TypeError("not supported");
          },
        },
      }),
    ).toBe("unknown");
  });
});
