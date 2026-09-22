// The screen wake lock for an active run (GPS plan U14: R25; F6).
//
// The phone's GPS watch and the ping stream live only while the page is
// visible, and a locked screen ends both (Risks: foreground-only tracking).
// So the app asks the browser to keep the screen on: requested on Start Run
// — inside the tap's gesture chain, once the run exists — held while the
// context reports a run in progress (a reload mid-run re-asks), released when
// the run ends, and re-acquired on `visibilitychange` to visible, because the
// browser drops it whenever the page is hidden.
//
// Where the API is missing (older Safari, a home-screen install before iOS
// 18.4) or refuses (`NotAllowedError`: battery saver, a policy, no visible
// document), nothing breaks: the Run page shows a one-line hint asking the
// driver to keep the screen on, and every tap keeps working.

import { useEffect, useSyncExternalStore } from "react";
import type { DriverContext } from "@/features/driver/driverHooks";

export type WakeLockReason = "unsupported" | "denied" | null;

export interface WakeLockStatus {
  /** A run is in progress and the lock should be held. */
  wanted: boolean;
  /** The browser holds it now. */
  held: boolean;
  /** Why it is not held when wanted: no API, or the last request refused. */
  reason: WakeLockReason;
}

/** The hint the Run page shows when the lock is wanted and not held. */
export const WAKE_LOCK_HINT = "Keep your screen on during the run — this phone will not keep it awake by itself.";

export interface WakeLockSentinelLike {
  released?: boolean;
  release: () => Promise<void>;
  addEventListener?: (type: "release", listener: () => void) => void;
  onrelease?: (() => void) | null;
}

export interface WakeLockLike {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
}

export interface WakeLockDeps {
  wakeLock: () => WakeLockLike | null | undefined;
  document?: Document | null;
}

const IDLE: WakeLockStatus = Object.freeze({ wanted: false, held: false, reason: null });

export class WakeLockKeeper {
  private wanted = false;
  private sentinel: WakeLockSentinelLike | null = null;
  private requesting: Promise<boolean> | null = null;
  private reason: WakeLockReason = null;
  private visibilityBound = false;
  private listeners = new Set<() => void>();
  private snapshot: WakeLockStatus = IDLE;

  constructor(private readonly deps: WakeLockDeps) {}

  /** Acquire now — the Start Run tap calls this once the run has started.
   * Resolves true when the browser holds the lock. Idempotent while held or
   * in flight. A hidden page does not ask; the return to it does. */
  request(): Promise<boolean> {
    this.wanted = true;
    this.bindVisibility();
    if (this.sentinel && !this.sentinel.released) return Promise.resolve(true);
    if (this.requesting) return this.requesting;
    const api = this.deps.wakeLock();
    if (!api || typeof api.request !== "function") {
      this.reason = "unsupported";
      this.emit();
      return Promise.resolve(false);
    }
    const doc = this.deps.document;
    if (doc && doc.visibilityState === "hidden") {
      this.emit();
      return Promise.resolve(false);
    }
    this.requesting = (async () => {
      try {
        const sentinel = await api.request("screen");
        if (!this.wanted) {
          // The run ended while the browser was answering.
          sentinel.release().catch(() => {});
          return false;
        }
        const onRelease = () => {
          if (this.sentinel !== sentinel) return;
          this.sentinel = null;
          this.emit();
        };
        if (typeof sentinel.addEventListener === "function") {
          sentinel.addEventListener("release", onRelease);
        } else {
          sentinel.onrelease = onRelease;
        }
        this.sentinel = sentinel;
        this.reason = null;
        this.emit();
        return true;
      } catch {
        // NotAllowedError and anything else the browser says: the hint.
        this.sentinel = null;
        this.reason = "denied";
        this.emit();
        return false;
      } finally {
        this.requesting = null;
      }
    })();
    return this.requesting;
  }

  /** The driver context's word, on every poll: a run in progress wants the
   * lock (asked for once, not on every poll after a refusal — the next
   * visibility change or tap asks again); none releases it. */
  setWanted(wanted: boolean): void {
    if (!wanted) {
      void this.release();
      return;
    }
    const first = !this.wanted;
    this.wanted = true;
    if (first || (!this.sentinel && !this.requesting && this.reason == null)) {
      void this.request();
    }
  }

  async release(): Promise<void> {
    this.wanted = false;
    this.reason = null;
    const sentinel = this.sentinel;
    this.sentinel = null;
    this.emit();
    if (sentinel) {
      try {
        await sentinel.release();
      } catch {
        // Already released by the browser.
      }
    }
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): WakeLockStatus => this.snapshot;

  // Internals ---------------------------------------------------------------

  private bindVisibility(): void {
    const doc = this.deps.document;
    if (!doc || this.visibilityBound) return;
    this.visibilityBound = true;
    doc.addEventListener("visibilitychange", () => {
      if (doc.visibilityState !== "visible" || !this.wanted) return;
      // The browser released it when the page went away; a fresh request
      // is the only way back, and a refusal is judged afresh too.
      this.reason = null;
      void this.request();
    });
  }

  private emit(): void {
    const next: WakeLockStatus = {
      wanted: this.wanted,
      held: this.sentinel != null && !this.sentinel.released,
      reason: this.reason,
    };
    if (
      next.wanted === this.snapshot.wanted
      && next.held === this.snapshot.held
      && next.reason === this.snapshot.reason
    ) {
      return;
    }
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }
}

/** The app's one keeper. */
export const wakeLock = new WakeLockKeeper({
  wakeLock: () => (typeof navigator !== "undefined" ? (navigator as any).wakeLock : null),
  document: typeof document !== "undefined" ? document : null,
});

/** Hold the lock while the context reports a run in progress; release when
 * it reports none. Mounted by the driver layout beside the fix watch. */
export function useWakeLockForRun(data: DriverContext | undefined): void {
  const run = data?.active_run;
  const runId: string | null = run && run.status !== "completed" ? run.id : null;
  const loaded = data != null;
  useEffect(() => {
    if (!loaded) return;
    wakeLock.setWanted(runId != null);
  }, [loaded, runId]);
}

export function useWakeLockStatus(): WakeLockStatus {
  return useSyncExternalStore(wakeLock.subscribe, wakeLock.getSnapshot, wakeLock.getSnapshot);
}

/** Whether the Run page should show the hint: the lock is wanted, not held,
 * and the browser has said why. */
export function showWakeLockHint(status: WakeLockStatus): boolean {
  return status.wanted && !status.held && status.reason != null;
}
