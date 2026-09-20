// GPS plan U3 — the driver prompt queue and the attention cue, pure pieces.
// R13: one prompt at a time, safety prompts first; R23/R34: the two server
// conflicts settle a card the same way; the cue is keyed by kind (F3/F4 loud,
// the custody confirm silent).
import { describe, expect, it, vi } from "vitest";
import {
  aboutDistance,
  bypassedStopCopy,
  custodyCopy,
} from "@/features/driver/components/NudgeQueue";
import {
  NudgeStore,
  PROMPT_PRIORITY,
  RENDERABLE_KINDS,
  RESPONSE_GRACE_MS,
  comparePrompts,
  isPromptConflict,
  priorityOf,
  type NudgePrompt,
} from "@/features/driver/components/nudgeStore";
import {
  CUE_POLICY,
  VIBRATION_PATTERN,
  cuePolicy,
  toneWav,
} from "@/features/driver/components/useAttentionCue";
import { ApiError } from "@/lib/apiClient";

function prompt(over: Partial<NudgePrompt> & { event_id: string }): NudgePrompt {
  return {
    exception_id: `x-${over.event_id}`,
    kind: "stop-bypassed",
    stop_order: 4,
    stop_name: "Kimathi Corner",
    student_id: null,
    students: [{ id: "s1", name: "Brian" }],
    answers: ["dismissed"],
    created_at: "2026-09-20T06:30:00+00:00",
    delivered_at: null,
    shown_at: null,
    ...over,
  };
}

const ALL_KINDS = new Set(["stop-bypassed", "absent-remote", "custody-away", "unknown-kind"]);

describe("ordering", () => {
  it("ranks the two safety kinds before the custody confirm and unknown kinds last", () => {
    expect(PROMPT_PRIORITY).toEqual({ "stop-bypassed": 0, "absent-remote": 0, "custody-away": 1 });
    expect(priorityOf("stop-bypassed")).toBe(0);
    expect(priorityOf("absent-remote")).toBe(0);
    expect(priorityOf("custody-away")).toBe(1);
    expect(priorityOf("something-new")).toBe(2);
  });

  it("sorts by priority, then creation time, then id", () => {
    const custodyOld = prompt({ event_id: "c", kind: "custody-away", created_at: "2026-09-20T06:00:00Z" });
    const bypassedLater = prompt({ event_id: "b", created_at: "2026-09-20T06:20:00Z" });
    const absentEarlier = prompt({ event_id: "a", kind: "absent-remote", created_at: "2026-09-20T06:10:00Z" });
    const tieA = prompt({ event_id: "t1", created_at: "2026-09-20T06:20:00Z" });
    const undated = prompt({ event_id: "u", created_at: null });
    const sorted = [custodyOld, undated, bypassedLater, tieA, absentEarlier].sort(comparePrompts);
    expect(sorted.map((p) => p.event_id)).toEqual(["a", "b", "t1", "u", "c"]);
  });
});

describe("NudgeStore", () => {
  it("keys by event id across both feeds and shows one card, the safety prompt first", () => {
    const store = new NudgeStore(ALL_KINDS);
    const custody = prompt({ event_id: "c", kind: "custody-away", created_at: "2026-09-20T06:00:00Z" });
    const bypassed = prompt({ event_id: "b", created_at: "2026-09-20T06:20:00Z" });
    store.ingest([custody, bypassed], "response");
    store.ingest([custody, bypassed], "context");
    expect(store.size()).toBe(2);
    expect(store.head()?.event_id).toBe("b");
    expect(store.all().map((p) => p.event_id)).toEqual(["b", "c"]);
  });

  it("settles an answered id for good: a stale poll cannot bring it back", () => {
    const store = new NudgeStore();
    const first = prompt({ event_id: "1", created_at: "2026-09-20T06:00:00Z" });
    const second = prompt({ event_id: "2", created_at: "2026-09-20T06:05:00Z" });
    store.ingest([first, second], "context");
    expect(store.head()?.event_id).toBe("1");
    store.settle("1");
    expect(store.head()?.event_id).toBe("2");
    store.ingest([first, second], "context");
    expect(store.all().map((p) => p.event_id)).toEqual(["2"]);
  });

  it("removes what the poll no longer lists, except a fresh response-fed entry", () => {
    const store = new NudgeStore();
    const polled = prompt({ event_id: "p" });
    const fresh = prompt({ event_id: "r", created_at: "2026-09-20T06:40:00Z" });
    store.ingest([polled], "context", 1_000);
    store.ingest([fresh], "response", 2_000);
    // A poll that was in flight when the tap landed lists neither the fresh
    // prompt nor, say, the one another device just answered.
    store.ingest([], "context", 3_000);
    expect(store.all().map((p) => p.event_id)).toEqual(["r"]);
    // Past the grace period an absent poll is believed.
    store.ingest([], "context", 2_000 + RESPONSE_GRACE_MS);
    expect(store.size()).toBe(0);
  });

  it("ends a response-fed entry's grace once a poll has confirmed it", () => {
    const store = new NudgeStore();
    const fresh = prompt({ event_id: "r" });
    store.ingest([fresh], "response", 1_000);
    store.ingest([fresh], "context", 1_500);
    store.ingest([], "context", 2_000);
    expect(store.size()).toBe(0);
  });

  it("refreshes copy inputs in place and keeps the reference when nothing changed", () => {
    const store = new NudgeStore();
    const two = prompt({ event_id: "e", students: [{ id: "d", name: "D" }, { id: "e", name: "E" }] });
    store.ingest([two], "response");
    const before = store.head();
    store.ingest([prompt({ event_id: "e", students: [{ id: "d", name: "D" }, { id: "e", name: "E" }] })], "context");
    expect(store.head()).toBe(before);
    store.ingest([prompt({ event_id: "e", students: [{ id: "e", name: "E" }] })], "context");
    expect(store.head()).not.toBe(before);
    expect(store.head()?.students.map((s) => s.id)).toEqual(["e"]);
  });

  it("admits only kinds this client renders", () => {
    const store = new NudgeStore();
    store.ingest(
      [prompt({ event_id: "a", kind: "absent-remote" }), prompt({ event_id: "b" })],
      "context",
    );
    expect(store.all().map((p) => p.event_id)).toEqual(["b"]);
    const wider = new NudgeStore(ALL_KINDS);
    wider.ingest([prompt({ event_id: "a", kind: "absent-remote" })], "context");
    expect(wider.size()).toBe(1);
  });

  it("marks a prompt shown once, surviving a clear, and clears entries without tombstoning them", () => {
    const store = new NudgeStore();
    store.ingest([prompt({ event_id: "s" })], "context");
    expect(store.markShown("s")).toBe(true);
    expect(store.markShown("s")).toBe(false);
    store.clear();
    expect(store.head()).toBeNull();
    expect(store.markShown("s")).toBe(false);
    // No run, then the same run again: the prompt is not settled, it comes back.
    store.ingest([prompt({ event_id: "s" })], "context");
    expect(store.head()?.event_id).toBe("s");
  });

  it("notifies subscribers only when the card to show changes", () => {
    const store = new NudgeStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const a = prompt({ event_id: "a", created_at: "2026-09-20T06:00:00Z" });
    const b = prompt({ event_id: "b", created_at: "2026-09-20T06:01:00Z" });
    store.ingest([a], "context");
    expect(listener).toHaveBeenCalledTimes(1);
    store.ingest([a, b], "context"); // head unchanged
    expect(listener).toHaveBeenCalledTimes(1);
    store.settle("a");
    expect(listener).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot()?.event_id).toBe("b");
  });

  it("ignores malformed deliveries", () => {
    const store = new NudgeStore();
    store.ingest(undefined, "context");
    store.ingest(null, "response");
    store.ingest([{} as NudgePrompt], "context");
    expect(store.size()).toBe(0);
  });
});

describe("isPromptConflict", () => {
  it("is true for the two respond conflicts only", () => {
    expect(isPromptConflict(new ApiError("answered", 409, "prompt-already-answered"))).toBe(true);
    expect(isPromptConflict(new ApiError("resolved", 409, "prompt-resolved"))).toBe(true);
    expect(isPromptConflict(new ApiError("other", 409, "idempotency-mismatch"))).toBe(false);
    expect(isPromptConflict(new ApiError("bare", 409))).toBe(false);
    expect(isPromptConflict(new ApiError("bad", 400, "prompt-resolved"))).toBe(false);
    expect(isPromptConflict(new Error("network"))).toBe(false);
    expect(isPromptConflict(undefined)).toBe(false);
  });
});

describe("attention cue policy", () => {
  it("sounds and vibrates for the two safety prompts and stays silent for the custody confirm", () => {
    expect(CUE_POLICY["stop-bypassed"]).toEqual({ tone: true, vibrate: true });
    expect(CUE_POLICY["absent-remote"]).toEqual({ tone: true, vibrate: true });
    expect(CUE_POLICY["custody-away"]).toEqual({ tone: false, vibrate: false });
    expect(cuePolicy("custody-away")).toEqual({ tone: false, vibrate: false });
    expect(cuePolicy("never-heard-of")).toEqual({ tone: false, vibrate: false });
    expect(VIBRATION_PATTERN).toEqual([200, 100, 200]);
  });

  it("builds a valid 16-bit mono PCM WAV for the tone", () => {
    const bytes = toneWav({ frequency: 880, durationMs: 100, sampleRate: 8_000 });
    const text = (from: number, to: number) => String.fromCharCode(...bytes.subarray(from, to));
    expect(text(0, 4)).toBe("RIFF");
    expect(text(8, 12)).toBe("WAVE");
    expect(text(12, 16)).toBe("fmt ");
    expect(text(36, 40)).toBe("data");
    const view = new DataView(bytes.buffer);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(8_000);
    expect(view.getUint16(34, true)).toBe(16);
    const samples = 800;
    expect(view.getUint32(40, true)).toBe(samples * 2);
    expect(bytes.length).toBe(44 + samples * 2);
    // Fades in from silence: the first sample is zero; the middle of the tone
    // is not (a window of samples, since any single one may sit on a zero
    // crossing).
    expect(view.getInt16(44, true)).toBe(0);
    const middle = Array.from({ length: 8 }, (_, i) =>
      Math.abs(view.getInt16(44 + (samples / 2 + i) * 2, true)),
    );
    expect(Math.max(...middle)).toBeGreaterThan(10_000);
  });
});

describe("bypassedStopCopy", () => {
  it("names the stop and the children, one or several, in the run's own words", () => {
    const one = bypassedStopCopy(prompt({ event_id: "1" }), false);
    expect(one.title).toBe("Stop 4: Kimathi Corner");
    expect(one.body).toBe("Brian has no record. Mark boarded or absent?");
    expect(one.outcomeLabel).toBe("Boarded");

    const two = bypassedStopCopy(
      prompt({ event_id: "2", students: [{ id: "b", name: "Brian" }, { id: "a", name: "Amina" }] }),
      true,
    );
    expect(two.body).toBe("Brian and Amina have no record. Mark dropped off or absent?");
    expect(two.outcomeLabel).toBe("Dropped off");

    const three = bypassedStopCopy(
      prompt({
        event_id: "3",
        stop_name: null,
        students: [{ id: "b", name: "Brian" }, { id: "a", name: "Amina" }, { id: "k", name: "Kevin" }],
      }),
      false,
    );
    expect(three.title).toBe("Stop 4");
    expect(three.body).toBe("Brian, Amina and Kevin have no record. Mark boarded or absent?");
  });
});

// --- the custody confirm (GPS plan U9: R14, F2) ---------------------------------

describe("custodyCopy", () => {
  const far = prompt({
    event_id: "c1",
    kind: "custody-away",
    stop_order: 1,
    stop_name: "Kilimani",
    student_id: "s1",
    students: [{ id: "s1", name: "Wanjiru" }],
    answers: ["confirmed"],
    distance_m: 1800,
  });

  it("names the child, the outcome tapped and the distance, and offers confirm or undo", () => {
    expect(custodyCopy(far, false)).toEqual({
      title: "Stop 1: Kilimani",
      body: "You marked Wanjiru boarded about 1.8 km from their stop. Confirm, or undo?",
    });
    expect(custodyCopy(far, true).body).toBe(
      "You marked Wanjiru dropped off about 1.8 km from their stop. Confirm, or undo?",
    );
  });

  it("rounds the distance to what a driver can picture", () => {
    expect(aboutDistance(1800)).toBe("1.8 km");
    expect(aboutDistance(1849)).toBe("1.8 km");
    expect(aboutDistance(999)).toBe("1000 m");
    expect(aboutDistance(263)).toBe("260 m");
    expect(aboutDistance(4)).toBe("10 m");
    expect(aboutDistance(null)).toBe("some way");
    expect(aboutDistance(Number.NaN)).toBe("some way");
  });

  it("copes with a prompt missing its stop name or child", () => {
    const bare = custodyCopy(prompt({ ...far, stop_name: null, students: [] }), false);
    expect(bare.title).toBe("Stop 1");
    expect(bare.body).toContain("You marked this child boarded");
  });

  it("renders in the queue: the custody kind is renderable, after a safety prompt", () => {
    expect([...RENDERABLE_KINDS].sort()).toEqual(["custody-away", "stop-bypassed"]);
    const store = new NudgeStore();
    const bypassed = prompt({ event_id: "b1", created_at: "2026-09-20T06:31:00+00:00" });
    store.ingest([far, bypassed], "context");
    expect(store.size()).toBe(2);
    expect(store.head()?.event_id).toBe("b1");
    store.settle("b1");
    expect(store.head()?.event_id).toBe("c1");
  });
});
