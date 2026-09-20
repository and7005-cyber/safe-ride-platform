// The served bus position as every surface reads it (GPS plan U8: R7, R9,
// R27, R37). The server derives the position and its freshness once — the
// shared fragment in backend/app/dao/status_sql.py — and this module only
// puts words to it. Nothing here decides staleness: `stale` is the server's
// verdict against the school's threshold; the client's clock is used only to
// count the seconds shown beside it.

/** What a parent may see (R37, R22): coordinates, when, and whether it is stale. */
export interface ParentBusPosition {
  lat: number;
  lng: number;
  /** ISO 8601. Null for a checkpoint of unknown age (an older writer). */
  position_at: string | null;
  stale: boolean;
}

/** The staff payload adds the source and the run's GPS state (R9, R20, F5). */
export interface StaffBusPosition extends ParentBusPosition {
  source: "checkpoint" | "action" | "ping" | null;
  accuracy_m: number | null;
  age_s: number | null;
  /** The run's latest tap carried no fix for a device reason (denied, off, timeout). */
  gps_off: boolean;
  /** The run has taps and none of them carried a fix. */
  no_gps_for_run: boolean;
  /** "Phone GPS" for a fix; where the run is for a checkpoint. */
  label: string | null;
}

/** The accuracy circle never grows past this (an approximate-location grant
 * reports kilometres and would swallow the map). */
export const ACCURACY_CIRCLE_CAP_M = 300;

/** Staff wording for the source. One map, so the info window, the buses card
 * and any future surface cannot disagree. */
export const POSITION_SOURCE_LABEL: Record<NonNullable<StaffBusPosition["source"]>, string> = {
  checkpoint: "Planned stop",
  action: "Phone GPS (tap)",
  ping: "Phone GPS (live)",
};

export function sourceLabel(source: StaffBusPosition["source"] | undefined): string {
  if (!source) return "Checkpoint (older app)";
  return POSITION_SOURCE_LABEL[source] ?? source;
}

/** Whole seconds since `positionAt` at `nowMs`, never negative (a client clock
 * slightly behind the server must not read "-3 s ago"). Null when unknown. */
export function ageSeconds(positionAt: string | null | undefined, nowMs = Date.now()): number | null {
  if (!positionAt) return null;
  const at = Date.parse(positionAt);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.floor((nowMs - at) / 1000));
}

/** "just now", "42 s ago", "4 min ago", "2 h ago", "3 d ago". */
export function formatAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

/**
 * The freshness line every surface shows beside the position (R9, R27):
 * "updated 12 s ago" while fresh, "last seen 4 min ago" once the server says
 * stale. Null when there is no position or its time is unknown — a checkpoint
 * of unknown age makes no claim rather than a wrong one.
 */
export function freshnessLabel(
  position: Pick<ParentBusPosition, "position_at" | "stale"> | null | undefined,
  nowMs = Date.now(),
): string | null {
  if (!position) return null;
  const age = ageSeconds(position.position_at, nowMs);
  if (age == null) return null;
  return `${position.stale ? "last seen" : "updated"} ${formatAge(age)}`;
}

/** The radius to draw for a fix's accuracy: capped, and nothing for an
 * absent, zero or non-finite value. */
export function accuracyRadius(accuracyM: number | null | undefined): number | null {
  if (accuracyM == null || !Number.isFinite(accuracyM) || accuracyM <= 0) return null;
  return Math.min(accuracyM, ACCURACY_CIRCLE_CAP_M);
}
