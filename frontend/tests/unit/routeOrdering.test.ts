// U11 — RoutesPage pure ordering pieces. Covers AE3 (R10/R11): the reorder
// payload is the FULL deduped group_key list in display order with one group
// shifted (gate/planner rows pinned, boundary moves are no-ops, sibling keys
// collapse to one slot), the mode chip follows the one-ordering-authority
// precedence (custom > manual > auto), Recalculate shows exactly when there is
// something to recompute or retry, the degraded badge renders purely from
// the persisted route flag, and the broadcast Send gate blocks exactly the
// two bodies the server 400s (empty-after-trim, past the 500 cap).
import { describe, expect, it } from "vitest";
import {
  BROADCAST_MAX_CHARS,
  DEGRADED_MESSAGE,
  broadcastSendDisabled,
  buildReorderPayload,
  routeModeLabel,
  showDegradedBadge,
  showRecalculate,
  type ReorderableStop,
} from "@/features/admin/RoutesPage";

const stop = (group_key: string | null): ReorderableStop => ({ group_key });

// Morning shape: student groups first, school gate last.
const MORNING = [stop("A"), stop("B"), stop("C"), stop(null)];
// Afternoon shape: gate first (routes start at the school).
const AFTERNOON = [stop(null), stop("A"), stop("B"), stop("C")];

describe("buildReorderPayload", () => {
  it("moves a group up and returns the full deduped key list", () => {
    expect(buildReorderPayload(MORNING, 1, 0)).toEqual(["B", "A", "C"]);
  });

  it("moves a group down", () => {
    expect(buildReorderPayload(MORNING, 0, 1)).toEqual(["B", "A", "C"]);
    expect(buildReorderPayload(MORNING, 0, 2)).toEqual(["B", "C", "A"]);
  });

  it("keeps the gate out of the payload on afternoon routes too", () => {
    expect(buildReorderPayload(AFTERNOON, 2, 3)).toEqual(["A", "C", "B"]);
  });

  it("refuses boundary moves: past the top of the list", () => {
    expect(buildReorderPayload(MORNING, 0, -1)).toBeNull();
  });

  it("refuses boundary moves: onto the school gate / past the end", () => {
    // Morning gate sits below the last student group…
    expect(buildReorderPayload(MORNING, 2, 3)).toBeNull();
    // …and out of bounds entirely.
    expect(buildReorderPayload(MORNING, 3, 4)).toBeNull();
    expect(buildReorderPayload(AFTERNOON, 3, 4)).toBeNull();
    // Afternoon gate sits above the first student group.
    expect(buildReorderPayload(AFTERNOON, 1, 0)).toBeNull();
  });

  it("never moves gate or planner (null-key) rows", () => {
    expect(buildReorderPayload(MORNING, 3, 2)).toBeNull();
    expect(buildReorderPayload(AFTERNOON, 0, 1)).toBeNull();
    // Planner-authored student-less rows are all null keys.
    expect(buildReorderPayload([stop(null), stop(null)], 0, 1)).toBeNull();
  });

  it("is a no-op when from equals to", () => {
    expect(buildReorderPayload(MORNING, 1, 1)).toBeNull();
  });

  it("dedups sibling rows that share a group key", () => {
    const withSiblings = [stop("A"), stop("A"), stop("B"), stop(null)];
    expect(buildReorderPayload(withSiblings, 2, 0)).toEqual(["B", "A"]);
    expect(buildReorderPayload(withSiblings, 0, 2)).toEqual(["B", "A"]);
  });

  it("treats a move between two rows of the same group as a no-op", () => {
    const withSiblings = [stop("A"), stop("A"), stop("B")];
    expect(buildReorderPayload(withSiblings, 0, 1)).toBeNull();
  });
});

describe("routeModeLabel", () => {
  it("follows the ordering-authority precedence: custom > manual > auto", () => {
    expect(routeModeLabel({})).toBe("Auto");
    expect(routeModeLabel({ manual_stop_order: false })).toBe("Auto");
    expect(routeModeLabel({ manual_stop_order: true })).toBe("Manual order");
    expect(routeModeLabel({ custom_stops: true })).toBe("Planner");
    // The 008 CHECK forbids custom ∧ manual; precedence still documents it.
    expect(routeModeLabel({ custom_stops: true, manual_stop_order: true })).toBe("Planner");
  });
});

describe("showRecalculate", () => {
  it("hides on a healthy auto route", () => {
    expect(showRecalculate({})).toBe(false);
    expect(showRecalculate({ manual_stop_order: false, last_recalc_degraded: false })).toBe(false);
  });

  it("shows for manual order, degraded, and both at once", () => {
    expect(showRecalculate({ manual_stop_order: true })).toBe(true);
    expect(showRecalculate({ last_recalc_degraded: true })).toBe(true);
    expect(showRecalculate({ manual_stop_order: true, last_recalc_degraded: true })).toBe(true);
  });

  it("never shows on planner routes (server 409s)", () => {
    expect(showRecalculate({ custom_stops: true })).toBe(false);
    expect(showRecalculate({ custom_stops: true, last_recalc_degraded: true })).toBe(false);
  });
});

describe("degraded badge", () => {
  it("renders purely from the persisted last_recalc_degraded flag", () => {
    expect(showDegradedBadge({})).toBe(false);
    expect(showDegradedBadge({ last_recalc_degraded: false })).toBe(false);
    expect(showDegradedBadge({ last_recalc_degraded: true })).toBe(true);
    // Manual reorder does not clear the badge; only a google-success recalc does.
    expect(showDegradedBadge({ manual_stop_order: true, last_recalc_degraded: true })).toBe(true);
  });

  it("pins the R10 message and the R23 cap the dialog enforces", () => {
    expect(DEGRADED_MESSAGE).toBe("Order/times not recalculated — check addresses/maps key");
    expect(BROADCAST_MAX_CHARS).toBe(500);
  });
});

describe("broadcastSendDisabled", () => {
  it("blocks empty and whitespace-only bodies", () => {
    expect(broadcastSendDisabled("")).toBe(true);
    expect(broadcastSendDisabled("   ")).toBe(true);
    expect(broadcastSendDisabled("\n\t ")).toBe(true);
    expect(broadcastSendDisabled("hi")).toBe(false);
  });

  it("enforces the 500-char cap at the exact boundary", () => {
    expect(broadcastSendDisabled("a".repeat(500))).toBe(false);
    expect(broadcastSendDisabled("a".repeat(501))).toBe(true);
  });

  it("counts raw length like the counter, but trims only for emptiness", () => {
    // 499 chars + a trailing space = 500 raw → allowed (server trims later);
    // 500 chars + a space = 501 raw → blocked, matching the visible counter.
    expect(broadcastSendDisabled("a".repeat(499) + " ")).toBe(false);
    expect(broadcastSendDisabled("a".repeat(500) + " ")).toBe(true);
  });
});
