// The driver's prompt queue (GPS plan U3: R13, R23, R34).
//
// Prompts are server-owned: a pending `run_exception_events` row, delivered
// both in the action response that raised it (`prompts` on Arrive today) and in
// every driver-context poll (`pending_prompts`). This store is the client's
// one view over both feeds, keyed by event id, so a reload, a reconnect or a
// second device converge on the same card — and never two cards. It lives at
// module level rather than in a component: the four driver pages are separate
// routes, and a queue that died with the page would drop a response-fed prompt
// on every tab change until the next poll re-delivered it.
//
// Rules:
// - one card at a time, safety prompts first, then by creation time;
// - the context poll is the source of truth for removal (an answer from
//   another device, an Arrive / End Run auto-resolution, a resolution recorded
//   through the board page) — except that an entry fed by an action response
//   is protected for a grace period, so a poll that was already in flight when
//   the tap landed cannot blink it away before the invalidation refetch;
// - an answered or dismissed id is a tombstone: a stale poll cannot resurrect
//   it, and the server's 409 conflicts settle it the same way (R23: both
//   "already answered" and "resolved" mean remove the card and refresh);
// - only kinds this client can render enter the queue (the bypassed stop, the
//   custody confirm and the remote-absent attestation);
// - the arrival offer (GPS plan U15/R29) is the one client-side kind: not a
//   server prompt but a derived fact — `arrival_offer` on the context poll and
//   on every ping response — so it is set whole, never reconciled against the
//   prompt list, ranks below every real prompt, and its dismiss is remembered
//   here, per stop, only until the server stops offering that stop.

import { useSyncExternalStore } from "react";
import { ApiError } from "@/lib/apiClient";

export interface PromptStudent {
  id: string;
  name: string;
}

/** `_prompt_payload` in backend/app/dao/exception_dao.py, verbatim. */
export interface NudgePrompt {
  event_id: string;
  exception_id: string;
  kind: string;
  stop_order: number | null;
  stop_name: string | null;
  student_id: string | null;
  students: PromptStudent[];
  answers: string[];
  /** The tap's distance from the stop, for the custody copy (U9); null for a bypassed stop. */
  distance_m?: number | null;
  created_at?: string | null;
  delivered_at?: string | null;
  shown_at?: string | null;
}

export type PromptSource = "response" | "context";

/** Mirrors PROMPT_PRIORITY in exception_dao.py: the two safety prompts before
 * the routine custody confirm; unknown kinds last. */
export const PROMPT_PRIORITY: Record<string, number> = {
  "stop-bypassed": 0,
  "absent-remote": 0,
  "custody-away": 1,
};

export const RENDERABLE_KINDS: ReadonlySet<string> = new Set([
  "stop-bypassed",
  "absent-remote",
  "custody-away",
]);

/** How long a response-fed entry survives a poll that does not list it. Two
 * polls at POLL_LIVE (5 s) comfortably outlast any request that was already
 * in flight when the tap landed. */
export const RESPONSE_GRACE_MS = 12_000;

export const PROMPT_CONFLICT_CODES = new Set(["prompt-already-answered", "prompt-resolved"]);

/** The arrival offer (GPS plan U15/R29): `arrival_offer` on the driver
 * context and on a ping response — the not-yet-arrived stop the trail puts
 * the bus at. Not an exception, not an event: the answer is an Arrive to
 * that stop. */
export interface ArrivalOffer {
  stop_order: number;
  stop_name: string | null;
}

/** The client-side kind the offer takes in the queue. Never sent by the
 * server as a prompt; the store mints it from `arrival_offer`. */
export const ARRIVAL_OFFER_KIND = "arrival-offer";
/** Below every server prompt, including kinds this client does not know. */
export const ARRIVAL_OFFER_PRIORITY = 3;
/** The offer's one answer, for the card. */
export const ARRIVAL_OFFER_ANSWER = "arrive";

export function arrivalOfferEventId(runId: string, stopOrder: number): string {
  return `${ARRIVAL_OFFER_KIND}:${runId}:${stopOrder}`;
}

/** The offer in the queue's own shape, keyed per (run, stop). */
export function arrivalOfferPrompt(runId: string, offer: ArrivalOffer): NudgePrompt {
  return {
    event_id: arrivalOfferEventId(runId, offer.stop_order),
    exception_id: "",
    kind: ARRIVAL_OFFER_KIND,
    stop_order: offer.stop_order,
    stop_name: offer.stop_name ?? null,
    student_id: null,
    students: [],
    answers: [ARRIVAL_OFFER_ANSWER],
    distance_m: null,
    created_at: null,
    delivered_at: null,
    shown_at: null,
  };
}

export function priorityOf(kind: string): number {
  if (kind === ARRIVAL_OFFER_KIND) return ARRIVAL_OFFER_PRIORITY;
  return PROMPT_PRIORITY[kind] ?? 2;
}

/** Priority, then creation time (oldest first), then id — the server's order. */
export function comparePrompts(a: NudgePrompt, b: NudgePrompt): number {
  const byPriority = priorityOf(a.kind) - priorityOf(b.kind);
  if (byPriority !== 0) return byPriority;
  const at = a.created_at ?? "";
  const bt = b.created_at ?? "";
  if (at !== bt) {
    // A missing timestamp sorts after a known one.
    if (!at) return 1;
    if (!bt) return -1;
    return at < bt ? -1 : 1;
  }
  return a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0;
}

/** The two respond conflicts the client treats as "remove card, refresh". */
export function isPromptConflict(error: unknown): boolean {
  return (
    error instanceof ApiError
    && error.status === 409
    && error.code != null
    && PROMPT_CONFLICT_CODES.has(error.code)
  );
}

interface Entry {
  prompt: NudgePrompt;
  source: PromptSource;
  addedAt: number;
}

export class NudgeStore {
  private entries = new Map<string, Entry>();
  private settled = new Set<string>();
  private shown = new Set<string>();
  /** The current offer's id, and the offer ids hidden by a local dismiss or
   * an answered Arrive — kept only while the server keeps offering them. */
  private offerId: string | null = null;
  private hiddenOffers = new Set<string>();
  private listeners = new Set<() => void>();
  private headSnapshot: NudgePrompt | null = null;

  constructor(private readonly renderable: ReadonlySet<string> = RENDERABLE_KINDS) {}

  /** Merge a delivery. `context` deliveries also reconcile: entries the poll
   * no longer lists are removed, unless they are response-fed and younger
   * than the grace period. The arrival offer is not a listed prompt and is
   * never reconciled here — `setArrivalOffer` owns it. */
  ingest(prompts: NudgePrompt[] | null | undefined, source: PromptSource, now = Date.now()): void {
    const listed = new Set<string>();
    for (const prompt of prompts ?? []) {
      if (!prompt || !prompt.event_id) continue;
      listed.add(prompt.event_id);
      if (this.settled.has(prompt.event_id)) continue;
      if (!this.renderable.has(prompt.kind)) continue;
      const existing = this.entries.get(prompt.event_id);
      if (existing) {
        // Keep the reference when nothing changed so subscribers do not
        // re-render on every poll; refresh the copy inputs when they did.
        if (!samePrompt(existing.prompt, prompt)) existing.prompt = prompt;
        // A poll confirming a response-fed entry ends its grace period.
        if (source === "context") existing.source = "context";
        continue;
      }
      this.entries.set(prompt.event_id, { prompt, source, addedAt: now });
    }
    if (source === "context") {
      for (const [id, entry] of this.entries) {
        if (listed.has(id)) continue;
        if (entry.prompt.kind === ARRIVAL_OFFER_KIND) continue;
        if (entry.source === "response" && now - entry.addedAt < RESPONSE_GRACE_MS) continue;
        this.entries.delete(id);
      }
    }
    this.recompute();
  }

  /** The server's current word on the arrival offer (GPS plan U15/R29),
   * from the context poll or a ping response: the offered stop, or none.
   * Set whole: an offer for another stop replaces the card; none removes
   * it. A stop hidden by `hideArrivalOffer` stays hidden while the server
   * keeps offering exactly that stop, and is forgotten the moment the offer
   * changes — so the same stop offered again later (the bus came back)
   * shows again. */
  setArrivalOffer(offer: ArrivalOffer | null | undefined, runId: string | null, now = Date.now()): void {
    const id = offer && runId != null ? arrivalOfferEventId(runId, offer.stop_order) : null;
    for (const hidden of this.hiddenOffers) {
      if (hidden !== id) this.hiddenOffers.delete(hidden);
    }
    if (this.offerId && this.offerId !== id) this.entries.delete(this.offerId);
    this.offerId = id;
    if (id && offer && !this.hiddenOffers.has(id)) {
      const prompt = arrivalOfferPrompt(runId!, offer);
      const existing = this.entries.get(id);
      if (existing) {
        if (!samePrompt(existing.prompt, prompt)) existing.prompt = prompt;
      } else {
        this.entries.set(id, { prompt, source: "context", addedAt: now });
      }
    }
    this.recompute();
  }

  /** The offer's card was dismissed, or its Arrive was posted: hide it
   * until the server's offer changes. Not a tombstone. */
  hideArrivalOffer(eventId: string): void {
    this.hiddenOffers.add(eventId);
    if (this.entries.delete(eventId)) this.recompute();
  }

  /** Answered, dismissed, or conflicted on the server: gone for good. */
  settle(eventId: string): void {
    this.settled.add(eventId);
    if (this.entries.delete(eventId)) this.recompute();
  }

  /** True the first time this session shows the id — the cue and the shown-at
   * acknowledgement key on it. Survives clear(). */
  markShown(eventId: string): boolean {
    if (this.shown.has(eventId)) return false;
    this.shown.add(eventId);
    return true;
  }

  /** No open run: nothing to ask, and no offer to remember. Tombstones and
   * shown ids are kept. */
  clear(): void {
    this.offerId = null;
    this.hiddenOffers.clear();
    if (this.entries.size === 0) return;
    this.entries.clear();
    this.recompute();
  }

  head(): NudgePrompt | null {
    return this.headSnapshot;
  }

  all(): NudgePrompt[] {
    return [...this.entries.values()].map((e) => e.prompt).sort(comparePrompts);
  }

  size(): number {
    return this.entries.size;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): NudgePrompt | null => this.headSnapshot;

  private recompute(): void {
    const next = this.all()[0] ?? null;
    if (next === this.headSnapshot) return;
    this.headSnapshot = next;
    for (const listener of this.listeners) listener();
  }
}

function samePrompt(a: NudgePrompt, b: NudgePrompt): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The app's one queue. */
export const nudgeStore = new NudgeStore();

/** The card to show right now, or null. */
export function useNudgeHead(): NudgePrompt | null {
  return useSyncExternalStore(nudgeStore.subscribe, nudgeStore.getSnapshot, nudgeStore.getSnapshot);
}
