// The Phase 2 ping stream (GPS plan U14: R24, R26, R28; F6; AE13).
//
// While the driver context reports a run in progress, the fixes the run's
// watch delivers are batched and posted to the pings route on the school's
// ping interval: every interval while the bus is moving (the newest fix is
// MOVING_DISPLACEMENT_M or more from the last fix the server accepted, or
// nothing has been accepted yet), every third interval while it is not. A
// batch is the newest six fixes; whatever was gathered before them is left
// out, and nothing sent is ever re-sent — a failed batch is dropped and the
// next interval carries fresh fixes. A tick with no new fix sends nothing:
// the server's staleness is then the honest word on the bus.
//
// The stream stops the moment the context reports no run (End Run, an office
// close) and the moment the page is hidden — no browser tracks in the
// background, and R24 wants nothing sent from one — resuming on return. A
// `session-mismatch` refusal (this run was started from another sign-in)
// pauses it until the next tapped action from this app, which re-binds the
// run on the server; a `ping-too-soon` refusal skips one interval. Nothing
// is sent before Start Run: there is no run to send for.

// Since U15 an accepted batch's response can carry what the trail says
// (R29, R30): `prompts` — a bypassed-stop prompt raised the moment the bus
// left a stop with children unrecorded — and `arrival_offer`. Both go
// straight to the nudge queue from the response, so the card shows on this
// batch rather than one context poll later; the poll then owns them.

import { useEffect, useSyncExternalStore } from "react";
import {
  nudgeStore,
  type ArrivalOffer,
  type NudgePrompt,
} from "@/features/driver/components/nudgeStore";
import type { DriverContext } from "@/features/driver/driverHooks";
import { actionEnvelopes, getDeviceId } from "@/lib/actionEnvelope";
import { api, ApiError } from "@/lib/apiClient";
import { fixCapture, type Fix } from "@/lib/geo/fixCapture";

export const PINGS_PATH = "/api/runs/driver/pings";
/** The server's batch cap; a batch is the newest this many fixes. */
export const PING_MAX_BATCH = 6;
/** Until the driver context serves the school's interval (U11). */
export const DEFAULT_PING_INTERVAL_S = 10;
/** Displacement since the last accepted fix that counts as moving. */
export const MOVING_DISPLACEMENT_M = 30;
/** While stationary, a batch goes every this many intervals. */
export const STATIONARY_EVERY_N_INTERVALS = 3;
export const SESSION_MISMATCH_CODE = "session-mismatch";
export const PING_TOO_SOON_CODE = "ping-too-soon";

export interface PingBatch {
  run_id: string;
  fixes: Fix[];
  device_id: string;
}

export interface PingStreamStatus {
  /** The context reports a run in progress and the stream is armed. */
  active: boolean;
  /** Paused on `session-mismatch` until the next accepted tap. */
  suspended: boolean;
  /** Client clock when the server last accepted a batch. */
  lastAcceptedAt: number | null;
}

/** What an accepted batch's response may carry for the nudge queue (U15). */
export interface PingResponseNudges {
  prompts?: NudgePrompt[] | null;
  arrival_offer?: ArrivalOffer | null;
}

export interface RunPingsDeps {
  post: (body: PingBatch) => Promise<unknown>;
  /** An accepted batch's response body, with the run it was for — the
   * nudge queue reads `prompts` and `arrival_offer` off it (U15). Not
   * called for a refused batch, nor when the run changed under it. */
  onResponse?: (runId: string, body: unknown) => void;
  /** The run's fix source — `fixCapture.onPosition`. Returns the unsubscribe. */
  onPosition: (listener: (fix: Fix) => void) => () => void;
  /** The watch's fresh cached fix when the stream starts — the Start Run
   * tap's own fix arrived before the context confirmed the run — so the
   * first batch has something to carry even if the phone has not moved
   * since. Null when there is none or it is stale. */
  freshFix?: () => Fix | null;
  deviceId: () => string;
  now: () => number;
  /** For the visibility rule; null where there is no document. */
  document?: Document | null;
}

/** Great-circle metres between two fixes. */
export function distanceM(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const toRad = (degrees: number) => (degrees * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const h =
    Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * 6_371_000 * Math.asin(Math.sqrt(h));
}

/** The interval in ms from the context's value; the default when it is
 * missing or malformed. */
export function pingIntervalMs(value: unknown): number {
  const seconds =
    typeof value === "number" && Number.isFinite(value) && value > 0 ? value : DEFAULT_PING_INTERVAL_S;
  return Math.round(seconds * 1000);
}

const IDLE: PingStreamStatus = Object.freeze({ active: false, suspended: false, lastAcceptedAt: null });

export class RunPings {
  private runId: string | null = null;
  private intervalMs = DEFAULT_PING_INTERVAL_S * 1000;
  private buffer: Fix[] = [];
  private lastAccepted: Fix | null = null;
  private stationaryTicks = 0;
  private skipTicks = 0;
  private suspended = false;
  private inFlight = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private unsubscribePosition: (() => void) | null = null;
  private visibilityBound = false;
  private listeners = new Set<() => void>();
  private snapshot: PingStreamStatus = IDLE;

  constructor(private readonly deps: RunPingsDeps) {}

  /** The driver context's word, on every poll: the run in progress (or
   * none) and the school's ping interval. Idempotent for an unchanged run;
   * a new run starts the stream afresh, none stops it. */
  sync(runId: string | null, pingIntervalS?: unknown): void {
    const intervalMs = pingIntervalMs(pingIntervalS);
    if (runId !== this.runId) {
      this.stop();
      this.runId = runId;
      this.intervalMs = intervalMs;
      if (runId) this.start();
      this.emit();
      return;
    }
    if (runId && intervalMs !== this.intervalMs) {
      this.intervalMs = intervalMs;
      if (this.timer != null) {
        this.stopTimer();
        this.startTimer();
      }
    }
  }

  /** A tapped action of this app was accepted: the server has re-bound the
   * run to this session, so a stream paused on `session-mismatch` resumes. */
  noteTapSucceeded(): void {
    if (!this.suspended) return;
    this.suspended = false;
    this.emit();
  }

  /** Whether the interval timer is up (a run, and the page visible). */
  isTicking(): boolean {
    return this.timer != null;
  }

  pendingFixes(): number {
    return this.buffer.length;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): PingStreamStatus => this.snapshot;

  // Internals ---------------------------------------------------------------

  private start(): void {
    this.bindVisibility();
    if (!this.unsubscribePosition) {
      this.unsubscribePosition = this.deps.onPosition((fix) => this.remember(fix));
    }
    this.startTimer();
    // The first batch: what the watch already holds, when it is fresh.
    // Only once the timer is up — a hidden page seeds nothing.
    if (this.timer != null) {
      const fresh = this.deps.freshFix?.() ?? null;
      if (fresh) this.remember(fresh);
    }
  }

  private stop(): void {
    this.stopTimer();
    this.unsubscribePosition?.();
    this.unsubscribePosition = null;
    this.buffer = [];
    this.lastAccepted = null;
    this.stationaryTicks = 0;
    this.skipTicks = 0;
    this.suspended = false;
  }

  private startTimer(): void {
    if (this.timer != null) return;
    const doc = this.deps.document;
    if (doc && doc.visibilityState === "hidden") return;
    this.timer = setInterval(() => this.tick(), this.intervalMs);
  }

  private stopTimer(): void {
    if (this.timer == null) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  private bindVisibility(): void {
    const doc = this.deps.document;
    if (!doc || this.visibilityBound) return;
    this.visibilityBound = true;
    doc.addEventListener("visibilitychange", () => {
      if (!this.runId) return;
      if (doc.visibilityState === "hidden") {
        // Stopped at once: whatever the browser still delivers to a hidden
        // page is not sent, and the buffer does not carry over the gap.
        this.stopTimer();
        this.buffer = [];
      } else {
        this.startTimer();
      }
      this.emit();
    });
  }

  private remember(fix: Fix): void {
    if (!this.runId || this.timer == null) return;
    this.buffer.push(fix);
    if (this.buffer.length > PING_MAX_BATCH) {
      this.buffer.splice(0, this.buffer.length - PING_MAX_BATCH);
    }
  }

  private tick(): void {
    const runId = this.runId;
    if (!runId || this.suspended || this.inFlight) return;
    if (this.skipTicks > 0) {
      this.skipTicks -= 1;
      return;
    }
    if (this.buffer.length === 0) return;
    const newest = this.buffer[this.buffer.length - 1]!;
    const moving =
      this.lastAccepted == null || distanceM(this.lastAccepted, newest) >= MOVING_DISPLACEMENT_M;
    if (!moving) {
      this.stationaryTicks += 1;
      if (this.stationaryTicks < STATIONARY_EVERY_N_INTERVALS) return;
    }
    this.stationaryTicks = 0;
    const fixes = this.buffer.slice(-PING_MAX_BATCH);
    this.buffer = [];
    void this.send(runId, fixes);
  }

  private async send(runId: string, fixes: Fix[]): Promise<void> {
    this.inFlight = true;
    try {
      const body = await this.deps.post({ run_id: runId, fixes, device_id: this.deps.deviceId() });
      if (this.runId !== runId) return;
      this.lastAccepted = fixes[fixes.length - 1]!;
      this.emit({ lastAcceptedAt: this.deps.now() });
      this.deps.onResponse?.(runId, body);
    } catch (error) {
      if (this.runId !== runId) return;
      if (error instanceof ApiError && error.status === 409 && error.code === SESSION_MISMATCH_CODE) {
        this.suspended = true;
        this.emit();
      } else if (error instanceof ApiError && error.status === 429) {
        this.skipTicks = 1;
      }
      // Anything else — the network, a 5xx, a run that ended under the
      // batch — is dropped; the next interval sends fresh fixes.
    } finally {
      this.inFlight = false;
    }
  }

  private emit(patch: Partial<PingStreamStatus> = {}): void {
    const next: PingStreamStatus = {
      active: this.runId != null && this.timer != null,
      suspended: this.suspended,
      lastAcceptedAt: this.snapshot.lastAcceptedAt,
      ...patch,
    };
    if (
      next.active === this.snapshot.active
      && next.suspended === this.snapshot.suspended
      && next.lastAcceptedAt === this.snapshot.lastAcceptedAt
    ) {
      return;
    }
    this.snapshot = next;
    for (const listener of this.listeners) listener();
  }
}

/** Feed a batch response's nudges to the queue (U15): the prompts it raised
 * as a response delivery, and the server's current word on the arrival
 * offer — set whenever the body carries the key, null included, so an
 * offer the trail withdrew leaves the card at once. */
export function applyPingResponse(runId: string, body: unknown): void {
  if (!body || typeof body !== "object") return;
  const nudges = body as PingResponseNudges;
  nudgeStore.ingest(nudges.prompts ?? [], "response");
  if ("arrival_offer" in nudges) nudgeStore.setArrivalOffer(nudges.arrival_offer ?? null, runId);
}

/** The app's one stream. */
export const runPings = new RunPings({
  post: (body) => api.post(PINGS_PATH, body),
  onResponse: applyPingResponse,
  onPosition: (listener) => fixCapture.onPosition(listener),
  freshFix: () => fixCapture.freshFix(),
  deviceId: () => getDeviceId(),
  now: () => Date.now(),
  document: typeof document !== "undefined" ? document : null,
});

// Every accepted driver action re-binds the run to this session on the
// server (U7): the stream resumes on the next one after a mismatch.
actionEnvelopes.onSent(() => runPings.noteTapSucceeded());

/**
 * Keep the ping stream in step with the run (mounted by the driver layout,
 * so every driver tab runs it — the run is spent on the board as much as on
 * the Run page). Nothing happens until the context has answered once: an
 * unloaded context is not "no run".
 */
export function useRunPings(data: DriverContext | undefined): void {
  const run = data?.active_run;
  const runId: string | null = run && run.status !== "completed" ? run.id : null;
  const loaded = data != null;
  const interval = data?.config?.ping_interval_s;
  useEffect(() => {
    if (!loaded) return;
    runPings.sync(runId, interval);
  }, [loaded, runId, interval]);
}

/** The stream's state, for the Run page's hint. */
export function usePingStreamStatus(): PingStreamStatus {
  return useSyncExternalStore(runPings.subscribe, runPings.getSnapshot, runPings.getSnapshot);
}
