// U9 — pure decision logic for the fleet-plan review surface (PlanReviewPage).
// Exported for tests/unit/planReview.test.ts, following the validation.ts /
// RoutesPage precedent of keeping every gate predicate unit-testable without
// a browser. The server owns the real gates (apply re-checks everything under
// its locks); these client mirrors exist so the dialogs can be assembled and
// disabled correctly BEFORE the request, and so the payload the admin
// confirmed is exactly the payload sent.

// --- server vocabulary mirrors (backend/app/dao/fleet_plan_dao.py) ----------

/** R15/R24: a communicated stop that moves by at least this many minutes
 * notifies — the client mirror of NOTIFY_MOVE_THRESHOLD_MIN. */
export const NOTIFY_MOVE_THRESHOLD_MIN = 5;

/** Shared diff categories the review endpoint's rows carry (R24 = R15). */
export const DIFF_BUS_CHANGE = "bus-change";
export const DIFF_TIME_MOVE = "time-move";
export const DIFF_PLACE_CHANGE = "place-change";
export const DIFF_FIRST_COMMUNICATION = "first-communication";
export const DIFF_NEWLY_UNPLACEABLE = "newly-unplaceable";
export const DIFF_LEG_REMOVED = "leg-removed";

/** R22 basis-drift kinds — confirmed as (kind, student_id) in the apply payload. */
export type GateKind = "enrolled" | "address-changed" | "departed";

export const PLAN_LEGS = ["morning", "afternoon"] as const;
export type PlanLeg = (typeof PLAN_LEGS)[number];

export const PATTERN_LABEL: Record<string, string> = {
  both_ways: "Both ways",
  morning_only: "Morning only",
  afternoon_only: "Afternoon only",
  split: "Split (different buses)",
};

export function legLabel(leg: string): string {
  return leg === "afternoon" ? "Afternoon" : "Morning";
}

// --- copy the page pins (tested so wording drift is deliberate) -------------

/** Degraded-draft language — the Fleet Map planner's existing degraded badge
 * wording, reused verbatim so "approximate times" reads the same everywhere. */
export const PLAN_DEGRADED_MESSAGE =
  "Schedule couldn't be fully optimised — times are approximate.";

/** Shown when the diff is exclusively first communications (a school with no
 * communicated baseline — i.e. its first plan). */
export const FIRST_APPLY_MESSAGE =
  "This is the school's first plan — every family will be notified.";

/** Apply/restore warning when the school has a run in progress (System-Wide
 * Impact: the parent portal reads live tables, not run snapshots). */
export const ACTIVE_RUN_WARNING =
  "A run is currently active for this school. Parents' live tracking will not " +
  "match the bus until that run ends. Changes take effect from the next run — " +
  "which can be this same afternoon.";

/** R23 consequence copy for the by-name acknowledgments (the server's own
 * wording for the same-day consequence when apply lands between runs). */
export const UNPLACEABLE_CONSEQUENCE =
  "They will have no seat from the next run — which can be this same afternoon.";

/** In-draft re-solve confirm (U5's contract, stated before the request). */
export const RESOLVE_CONFIRM_MESSAGE =
  "Re-solving re-optimises the draft; pinned children and patterns are kept, " +
  "other manual arrangements are discarded.";

/** One-level restore rule (R21), stated on the restore confirm. */
export const RESTORE_CONFIRM_MESSAGE =
  "Restoring returns to the previous plan; the current one becomes restorable " +
  "in its place. Only one step back exists.";

/** U6/U7 idempotent answer: apply/restore on an already-applied plan returns
 * `{already_applied, plan}` WITHOUT routes_written / notified_family_count
 * (the gateway-timeout-retry case the DAO anticipates), so the success toast
 * must branch on it instead of interpolating counts the response never
 * carried. */
export const ALREADY_APPLIED_TITLE = "Already applied — nothing to redo";
export const ALREADY_APPLIED_DESCRIPTION =
  "An earlier request already made this plan live — no routes were rewritten " +
  "and no families were re-notified.";

// --- R22 gate list (basis drift, computed client-side from the draft basis) --

export interface BasisStudentLike {
  id: string;
  name: string;
  lat?: number | null;
  lng?: number | null;
}

export interface LiveStudentLike {
  id: string;
  name: string;
  home_lat?: number | null;
  home_lng?: number | null;
}

export interface GateRow {
  kind: GateKind;
  student_id: string;
  name: string;
}

// Client mirror of the server's float-noise guard (_COORD_EPS): coordinates
// closer than ~1 cm are the same place.
const COORD_EPS = 1e-7;

function coordsDiffer(a: number | null | undefined, b: number | null | undefined): boolean {
  if (a == null || b == null) return (a == null) !== (b == null);
  return Math.abs(a - b) > COORD_EPS;
}

const GATE_KIND_ORDER: GateKind[] = ["enrolled", "address-changed", "departed"];

export const GATE_KIND_LABEL: Record<GateKind, string> = {
  enrolled: "Enrolled since this draft was generated — they have no seat in it",
  "address-changed": "Address changed since this draft was generated — their stop may be wrong",
  departed: "Departed since this draft was generated — their stop will be dropped",
};

/** Restore-flavoured R22 labels: the server's `previous.drift` rows share the
 * gate-kind vocabulary but read against the preserved capture, not the draft
 * basis. Restore never gates on address changes (communicated stops are put
 * back verbatim), but the label exists so an unexpected row still renders
 * honestly rather than blank. */
export const RESTORE_GATE_KIND_LABEL: Record<GateKind, string> = {
  enrolled: "Enrolled after this plan was preserved — they will be left without a route",
  "address-changed": "Address changed since this plan was preserved",
  departed: "Departed since this plan was preserved — their stop will be dropped",
};

/**
 * The R22 basis-drift list, mirroring apply's server-side gate: students
 * enrolled since generation (live but not in the basis), address changes
 * (coordinates drifted beyond float noise), and departures (in the basis but
 * no longer live). Every row must be individually confirmed before apply; the
 * server re-derives the same list under its locks and 409s on any mismatch.
 */
export function buildGateList(
  basisStudents: BasisStudentLike[],
  liveStudents: LiveStudentLike[],
): GateRow[] {
  const basisById = new Map(basisStudents.map((s) => [String(s.id), s]));
  const liveIds = new Set(liveStudents.map((s) => String(s.id)));
  const rows: GateRow[] = [];
  for (const live of liveStudents) {
    const sid = String(live.id);
    const basis = basisById.get(sid);
    if (!basis) {
      rows.push({ kind: "enrolled", student_id: sid, name: live.name });
    } else if (
      coordsDiffer(basis.lat, live.home_lat) ||
      coordsDiffer(basis.lng, live.home_lng)
    ) {
      rows.push({ kind: "address-changed", student_id: sid, name: live.name });
    }
  }
  for (const basis of basisStudents) {
    if (!liveIds.has(String(basis.id))) {
      // The basis denormalizes names, so a departed child is still named.
      rows.push({ kind: "departed", student_id: String(basis.id), name: basis.name });
    }
  }
  rows.sort(
    (a, b) =>
      GATE_KIND_ORDER.indexOf(a.kind) - GATE_KIND_ORDER.indexOf(b.kind) ||
      a.name.localeCompare(b.name) ||
      a.student_id.localeCompare(b.student_id),
  );
  return rows;
}

export interface GateGroup {
  kind: GateKind;
  label: string;
  rows: GateRow[];
}

/** Group gate rows by kind in the fixed enrolled → address-changed → departed
 * order, dropping empty groups — the R22 dialog's section structure. Serves
 * both gate dialogs: the apply flow feeds it the client-built basis list with
 * the default (draft-flavoured) labels; the restore flow feeds it the server's
 * `previous.drift` rows — the same {kind, student_id, name} shape — with
 * RESTORE_GATE_KIND_LABEL. */
export function groupGateRows(
  rows: GateRow[],
  labels: Record<GateKind, string> = GATE_KIND_LABEL,
): GateGroup[] {
  return GATE_KIND_ORDER.map((kind) => ({
    kind,
    label: labels[kind],
    rows: rows.filter((r) => r.kind === kind),
  })).filter((g) => g.rows.length > 0);
}

/**
 * The gate dialog's "Discard and re-draft" escape appears only when the drift
 * list is longer than this — at that size, per-row confirmation is busywork
 * and the honest act is a fresh draft against today's school.
 *
 * Documented design decision (plan Open Questions, 2026-08-19 review): the
 * threshold is 10 rows — strictly more than 10 shows the escape.
 */
export const GATE_ESCAPE_THRESHOLD = 10;

export function showDiscardEscape(rowCount: number): boolean {
  return rowCount > GATE_ESCAPE_THRESHOLD;
}

// --- R24 diff shaping --------------------------------------------------------

export interface PlanDiffStop {
  bus_id?: string | null;
  bus_name?: string | null;
  stop_name?: string | null;
  scheduled_time?: string | null;
  lat?: number | null;
  lng?: number | null;
}

export interface PlanDiffRow {
  student_id: string;
  student_name: string;
  leg: string;
  categories: string[];
  current: PlanDiffStop | null;
  baseline: PlanDiffStop | null;
  live_bus_id?: string | null;
  family_ids?: string[];
}

export interface ShapedDiff {
  busChanges: PlanDiffRow[];
  timeMoves: Array<PlanDiffRow & { moveMinutes: number | null }>;
  placeChanges: PlanDiffRow[];
  firstCommunications: PlanDiffRow[];
  newlyUnplaceable: PlanDiffRow[];
  legRemoved: PlanDiffRow[];
}

/** Defensive HH:MM → minutes-from-midnight (null on malformed/absent) — the
 * client twin of the server's parser. */
export function hhmmToMinutes(hhmm: string | null | undefined): number | null {
  if (!hhmm || typeof hhmm !== "string") return null;
  const match = /^(\d{1,2}):(\d{2})/.exec(hhmm.trim());
  if (!match) return null;
  const h = Number(match[1]);
  const m = Number(match[2]);
  if (h > 23 || m > 59) return null;
  return h * 60 + m;
}

/** Absolute minutes between a row's baseline and current stop times; null
 * when either side is absent or malformed. */
export function moveMinutes(row: Pick<PlanDiffRow, "baseline" | "current">): number | null {
  const oldMin = hhmmToMinutes(row.baseline?.scheduled_time);
  const newMin = hhmmToMinutes(row.current?.scheduled_time);
  if (oldMin == null || newMin == null) return null;
  return Math.abs(newMin - oldMin);
}

/**
 * Bucket the shared-diff rows into the panel's sections. A row can appear in
 * several buckets (its categories are a list). Time moves are re-derived from
 * the row's own times and admitted at ≥ NOTIFY_MOVE_THRESHOLD_MIN minutes
 * ONLY — a sub-threshold drift can never render as a move, whatever the
 * category list claims. (A time-move row whose baseline time is unreadable is
 * kept — the family is being told a time they were never told, the server's
 * own rule — with moveMinutes null.)
 */
export function shapeDiff(rows: PlanDiffRow[] | null | undefined): ShapedDiff {
  const shaped: ShapedDiff = {
    busChanges: [],
    timeMoves: [],
    placeChanges: [],
    firstCommunications: [],
    newlyUnplaceable: [],
    legRemoved: [],
  };
  for (const row of rows ?? []) {
    const cats = row.categories ?? [];
    if (cats.includes(DIFF_BUS_CHANGE)) shaped.busChanges.push(row);
    if (cats.includes(DIFF_PLACE_CHANGE)) shaped.placeChanges.push(row);
    if (cats.includes(DIFF_FIRST_COMMUNICATION)) shaped.firstCommunications.push(row);
    if (cats.includes(DIFF_NEWLY_UNPLACEABLE)) shaped.newlyUnplaceable.push(row);
    if (cats.includes(DIFF_LEG_REMOVED)) shaped.legRemoved.push(row);
    if (cats.includes(DIFF_TIME_MOVE)) {
      const moved = moveMinutes(row);
      if (moved == null || moved >= NOTIFY_MOVE_THRESHOLD_MIN) {
        shaped.timeMoves.push({ ...row, moveMinutes: moved });
      }
    }
  }
  return shaped;
}

/** First-apply copy predicate: every diff row is a first communication (and
 * there is at least one) — the whole school hears for the first time. */
export function isFirstApplyDiff(rows: PlanDiffRow[] | null | undefined): boolean {
  const list = rows ?? [];
  return (
    list.length > 0 &&
    list.every((r) => (r.categories ?? []).includes(DIFF_FIRST_COMMUNICATION))
  );
}

// --- R23 acknowledgments -----------------------------------------------------

export interface UnplaceableEntry {
  student_id: string;
  name?: string;
  leg: string;
  constraint?: string;
}

/** Flatten the review endpoint's per-leg unplaceable map into one list in
 * fixed leg order (morning first), preserving each entry's fields. */
export function flattenUnplaceable(
  byLeg: Record<string, UnplaceableEntry[]> | null | undefined,
): UnplaceableEntry[] {
  const out: UnplaceableEntry[] = [];
  for (const leg of PLAN_LEGS) {
    for (const u of byLeg?.[leg] ?? []) out.push(u);
  }
  return out;
}

/**
 * The R23 payload: every unplaceable (student, leg) exactly once. The apply
 * endpoint 422s naming any missing pair, so the payload is assembled from the
 * full list — the dialog's checkboxes gate the button, never the payload.
 */
export function buildAcknowledgmentPayload(
  unplaceable: UnplaceableEntry[] | null | undefined,
): Array<{ student_id: string; leg: string }> {
  const seen = new Set<string>();
  const out: Array<{ student_id: string; leg: string }> = [];
  for (const u of unplaceable ?? []) {
    const key = `${u.student_id}|${u.leg}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ student_id: String(u.student_id), leg: u.leg });
  }
  return out;
}

/** Stable key for one (student, leg) checkbox. */
export function ackKey(studentId: string, leg: string): string {
  return `${studentId}|${leg}`;
}

// --- reorder ----------------------------------------------------------------

/**
 * Full-order echo for POST /{plan}/reorder: the leg's stop keys with the stop
 * at `from` shifted into `to`'s slot. Null when the move is out of bounds or
 * a no-op — the arrows reuse this as their disabled predicate, matching the
 * RoutesPage buildReorderPayload contract.
 */
export function movedOrder(keys: string[], from: number, to: number): string[] | null {
  if (from === to) return null;
  if (from < 0 || to < 0 || from >= keys.length || to >= keys.length) return null;
  const order = [...keys];
  const [moved] = order.splice(from, 1);
  order.splice(to, 0, moved);
  return order;
}

// --- small formatters shared by the panels -----------------------------------

export function fmtRide(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

export function fmtDriving(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  return `${Math.round(seconds / 60)} min`;
}
