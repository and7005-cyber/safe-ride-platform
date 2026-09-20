// The run-scoped GPS fix (GPS plan U6: R1, R2, R4, R5; F1, F5).
//
// While a run is in progress the app keeps one high-accuracy `watchPosition`
// alive so the cache is warm, and a tap reads the freshest cached fix when it
// is under 15 s old. Otherwise the tap asks once, bounded by the fix-wait
// budget, and goes out with whatever that produced: a fix, a coarse fix with
// its coordinates kept, or the reason there is none. A tap never waits past
// the budget and never fails for want of a fix.
//
// Nothing is requested outside a run (R5). The watch starts inside the Start
// Run tap — the one gesture every run has, and where the browser's permission
// prompt belongs — and stops the moment the driver context says the run is
// over, whoever ended it. Nothing the watch produces is sent anywhere in
// Phase 1; it only feeds the cache.
//
// The "location off" indicator keys off a real PERMISSION_DENIED error, not
// the Permissions API: Safari and one-time grants make the latter unreliable,
// and a live watch on Android Chrome reports POSITION_UNAVAILABLE now and then
// under a roof without meaning anything. Those are tolerated; the next tap's
// bounded request says what is really available.

import { useSyncExternalStore } from "react";

export type FixReason = "denied" | "unavailable" | "timeout" | "coarse";

export interface Fix {
  lat: number;
  lng: number;
  accuracy_m: number;
  /** ISO 8601 with the device's UTC offset. */
  captured_at: string;
}

/** What travels on an action as `fix`: a usable fix; a coarse one (accuracy
 * above the cap) with its coordinates still attached; or a reason alone. */
export type FixPayload =
  | Fix
  | (Fix & { reason: "coarse" })
  | { reason: Exclude<FixReason, "coarse"> };

export interface FixConfig {
  /** How long a tap may wait for a fix. */
  fix_wait_budget_s: number;
  /** Accuracy above this is `coarse` (kept, but unusable for checks). */
  fix_accuracy_cap_m: number;
}

/** System defaults until the driver context serves the school's values (U11). */
export const DEFAULT_FIX_CONFIG: FixConfig = { fix_wait_budget_s: 5, fix_accuracy_cap_m: 200 };

/** A cached fix younger than this is used without asking again. */
export const CACHE_FRESH_MS = 15_000;

/** Accuracy at or beyond this reads as an approximate-location grant. */
export const APPROXIMATE_ACCURACY_M = 1_000;

/** Consecutive approximate fixes before the "turn on precise location" banner. */
export const APPROXIMATE_FIX_COUNT = 3;

/** How long a Start Run tap may keep the watch alive before the context has
 * confirmed a run — covers the fix wait plus a slow start. */
export const ARM_WINDOW_MS = 60_000;

export type PermissionState = "granted" | "denied" | "prompt" | "unknown";

export interface LocationStatus {
  /** A real PERMISSION_DENIED error was seen; cleared by any later fix. */
  denied: boolean;
  /** Several consecutive fixes at 1 km or worse; cleared by a better fix. */
  approximate: boolean;
}

const NO_STATUS: LocationStatus = Object.freeze({ denied: false, approximate: false });

// GeolocationPositionError codes, spelled out because a fake in tests need
// not carry the constants.
const PERMISSION_DENIED = 1;
const TIMEOUT = 3;

export function reasonForError(error: { code: number }): Exclude<FixReason, "coarse"> {
  if (error.code === PERMISSION_DENIED) return "denied";
  if (error.code === TIMEOUT) return "timeout";
  return "unavailable";
}

function pad(value: number, width = 2): string {
  return String(Math.trunc(Math.abs(value))).padStart(width, "0");
}

/** `2026-09-20T06:40:12.345+03:00` — the device's own offset, not Z, so the
 * office can read the tap time as the driver saw it. */
export function isoWithOffset(ms: number): string {
  const d = new Date(ms);
  const offsetMinutes = -d.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? "+" : "-";
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
    + `${sign}${pad(offsetMinutes / 60)}:${pad(offsetMinutes % 60)}`
  );
}

/** The Permissions API's word, or `unknown` where it has none (older Safari,
 * a throwing query). Only the Start Run explainer reads this; the denied
 * indicator never does. */
export async function queryPermissionState(
  nav: { permissions?: { query: (d: { name: string }) => Promise<{ state: string }> } } | undefined =
    typeof navigator !== "undefined" ? (navigator as any) : undefined,
): Promise<PermissionState> {
  try {
    if (!nav?.permissions?.query) return "unknown";
    const result = await nav.permissions.query({ name: "geolocation" });
    if (result.state === "granted" || result.state === "denied" || result.state === "prompt") {
      return result.state;
    }
    return "unknown";
  } catch {
    return "unknown";
  }
}

interface Cached {
  position: GeolocationPosition;
  receivedAt: number;
}

export interface FixCaptureDeps {
  geolocation: () => Geolocation | null | undefined;
  now: () => number;
  /** For the visibilitychange restart; null where there is no document. */
  document?: Document | null;
}

export class FixCapture {
  private runId: string | null = null;
  private armedUntil = 0;
  private watchId: number | null = null;
  private last: Cached | null = null;
  private config: FixConfig = { ...DEFAULT_FIX_CONFIG };
  private status: LocationStatus = NO_STATUS;
  private approximateStreak = 0;
  private listeners = new Set<() => void>();
  private visibilityBound = false;

  constructor(private readonly deps: FixCaptureDeps) {}

  // Config ------------------------------------------------------------------

  /** Values from the driver context's config block when it has them (U11);
   * anything missing or malformed keeps the default. */
  setConfig(partial: Partial<Record<keyof FixConfig, unknown>> | null | undefined): void {
    const next = { ...this.config };
    for (const key of ["fix_wait_budget_s", "fix_accuracy_cap_m"] as const) {
      const value = partial?.[key];
      next[key] = typeof value === "number" && Number.isFinite(value) && value > 0
        ? value
        : DEFAULT_FIX_CONFIG[key];
    }
    this.config = next;
  }

  getConfig(): FixConfig {
    return { ...this.config };
  }

  // Run lifecycle -----------------------------------------------------------

  /** The Start Run tap: start the watch now, inside the gesture, before the
   * run exists. `syncRun` reconciles once the context answers; if it never
   * confirms a run the watch stops at the end of the arm window. */
  arm(): void {
    this.armedUntil = this.deps.now() + ARM_WINDOW_MS;
    this.startWatch();
  }

  /** A Start Run that failed: nothing to watch for. */
  disarm(): void {
    this.armedUntil = 0;
    if (!this.runId) this.stopWatch();
  }

  /** The driver context's word on the run in progress, on every poll. */
  syncRun(runId: string | null): void {
    this.runId = runId;
    if (runId) {
      this.armedUntil = 0;
      this.startWatch();
      return;
    }
    if (this.deps.now() < this.armedUntil) return;
    this.stopWatch();
  }

  currentRunId(): string | null {
    return this.runId;
  }

  isWatching(): boolean {
    return this.watchId != null;
  }

  // Capture -----------------------------------------------------------------

  /** The fix for a tap: the cached one when fresh, else one bounded request.
   * Resolves within the budget, always. */
  capture(): Promise<FixPayload> {
    const now = this.deps.now();
    if (this.last && now - this.last.receivedAt < CACHE_FRESH_MS) {
      return Promise.resolve(this.toPayload(this.last.position));
    }
    const geo = this.deps.geolocation();
    if (!geo) return Promise.resolve({ reason: "unavailable" });
    const budgetMs = Math.max(0, Math.round(this.config.fix_wait_budget_s * 1000));
    return new Promise<FixPayload>((resolve) => {
      let settled = false;
      const finish = (payload: FixPayload) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(payload);
      };
      const timer = setTimeout(() => finish({ reason: "timeout" }), budgetMs);
      try {
        geo.getCurrentPosition(
          (position) => {
            // Late answers still warm the cache for the next tap.
            this.remember(position);
            finish(this.toPayload(position));
          },
          (error) => {
            if (error.code === PERMISSION_DENIED) this.setStatus({ denied: true });
            finish({ reason: reasonForError(error) });
          },
          { enableHighAccuracy: true, maximumAge: CACHE_FRESH_MS, timeout: budgetMs },
        );
      } catch {
        finish({ reason: "unavailable" });
      }
    });
  }

  /** The last position the watch or a request delivered, for tests and
   * diagnostics; never sent on its own. */
  lastFix(): GeolocationPosition | null {
    return this.last?.position ?? null;
  }

  // Status ------------------------------------------------------------------

  getStatus(): LocationStatus {
    return this.status;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): LocationStatus => this.status;

  // Internals ---------------------------------------------------------------

  private startWatch(): void {
    if (this.watchId != null) return;
    const geo = this.deps.geolocation();
    if (!geo) return;
    this.bindVisibility();
    try {
      this.watchId = geo.watchPosition(
        (position) => this.remember(position),
        (error) => this.onWatchError(error),
        { enableHighAccuracy: true, maximumAge: 0 },
      );
    } catch {
      this.watchId = null;
    }
  }

  private stopWatch(): void {
    if (this.watchId != null) {
      const geo = this.deps.geolocation();
      try {
        geo?.clearWatch(this.watchId);
      } catch {
        // A watch the browser already dropped.
      }
      this.watchId = null;
    }
    // Nothing cached outside a run (R5): the next run starts cold.
    this.last = null;
    this.approximateStreak = 0;
  }

  /** A backgrounded tab's watch is throttled or dead on both platforms; the
   * return to the app is when the cache needs to be warm again. */
  private restartWatch(): void {
    if (this.watchId == null) return;
    const geo = this.deps.geolocation();
    try {
      geo?.clearWatch(this.watchId);
    } catch {
      // As above.
    }
    this.watchId = null;
    this.startWatch();
  }

  private bindVisibility(): void {
    const doc = this.deps.document;
    if (!doc || this.visibilityBound) return;
    this.visibilityBound = true;
    doc.addEventListener("visibilitychange", () => {
      if (doc.visibilityState === "visible") this.restartWatch();
    });
  }

  private onWatchError(error: GeolocationPositionError): void {
    if (error.code === PERMISSION_DENIED) {
      this.setStatus({ denied: true });
      return;
    }
    // POSITION_UNAVAILABLE and TIMEOUT from a live watch are routine (a GPS
    // dropout under a roof, a slow first lock). The watch stays up.
  }

  private remember(position: GeolocationPosition): void {
    const wasDenied = this.status.denied;
    this.last = { position, receivedAt: this.deps.now() };
    const accuracy = position.coords.accuracy;
    if (Number.isFinite(accuracy) && accuracy >= APPROXIMATE_ACCURACY_M) {
      this.approximateStreak += 1;
      this.setStatus({
        denied: false,
        approximate: this.status.approximate || this.approximateStreak >= APPROXIMATE_FIX_COUNT,
      });
    } else {
      this.approximateStreak = 0;
      this.setStatus(NO_STATUS);
    }
    // Permission came back after a denial (the driver fixed the setting):
    // the dead watch is replaced so the cache warms again.
    if (wasDenied && this.watchId != null) this.restartWatch();
  }

  private toPayload(position: GeolocationPosition): FixPayload {
    const accuracy = position.coords.accuracy;
    const fix: Fix = {
      lat: position.coords.latitude,
      lng: position.coords.longitude,
      accuracy_m: accuracy,
      captured_at: isoWithOffset(this.captureTime(position)),
    };
    if (Number.isFinite(accuracy) && accuracy > this.config.fix_accuracy_cap_m) {
      return { ...fix, reason: "coarse" };
    }
    return fix;
  }

  /** The position's own timestamp unless it is plainly not wall-clock time
   * (an old WebKit reported seconds since 2001), then the capture instant. */
  private captureTime(position: GeolocationPosition): number {
    const now = this.deps.now();
    const stamp = position.timestamp;
    if (typeof stamp === "number" && Number.isFinite(stamp) && Math.abs(now - stamp) < 86_400_000) {
      return stamp;
    }
    return now;
  }

  private setStatus(partial: Partial<LocationStatus>): void {
    const next = { ...this.status, ...partial };
    if (next.denied === this.status.denied && next.approximate === this.status.approximate) return;
    this.status = next;
    for (const listener of this.listeners) listener();
  }
}

/** The app's one capture. */
export const fixCapture = new FixCapture({
  geolocation: () => (typeof navigator !== "undefined" ? navigator.geolocation : null),
  now: () => Date.now(),
  document: typeof document !== "undefined" ? document : null,
});

/** The denied / approximate indicator state, for the banner. */
export function useLocationStatus(): LocationStatus {
  return useSyncExternalStore(fixCapture.subscribe, fixCapture.getSnapshot, fixCapture.getSnapshot);
}
