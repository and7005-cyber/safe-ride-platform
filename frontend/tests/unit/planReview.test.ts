// U9 — PlanReviewPage pure decision logic (lib/planReview). Covers the plan's
// unit scenarios: the R22 gate-list builder groups basis-drift rows by kind
// (for apply's basis list AND restore's server-computed previous.drift rows,
// each with its own labels); diff shaping marks ≥5-minute moves ONLY; the R23
// acknowledgment payload includes every unplaceable (student, leg); the
// "Discard and re-draft" escape appears only above the documented 10-row
// threshold; the first-apply copy predicate fires exactly when every diff row
// is a first communication; and the idempotent already-applied receipt copy
// carries no counts.
import { describe, expect, it } from "vitest";
import {
  ACTIVE_RUN_WARNING,
  ALREADY_APPLIED_DESCRIPTION,
  ALREADY_APPLIED_TITLE,
  DIFF_BUS_CHANGE,
  DIFF_FIRST_COMMUNICATION,
  DIFF_LEG_REMOVED,
  DIFF_NEWLY_UNPLACEABLE,
  DIFF_PLACE_CHANGE,
  DIFF_TIME_MOVE,
  FIRST_APPLY_MESSAGE,
  GATE_ESCAPE_THRESHOLD,
  GATE_KIND_LABEL,
  NOTIFY_MOVE_THRESHOLD_MIN,
  RESTORE_CONFIRM_MESSAGE,
  RESTORE_GATE_KIND_LABEL,
  ackKey,
  buildAcknowledgmentPayload,
  buildGateList,
  flattenUnplaceable,
  groupGateRows,
  hhmmToMinutes,
  isFirstApplyDiff,
  movedOrder,
  moveMinutes,
  shapeDiff,
  showDiscardEscape,
  type PlanDiffRow,
} from "@/lib/planReview";

const basis = (id: string, name: string, lat: number | null = 1, lng: number | null = 36) => ({
  id,
  name,
  lat,
  lng,
});
const live = (id: string, name: string, lat: number | null = 1, lng: number | null = 36) => ({
  id,
  name,
  home_lat: lat,
  home_lng: lng,
});

describe("buildGateList (R22 basis drift)", () => {
  it("returns no rows when nothing changed", () => {
    expect(
      buildGateList([basis("a", "Amina")], [live("a", "Amina")]),
    ).toEqual([]);
  });

  it("classifies enrolled, address-changed and departed", () => {
    const rows = buildGateList(
      [basis("a", "Amina"), basis("b", "Brian", 1.5, 36.5)],
      [live("a", "Amina"), live("b", "Brian", 1.6, 36.5), live("c", "Cynthia")],
    );
    expect(rows).toEqual([
      { kind: "enrolled", student_id: "c", name: "Cynthia" },
      { kind: "address-changed", student_id: "b", name: "Brian" },
    ]);
    const departed = buildGateList([basis("d", "David")], []);
    expect(departed).toEqual([{ kind: "departed", student_id: "d", name: "David" }]);
  });

  it("ignores float round-trip noise on coordinates (the server's epsilon)", () => {
    expect(
      buildGateList([basis("a", "Amina", 1.123456789, 36)], [live("a", "Amina", 1.12345679, 36)]),
    ).toEqual([]);
  });

  it("treats a coordinate appearing or disappearing as an address change", () => {
    expect(
      buildGateList([basis("a", "Amina", null, null)], [live("a", "Amina", 1, 36)]),
    ).toEqual([{ kind: "address-changed", student_id: "a", name: "Amina" }]);
  });

  it("orders rows kind-first (enrolled, address-changed, departed) then by name", () => {
    const rows = buildGateList(
      [basis("d", "Zed"), basis("b", "Brian", 5, 5)],
      [live("b", "Brian", 6, 6), live("z", "Aaron"), live("y", "Yusuf")],
    );
    expect(rows.map((r) => `${r.kind}:${r.name}`)).toEqual([
      "enrolled:Aaron",
      "enrolled:Yusuf",
      "address-changed:Brian",
      "departed:Zed",
    ]);
  });
});

describe("groupGateRows", () => {
  it("groups rows by kind in fixed order, dropping empty groups", () => {
    const rows = buildGateList(
      [basis("d", "David")],
      [live("e", "Elena"), live("f", "Farah")],
    );
    const groups = groupGateRows(rows);
    expect(groups.map((g) => g.kind)).toEqual(["enrolled", "departed"]);
    expect(groups[0].rows.map((r) => r.name)).toEqual(["Elena", "Farah"]);
    expect(groups[1].rows.map((r) => r.name)).toEqual(["David"]);
    expect(groups[0].label).toContain("Enrolled");
  });

  it("returns nothing for an empty list", () => {
    expect(groupGateRows([])).toEqual([]);
  });

  it("accepts the server's restore drift rows with restore-flavoured labels", () => {
    // GET /current's previous.drift rows share the gate-kind vocabulary and
    // {kind, student_id, name} shape, arriving server-sorted by kind
    // alphabetically — grouping still lands in the fixed dialog order.
    const drift = [
      { kind: "departed" as const, student_id: "d1", name: "David" },
      { kind: "enrolled" as const, student_id: "e1", name: "Elena" },
      { kind: "enrolled" as const, student_id: "e2", name: "Farah" },
    ];
    const groups = groupGateRows(drift, RESTORE_GATE_KIND_LABEL);
    expect(groups.map((g) => g.kind)).toEqual(["enrolled", "departed"]);
    expect(groups[0].rows.map((r) => r.name)).toEqual(["Elena", "Farah"]);
    expect(groups[0].label).toBe(RESTORE_GATE_KIND_LABEL.enrolled);
    expect(groups[1].label).toBe(RESTORE_GATE_KIND_LABEL.departed);
    // The restore labels speak of the preserved plan, not the draft.
    expect(groups[0].label).toContain("preserved");
    expect(groups[0].label).not.toContain("draft");
  });

  it("defaults to the draft-flavoured apply labels", () => {
    const rows = buildGateList([], [live("e", "Elena")]);
    expect(groupGateRows(rows)[0].label).toBe(GATE_KIND_LABEL.enrolled);
  });
});

describe("showDiscardEscape (the documented 10-row threshold)", () => {
  it("appears only strictly above 10 rows", () => {
    expect(GATE_ESCAPE_THRESHOLD).toBe(10);
    expect(showDiscardEscape(0)).toBe(false);
    expect(showDiscardEscape(9)).toBe(false);
    expect(showDiscardEscape(10)).toBe(false);
    expect(showDiscardEscape(11)).toBe(true);
    expect(showDiscardEscape(40)).toBe(true);
  });
});

const diffRow = (over: Partial<PlanDiffRow>): PlanDiffRow => ({
  student_id: "s1",
  student_name: "Amina",
  leg: "morning",
  categories: [],
  current: null,
  baseline: null,
  ...over,
});

describe("shapeDiff (R24)", () => {
  it("marks ≥5-minute moves only", () => {
    expect(NOTIFY_MOVE_THRESHOLD_MIN).toBe(5);
    const moved6 = diffRow({
      categories: [DIFF_TIME_MOVE],
      baseline: { scheduled_time: "06:40" },
      current: { scheduled_time: "06:46" },
    });
    const moved5 = diffRow({
      student_id: "s2",
      categories: [DIFF_TIME_MOVE],
      baseline: { scheduled_time: "06:40" },
      current: { scheduled_time: "06:45" },
    });
    // A sub-threshold drift never renders as a move, whatever the category
    // list claims (defensive client re-derivation from the row's own times).
    const moved3 = diffRow({
      student_id: "s3",
      categories: [DIFF_TIME_MOVE],
      baseline: { scheduled_time: "06:40" },
      current: { scheduled_time: "06:43" },
    });
    const shaped = shapeDiff([moved6, moved5, moved3]);
    expect(shaped.timeMoves.map((r) => r.student_id)).toEqual(["s1", "s2"]);
    expect(shaped.timeMoves[0].moveMinutes).toBe(6);
    expect(shaped.timeMoves[1].moveMinutes).toBe(5);
  });

  it("keeps a time-move whose baseline time is unreadable (told a time never told)", () => {
    const shaped = shapeDiff([
      diffRow({
        categories: [DIFF_TIME_MOVE],
        baseline: { scheduled_time: null },
        current: { scheduled_time: "06:46" },
      }),
    ]);
    expect(shaped.timeMoves).toHaveLength(1);
    expect(shaped.timeMoves[0].moveMinutes).toBeNull();
  });

  it("never invents a time move without the category", () => {
    const shaped = shapeDiff([
      diffRow({
        categories: [DIFF_PLACE_CHANGE],
        baseline: { scheduled_time: "06:00" },
        current: { scheduled_time: "07:00" },
      }),
    ]);
    expect(shaped.timeMoves).toEqual([]);
    expect(shaped.placeChanges).toHaveLength(1);
  });

  it("buckets every category, allowing a row in several buckets", () => {
    const row = diffRow({
      categories: [DIFF_BUS_CHANGE, DIFF_PLACE_CHANGE],
      current: { bus_name: "Twiga", stop_name: "New stop", scheduled_time: "06:30" },
      baseline: { bus_id: "old", stop_name: "Old stop", scheduled_time: "06:31" },
    });
    const shaped = shapeDiff([
      row,
      diffRow({ student_id: "u1", categories: [DIFF_NEWLY_UNPLACEABLE] }),
      diffRow({ student_id: "u2", categories: [DIFF_LEG_REMOVED] }),
      diffRow({ student_id: "f1", categories: [DIFF_FIRST_COMMUNICATION] }),
    ]);
    expect(shaped.busChanges).toHaveLength(1);
    expect(shaped.placeChanges).toHaveLength(1);
    expect(shaped.newlyUnplaceable.map((r) => r.student_id)).toEqual(["u1"]);
    expect(shaped.legRemoved.map((r) => r.student_id)).toEqual(["u2"]);
    expect(shaped.firstCommunications.map((r) => r.student_id)).toEqual(["f1"]);
  });

  it("handles null/undefined rows", () => {
    expect(shapeDiff(null).busChanges).toEqual([]);
    expect(shapeDiff(undefined).timeMoves).toEqual([]);
  });
});

describe("moveMinutes / hhmmToMinutes", () => {
  it("parses defensively", () => {
    expect(hhmmToMinutes("06:45")).toBe(405);
    expect(hhmmToMinutes("6:45")).toBe(405);
    expect(hhmmToMinutes("24:00")).toBeNull();
    expect(hhmmToMinutes("junk")).toBeNull();
    expect(hhmmToMinutes(null)).toBeNull();
    expect(hhmmToMinutes(undefined)).toBeNull();
  });

  it("is absolute and null when either side is missing", () => {
    expect(
      moveMinutes({ baseline: { scheduled_time: "06:50" }, current: { scheduled_time: "06:40" } }),
    ).toBe(10);
    expect(moveMinutes({ baseline: null, current: { scheduled_time: "06:40" } })).toBeNull();
  });
});

describe("isFirstApplyDiff (first-apply copy predicate)", () => {
  it("fires only when every row is a first communication, and there is at least one", () => {
    expect(isFirstApplyDiff([])).toBe(false);
    expect(isFirstApplyDiff(null)).toBe(false);
    expect(
      isFirstApplyDiff([
        diffRow({ categories: [DIFF_FIRST_COMMUNICATION] }),
        diffRow({ student_id: "s2", categories: [DIFF_FIRST_COMMUNICATION] }),
      ]),
    ).toBe(true);
    expect(
      isFirstApplyDiff([
        diffRow({ categories: [DIFF_FIRST_COMMUNICATION] }),
        diffRow({ student_id: "s2", categories: [DIFF_BUS_CHANGE] }),
      ]),
    ).toBe(false);
  });

  it("pins the copy the panel renders", () => {
    expect(FIRST_APPLY_MESSAGE).toBe(
      "This is the school's first plan — every family will be notified.",
    );
  });
});

describe("buildAcknowledgmentPayload (R23)", () => {
  it("includes every unplaceable (student, leg) exactly once", () => {
    const byLeg = {
      morning: [
        { student_id: "a", name: "Amina", leg: "morning", constraint: "seats" },
        { student_id: "b", name: "Brian", leg: "morning", constraint: "unresolved address" },
      ],
      afternoon: [
        { student_id: "a", name: "Amina", leg: "afternoon", constraint: "seats" },
        // A duplicate entry must not double-acknowledge.
        { student_id: "a", name: "Amina", leg: "afternoon", constraint: "seats" },
      ],
    };
    const payload = buildAcknowledgmentPayload(flattenUnplaceable(byLeg));
    expect(payload).toEqual([
      { student_id: "a", leg: "morning" },
      { student_id: "b", leg: "morning" },
      { student_id: "a", leg: "afternoon" },
    ]);
  });

  it("is empty for an empty or missing list", () => {
    expect(buildAcknowledgmentPayload([])).toEqual([]);
    expect(buildAcknowledgmentPayload(null)).toEqual([]);
    expect(flattenUnplaceable(null)).toEqual([]);
  });

  it("keys checkboxes per (student, leg)", () => {
    expect(ackKey("a", "morning")).not.toBe(ackKey("a", "afternoon"));
  });
});

describe("movedOrder (reorder echo)", () => {
  it("shifts one key and returns the full order", () => {
    expect(movedOrder(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
    expect(movedOrder(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });

  it("refuses no-ops and out-of-bounds moves (the arrows' disabled predicate)", () => {
    expect(movedOrder(["a", "b"], 0, 0)).toBeNull();
    expect(movedOrder(["a", "b"], 0, -1)).toBeNull();
    expect(movedOrder(["a", "b"], 1, 2)).toBeNull();
  });
});

describe("copy pins", () => {
  it("states the same-day consequence in the active-run warning", () => {
    expect(ACTIVE_RUN_WARNING).toContain("next run");
    expect(ACTIVE_RUN_WARNING).toContain("afternoon");
  });

  it("states the one-level restore rule", () => {
    expect(RESTORE_CONFIRM_MESSAGE).toContain("Only one step back exists.");
  });

  it("pins the idempotent already-applied receipt (no counts to interpolate)", () => {
    // The apply/restore no-op answer carries no routes_written /
    // notified_family_count — the copy must claim none.
    expect(ALREADY_APPLIED_TITLE).toBe("Already applied — nothing to redo");
    expect(ALREADY_APPLIED_DESCRIPTION).toBe(
      "An earlier request already made this plan live — no routes were " +
        "rewritten and no families were re-notified.",
    );
    expect(ALREADY_APPLIED_DESCRIPTION).not.toContain("undefined");
  });
});
