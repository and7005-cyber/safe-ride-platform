// School Settings — the "Tracking" card's five per-school knobs (GPS plan
// U11: R26, R31, R38). Each is stored on the school as an integer or null
// (null = the system default, which the API serves as `tracking_defaults`).
// The form keeps strings ("" = use the default); the payload sends a number
// or an explicit null for EVERY knob, so a save from this page always states
// each knob — the server's "omitted keeps the stored value" rule exists for
// older pages that do not know these fields, not for this one.

export type TrackingKnob =
  | "custody_threshold_m"
  | "vicinity_radius_m"
  | "fix_accuracy_cap_m"
  | "position_retention_days"
  | "ping_interval_s";

export interface TrackingFieldSpec {
  key: TrackingKnob;
  label: string;
  unit: string;
  /** Inclusive bounds — the same numbers as the server's 422 and the column CHECK. */
  min: number;
  max: number;
  help: string;
}

export const TRACKING_FIELDS: readonly TrackingFieldSpec[] = [
  {
    key: "custody_threshold_m",
    label: "Custody distance",
    unit: "m",
    min: 25,
    max: 2000,
    help: "A Board or Drop-off further than this from the child's stop, after the phone's accuracy is subtracted, asks the driver to confirm.",
  },
  {
    key: "vicinity_radius_m",
    label: "Stop vicinity",
    unit: "m",
    min: 25,
    max: 2000,
    help: "A phone fix this close to a stop counts as the bus being at it — an Absent marked here needs no follow-up, and the run report reads \"seen at stop\".",
  },
  {
    key: "fix_accuracy_cap_m",
    label: "Accuracy cap",
    unit: "m",
    min: 25,
    max: 2000,
    help: "A fix wider than this is too coarse to check a tap against; it is recorded but neither raises nor clears a question.",
  },
  {
    key: "position_retention_days",
    label: "Position retention",
    unit: "days",
    min: 7,
    max: 365,
    help: "How long the bus position trail is kept before it is purged. Exceptions keep their decision record longer, without the coordinates.",
  },
  {
    key: "ping_interval_s",
    label: "Ping interval",
    unit: "s",
    min: 5,
    max: 60,
    help: "How often the driver app sends the bus position between stops once live pings are enabled.",
  },
];

export type TrackingForm = Record<TrackingKnob, string>;
export type TrackingDefaults = Partial<Record<TrackingKnob, number>>;

/** Seed the form from the school row: a stored number as text, null as "". */
export function trackingFormFromSchool(
  school: Partial<Record<TrackingKnob, number | null | undefined>> | null | undefined,
): TrackingForm {
  const form = {} as TrackingForm;
  for (const spec of TRACKING_FIELDS) {
    const stored = school?.[spec.key];
    form[spec.key] = stored == null ? "" : String(stored);
  }
  return form;
}

/** The field's validation message, or null when it is empty (default) or a
 * whole number inside the bounds. Mirrors the server's bounds so an obvious
 * mistake never leaves the page; the server's 422 remains the authority. */
export function trackingFieldError(spec: TrackingFieldSpec, value: string): string | null {
  const text = value.trim();
  if (text === "") return null;
  if (!/^-?\d+$/.test(text)) return `Whole number of ${spec.unit === "m" ? "metres" : spec.unit}`;
  const n = Number(text);
  if (n < spec.min || n > spec.max) return `Between ${spec.min} and ${spec.max} ${spec.unit}`;
  return null;
}

/** True when every field is empty or valid. */
export function trackingFormValid(form: TrackingForm): boolean {
  return TRACKING_FIELDS.every((spec) => trackingFieldError(spec, form[spec.key]) === null);
}

/** The five knobs for the PUT body: a number, or an explicit null to clear
 * the knob to the system default. Every knob is present. */
export function trackingPayload(form: TrackingForm): Record<TrackingKnob, number | null> {
  const payload = {} as Record<TrackingKnob, number | null>;
  for (const spec of TRACKING_FIELDS) {
    const text = form[spec.key].trim();
    payload[spec.key] = text === "" ? null : Number(text);
  }
  return payload;
}
