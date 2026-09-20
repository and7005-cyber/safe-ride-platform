/**
 * One label source per status domain, for every role surface (U17/R26-R28).
 *
 * Before this, three views defined three independent label maps with no shared
 * source: the parent app said "At School" where admin said "At school", "On the
 * bus" where admin said "On bus", "Absent" where admin said "Absent today". The
 * driver had no map at all and rendered raw slugs. The parent alerts page used
 * Title Case against the parent home page's sentence case on the same screen.
 *
 * Four domains live here: student, bus, run and notification type. The
 * notification domain is not optional — the casing inconsistency it carries is
 * the exact defect R28 targets.
 *
 * Casing convention: sentence case throughout. Only the first word and proper
 * nouns are capitalised.
 *
 * Adding a value to any domain without adding its label fails the exhaustiveness
 * assertions in tests/unit/statusVocabulary.test.ts.
 */

export type BadgeVariant = "secondary" | "success" | "warning" | "destructive" | "outline";

// --- students ---------------------------------------------------------------
// The derived, day-scoped status computed by the backend. The raw `status`
// column still travels in the payload but never renders.

export type StudentDisplayStatus =
  | "at-school"
  | "on-bus"
  | "expected-on-bus"
  | "dropped-off"
  | "absent"
  | "unaccounted"
  | "at-home"
  | "unassigned";

export const STUDENT_STATUS_LABEL: Record<StudentDisplayStatus, string> = {
  "at-school": "At school",
  "on-bus": "On bus",
  // The afternoon auto-board presumes a boarding nobody observed (U3). Rendering
  // it as "On bus" would state as fact the one thing the driver has not yet
  // confirmed, which is the class of claim this work removes.
  "expected-on-bus": "Expected on bus",
  "dropped-off": "Dropped off",
  absent: "Absent today",
  // Recorded by the office force-close (U6): the app saying plainly that nobody
  // knows, instead of decaying to "At home" as it used to.
  unaccounted: "Unaccounted",
  "at-home": "At home",
  unassigned: "Unassigned",
};

export const STUDENT_STATUS_VARIANT: Record<StudentDisplayStatus, BadgeVariant> = {
  "at-school": "secondary",
  "on-bus": "success",
  // Not 'success': the presumption is not yet evidence.
  "expected-on-bus": "warning",
  "dropped-off": "warning",
  absent: "destructive",
  unaccounted: "destructive",
  "at-home": "secondary",
  unassigned: "outline",
};

/**
 * Parent-facing wording for the same derived values (U12/R27).
 *
 * Identical wording is right almost everywhere — U17 exists because the three
 * surfaces had drifted apart — so this map matches the operational one except
 * where the operational term would land badly on a family.
 *
 * 'unaccounted' is that case. It means the office does not yet know where the
 * child is and is about to phone the parents; the force-close raises that
 * obligation precisely so a person makes that call. Showing the bare term would
 * have the app break the news first, in a word chosen for a dispatcher.
 */
export const PARENT_STUDENT_STATUS_LABEL: Record<StudentDisplayStatus, string> = {
  ...STUDENT_STATUS_LABEL,
  unaccounted: "Being confirmed",
};

/** A line under the badge, where the label alone would leave a parent guessing. */
export const PARENT_STUDENT_STATUS_NOTE: Partial<Record<StudentDisplayStatus, string>> = {
  unaccounted: "The school is confirming where your child is and will call you.",
  "expected-on-bus": "The driver has not confirmed this yet.",
};

/** Filter options are the derived value set — one option per label entry. */
export const STUDENT_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(STUDENT_STATUS_LABEL).map(([value, label]) => ({ value, label })),
];

// --- buses ------------------------------------------------------------------
// Derived from the bus's current run, except out-of-service, which is the
// office-set availability attribute overriding it (U9). The retired stored
// column's 'offline' meaning lives here as 'out-of-service'.

export type BusDerivedStatus = "active" | "idle" | "delayed" | "out-of-service";

export const BUS_STATUS_LABEL: Record<BusDerivedStatus, string> = {
  active: "Active",
  idle: "Idle",
  delayed: "Delayed",
  "out-of-service": "Out of service",
};

export const BUS_STATUS_VARIANT: Record<BusDerivedStatus, BadgeVariant> = {
  active: "success",
  idle: "secondary",
  delayed: "warning",
  "out-of-service": "destructive",
};

export const BUS_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(BUS_STATUS_LABEL).map(([value, label]) => ({ value, label })),
];

export const BUS_AVAILABILITY_OPTIONS = [
  { value: "in-service", label: "In service" },
  { value: "out-of-service", label: "Out of service" },
];

// --- runs -------------------------------------------------------------------
// Stored and event-written, except 'delayed', which the office sets and which
// is the only manually maintained status value in the system.

export type RunStatus = "in-progress" | "completed" | "delayed";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  "in-progress": "In progress",
  completed: "Completed",
  delayed: "Delayed",
};

export const RUN_STATUS_VARIANT: Record<RunStatus, BadgeVariant> = {
  "in-progress": "success",
  completed: "secondary",
  delayed: "warning",
};

export const RUN_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(RUN_STATUS_LABEL).map(([value, label]) => ({ value, label })),
];

// --- notification and alert types -------------------------------------------
// Two namespaces that don't collide: typed parent notifications (the push feed
// mirror) and incident types. Parents and admins read some of the same incident
// types with different wording, which is deliberate — the admin feed is
// operational, the parent feed is addressed to a family.

export type NotificationType =
  | "run-started"
  | "student-boarded"
  | "bus-approaching"
  | "reached-school"
  | "on-way-home"
  | "dropped-off"
  | "student-absent"
  | "incident"
  | "admin-notice"
  | "ride-cancelled"
  | "boarding-corrected"
  | "custom";

export const NOTIFICATION_LABEL: Record<NotificationType, string> = {
  "run-started": "Bus on the way",
  "student-boarded": "Boarded the bus",
  "bus-approaching": "Bus approaching",
  "reached-school": "Arrived at school",
  "on-way-home": "On the way home",
  "dropped-off": "Dropped off",
  "student-absent": "Marked absent",
  incident: "Bus incident",
  "admin-notice": "School notice",
  "ride-cancelled": "Ride cancelled",
  // The driver withdrew a boarding mark (GPS plan U9/R35). Neutral on
  // purpose: it says the mark is gone, not where the child is.
  "boarding-corrected": "Boarding mark withdrawn",
  custom: "Notice",
};

export const NOTIFICATION_VARIANT: Record<NotificationType, BadgeVariant> = {
  "run-started": "secondary",
  "student-boarded": "success",
  "bus-approaching": "warning",
  "reached-school": "success",
  "on-way-home": "secondary",
  "dropped-off": "success",
  "student-absent": "destructive",
  incident: "destructive",
  "admin-notice": "warning",
  "ride-cancelled": "secondary",
  "boarding-corrected": "secondary",
  custom: "secondary",
};

/**
 * Incident types. `lifecycle` entries are office-only — they never reach the
 * parent feed — so PARENT_INCIDENT_LABEL covers only what a parent can receive.
 */
export type IncidentType =
  | "breakdown"
  | "accident"
  | "student"
  | "traffic"
  | "arrival"
  | "other"
  | "cancellation"
  | "run-started"
  | "run-completed"
  | "closure-refused"
  | "force-closed"
  | "handover-recorded"
  | "action-reversed"
  | "stop-bypassed"
  | "absent-remote";

export const ADMIN_INCIDENT_LABEL: Record<IncidentType, string> = {
  breakdown: "Vehicle breakdown",
  accident: "Road accident",
  student: "Student issue",
  traffic: "Heavy traffic / delay",
  arrival: "Arrived at school",
  other: "Notice",
  cancellation: "Ride cancellation",
  "run-started": "Route started",
  "run-completed": "Route ended",
  // Named for what the office has to do about them, not for the internal event.
  "closure-refused": "Route cannot close",
  "force-closed": "Route force-closed",
  "handover-recorded": "Left the bus off-route",
  "action-reversed": "Driver correction",
  // The two stop exceptions loud enough to reach the office feed as well as
  // the run's exceptions panel (GPS plan U4/R21). Office-only: each one names
  // a child whose whereabouts the driver's taps did not establish.
  "stop-bypassed": "Stop passed without outcomes",
  "absent-remote": "Absent marked away from the stop",
};

export const ADMIN_INCIDENT_VARIANT: Record<IncidentType, BadgeVariant> = {
  breakdown: "destructive",
  accident: "destructive",
  student: "warning",
  traffic: "warning",
  arrival: "success",
  other: "secondary",
  cancellation: "warning",
  // Lifecycle events are informational, not exceptional.
  "run-started": "secondary",
  "run-completed": "secondary",
  // These four are lifecycle rows too, but they are not routine: each one means
  // a child's outcome is unresolved, was resolved off-route, or was retracted.
  "closure-refused": "warning",
  "force-closed": "warning",
  "handover-recorded": "warning",
  "action-reversed": "secondary",
  // Safety-critical, not routine: a child may have been left at a stop or
  // marked absent from kilometres away.
  "stop-bypassed": "warning",
  "absent-remote": "warning",
};

/** What a parent can actually receive — lifecycle and arrival rows are excluded
 * from their feed server-side, so they have no parent wording. */
export type ParentIncidentType = "breakdown" | "accident" | "student" | "traffic" | "other";

export const PARENT_INCIDENT_LABEL: Record<ParentIncidentType, string> = {
  breakdown: "Vehicle breakdown",
  accident: "Road accident",
  student: "Student issue",
  traffic: "Traffic delay",
  other: "Notice",
};

export const PARENT_INCIDENT_VARIANT: Record<ParentIncidentType, BadgeVariant> = {
  breakdown: "destructive",
  accident: "destructive",
  student: "warning",
  traffic: "warning",
  other: "secondary",
};

// --- lookups ----------------------------------------------------------------
// Every render goes through one of these, so an unlabelled value shows the
// fallback rather than a raw slug.

export function labelFor(
  map: Record<string, string>,
  value: string | null | undefined,
  fallback = "Unknown",
): string {
  if (!value) return fallback;
  return map[value] ?? fallback;
}

/** The supplementary line for a value, or null when it needs none. */
export function noteFor(
  map: Partial<Record<string, string>>,
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  return map[value] ?? null;
}

export function variantFor(
  map: Record<string, BadgeVariant>,
  value: string | null | undefined,
  fallback: BadgeVariant = "secondary",
): BadgeVariant {
  if (!value) return fallback;
  return map[value] ?? fallback;
}

/** The status filter matches the derived display_status, never the raw status. */
export function studentMatchesFilter(
  student: { display_status?: string | null },
  filter: string,
): boolean {
  return filter === "all" || student.display_status === filter;
}

/**
 * Absence badge text: whole-day keeps the historical wording; partial-scope
 * parent cancellations name their half of the day.
 */
export function absenceBadgeLabel(scope?: string | null): string {
  if (scope === "morning") return "Absent (AM)";
  if (scope === "afternoon") return "Absent (PM)";
  return "Absent today";
}
