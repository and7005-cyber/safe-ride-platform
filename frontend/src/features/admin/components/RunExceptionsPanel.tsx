import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Check, Flag } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { useActiveSchoolRole } from "@/lib/auth";
import { useSchoolKey } from "@/lib/queries";

/**
 * The run report's stop exceptions (GPS plan U4: R21, R22).
 *
 * Every row the server stores is listed here, in plain words, for as long as
 * the run exists: the kind, the stop, the children concerned, where the
 * phone said it was, the driver's answers, whether the stop is still open, and
 * who in the office has looked at it. Two things are deliberately kept apart:
 *
 * - open/resolved is derived by the server from the children's current
 *   outcomes and says whether the situation still stands;
 * - reviewed is a stored stamp and says only that a director or coordinator
 *   has seen the row. Reviewing an open exception is normal — the office may
 *   have phoned the driver already — and it stops the row counting on the
 *   Runs and Dashboard badges without hiding it.
 *
 * A fix corroborates and does not prove: "phone reported within 20 m" is
 * what the phone claimed, with the accuracy it claimed. Parents never see any
 * of this (R22) — the panel lives on a staff-only route and reads a staff-only
 * field of the report.
 */

export type ExceptionKind =
  | "custody-away"
  | "stop-bypassed"
  | "absent-remote"
  | "absent-attested"
  | "unverified"
  | "implausible-movement";

export const EXCEPTION_KINDS: readonly ExceptionKind[] = [
  "custody-away",
  "stop-bypassed",
  "absent-remote",
  "absent-attested",
  "unverified",
  "implausible-movement",
];

/** Sentence case, like every other label the office reads. The two kinds that
 * also raise an office alert share their wording with ADMIN_INCIDENT_LABEL so
 * the alert and the panel name the same thing the same way. */
export const EXCEPTION_KIND_LABEL: Record<ExceptionKind, string> = {
  "custody-away": "Tap far from the stop",
  "stop-bypassed": "Stop passed without outcomes",
  "absent-remote": "Absent marked away from the stop",
  "absent-attested": "Absent attested by the driver",
  unverified: "Check not verified",
  "implausible-movement": "Implausible movement",
};

/** One line under the label, so the row explains itself without the guide. */
export const EXCEPTION_KIND_NOTE: Record<ExceptionKind, string> = {
  "custody-away":
    "A Board or Drop-off was tapped further from the stop than the school's threshold allows.",
  "stop-bypassed":
    "An Arrive moved the run past this stop while a child there still had no outcome.",
  "absent-remote":
    "The driver marked the child absent away from the stop and did not say a parent or the office had told them.",
  "absent-attested":
    "Marked absent away from the stop; the driver said a parent or the office told them. Kept for history only.",
  unverified: "The location check for this tap could not be made.",
  "implausible-movement":
    "A fix on this run moved further or faster than a bus can, or reported an accuracy that cannot be trusted.",
};

const REASON_LABEL: Record<string, string> = {
  "no-fix": "no fix for this action",
  "too-coarse": "fix too coarse",
  "stop-unverified": "stop position unverified",
  invalid: "fix could not be read",
  jump: "implausible jump between fixes",
  accuracy: "accuracy reading cannot be trusted",
};

const RESPONSE_LABEL: Record<string, string> = {
  confirmed: "confirmed by the driver",
  dismissed: "dismissed by the driver",
  resolution: "answered by the driver recording the children",
  "told-me": "a parent or the office told the driver",
  "not-at-stop": "the driver was not at the stop",
  undo: "undone by the driver",
  // The custody undo (GPS plan U9): a pending prompt answered this way, or
  // the row appended after a confirmation the driver later withdrew.
  retracted: "undone by the driver",
};

export interface RunExceptionEvent {
  id: string;
  student_id: string | null;
  distance_m: number | null;
  prompt_state: "pending" | "answered" | "unanswered" | null;
  delivered_at: string | null;
  shown_at: string | null;
  response: string | null;
  call_now_due_at: string | null;
  call_now_sent_at: string | null;
  created_at: string;
}

export interface RunException {
  id: string;
  kind: string;
  reason: string | null;
  stop_order: number | null;
  stop_name: string | null;
  student_name: string | null;
  students: { id: string; name: string }[];
  fix_lat: number | null;
  fix_lng: number | null;
  fix_accuracy_m: number | null;
  fix_captured_at: string | null;
  distance_m: number | null;
  seen_at_stop: boolean | null;
  /** Derived by the server: open/resolved for a bypassed stop (current
   * outcomes), open/confirmed/retracted for a custody tap (its prompt
   * answers); null for the kinds that define none. */
  status: "open" | "resolved" | "confirmed" | "retracted" | null;
  created_at: string;
  reviewed_at: string | null;
  reviewed_by_display: string | null;
  events: RunExceptionEvent[];
}

// --- pure presentation helpers (unit-tested) ---------------------------------

export function kindLabel(kind: string): string {
  return (EXCEPTION_KIND_LABEL as Record<string, string>)[kind] ?? "Stop exception";
}

export function kindNote(kind: string): string | null {
  return (EXCEPTION_KIND_NOTE as Record<string, string>)[kind] ?? null;
}

/** A stored reason in words; an unknown reason is echoed with its hyphens
 * opened out rather than hidden, because the office needs to see it. */
export function reasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_LABEL[reason] ?? reason.replace(/-/g, " ");
}

export function responseLabel(response: string | null | undefined): string | null {
  if (!response) return null;
  return RESPONSE_LABEL[response] ?? response.replace(/-/g, " ");
}

/** Metres under a kilometre, one decimal above it: "180 m", "2.3 km". */
export function formatDistance(metres: number | null | undefined): string | null {
  if (metres == null || !Number.isFinite(metres)) return null;
  if (metres < 1000) return `${Math.round(metres)} m`;
  return `${(metres / 1000).toFixed(1)} km`;
}

function clock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : format(at, "HH:mm");
}

function stamp(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : format(at, "d MMM, HH:mm");
}

/** Where the phone said it was — nothing at all when no fix was stored (a
 * bypassed stop reads no fix; a purged trail leaves the coordinates null). */
export function fixLine(x: Pick<RunException, "fix_lat" | "fix_lng" | "fix_accuracy_m" | "fix_captured_at">): string | null {
  if (x.fix_lat == null || x.fix_lng == null) return null;
  const within = x.fix_accuracy_m != null ? `within ${formatDistance(x.fix_accuracy_m)}` : "with no accuracy given";
  const at = clock(x.fix_captured_at);
  return `Phone reported ${within}${at ? ` at ${at}` : ""}`;
}

export function distanceLine(x: Pick<RunException, "kind" | "distance_m">): string | null {
  const distance = formatDistance(x.distance_m);
  if (!distance) return null;
  if (x.kind === "implausible-movement") return `Moved ${distance} between fixes`;
  return `${distance} from the stop`;
}

export function seenAtStopLine(x: Pick<RunException, "seen_at_stop">): string | null {
  if (x.seen_at_stop == null) return null;
  return `Bus seen at stop: ${x.seen_at_stop ? "yes" : "no"}`;
}

/** The children the row is about: the ones still without an outcome for a
 * bypassed stop, the children tapped for a custody or unverified row, the
 * named child for a student-keyed one. */
export function childrenLine(x: Pick<RunException, "kind" | "students" | "student_name" | "status">): string | null {
  if (x.students.length > 0) {
    const names = x.students.map((s) => s.name);
    return x.kind === "stop-bypassed" ? `Still no outcome: ${names.join(", ")}` : names.join(", ");
  }
  if (x.student_name) return x.student_name;
  if (x.status === "resolved") return "Every child at this stop now has an outcome";
  return null;
}

/** The status badge's wording and tone, or null when the kind defines none. */
export function statusBadge(status: RunException["status"]): { label: string; variant: "warning" | "success" | "secondary" } | null {
  switch (status) {
    case "open":
      return { label: "Open", variant: "warning" };
    case "resolved":
      return { label: "Resolved", variant: "success" };
    case "confirmed":
      return { label: "Confirmed by driver", variant: "success" };
    case "retracted":
      return { label: "Retracted", variant: "secondary" };
    default:
      return null;
  }
}

/** One ledger event in plain words, oldest first in the panel. */
export function eventLine(event: RunExceptionEvent): string {
  const at = clock(event.created_at);
  const when = at ? `${at} — ` : "";
  const away = event.distance_m != null ? ` (${formatDistance(event.distance_m)} away)` : "";
  const parts: string[] = [];
  if (event.prompt_state === "pending") {
    if (event.shown_at) parts.push(`prompt shown to the driver at ${clock(event.shown_at)}, not yet answered`);
    else if (event.delivered_at) parts.push("prompt sent to the driver's phone, not yet shown");
    else parts.push("prompt waiting to reach the driver");
  } else if (event.prompt_state === "answered") {
    parts.push(`prompt answered: ${responseLabel(event.response) ?? "answered"}`);
  } else if (event.prompt_state === "unanswered") {
    parts.push("prompt closed unanswered");
  } else if (event.response === "resolution") {
    parts.push("a child at this stop was recorded");
  } else if (event.response) {
    parts.push(responseLabel(event.response) ?? event.response);
  } else {
    parts.push("tap recorded, no prompt shown");
  }
  if (event.call_now_sent_at) {
    parts.push(`call-now notice sent to the family at ${clock(event.call_now_sent_at)}`);
  } else if (event.call_now_due_at) {
    parts.push("call-now notice due, not yet sent");
  }
  return `${when}${parts.join(" · ")}${away}`;
}

export function unreviewedCount(exceptions: Pick<RunException, "reviewed_at">[]): number {
  return exceptions.filter((x) => !x.reviewed_at).length;
}

// --- the panel -----------------------------------------------------------------

export function RunExceptionsPanel({
  runId,
  exceptions,
}: {
  runId: string;
  exceptions: RunException[];
}) {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const { toast } = useToast();
  // Director or coordinator (the provider stepped in resolves as director);
  // the server's require_staff is the backstop.
  const canReview = useActiveSchoolRole() !== null;
  const [pending, setPending] = useState<string | null>(null);
  const toReview = unreviewedCount(exceptions);

  const review = async (exceptionId: string) => {
    setPending(exceptionId);
    try {
      await api.post(`/api/runs/${runId}/exceptions/${exceptionId}/review`, {});
      // The report re-reads with the stamp; both run lists drop the badge count.
      await Promise.all([
        qc.invalidateQueries({ queryKey: schoolKey("run-report", runId) }),
        qc.invalidateQueries({ queryKey: schoolKey("runs") }),
      ]);
    } catch (err) {
      toast({ title: "Cannot mark reviewed", description: (err as Error).message, variant: "destructive" });
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="space-y-2" data-testid="run-exceptions">
      <div className="flex items-center gap-2">
        <p className="text-sm font-medium">Stop exceptions</p>
        {exceptions.length > 0 && (
          toReview > 0 ? (
            <Badge variant="warning" data-testid="run-exceptions-to-review">
              <Flag className="h-3 w-3" /> {toReview} to review
            </Badge>
          ) : (
            <Badge variant="outline">All reviewed</Badge>
          )
        )}
      </div>
      {exceptions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No stop exceptions on this run.</p>
      ) : (
        <ul className="space-y-2">
          {exceptions.map((x) => {
            const lines = [fixLine(x), distanceLine(x), seenAtStopLine(x)].filter(Boolean) as string[];
            const reason = reasonLabel(x.reason);
            const children = childrenLine(x);
            const note = kindNote(x.kind);
            const status = statusBadge(x.status);
            return (
              <li
                key={x.id}
                data-testid={`exception-${x.id}`}
                data-kind={x.kind}
                className="space-y-1.5 rounded-md border p-3 text-sm"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">{kindLabel(x.kind)}</span>
                  {reason && <span className="text-muted-foreground">— {reason}</span>}
                  {/* Derived by the server — from current outcomes for a
                      bypassed stop, from the prompt answers for a custody
                      tap; only the kinds that define it carry it. */}
                  {status && <Badge variant={status.variant}>{status.label}</Badge>}
                </div>
                {note && <p className="text-xs text-muted-foreground">{note}</p>}
                <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                  <p>
                    <span className="text-xs text-muted-foreground">Stop</span>
                    <br />
                    {x.stop_name
                      ? `${x.stop_order != null ? `${x.stop_order} · ` : ""}${x.stop_name}`
                      : "—"}
                  </p>
                  <p>
                    <span className="text-xs text-muted-foreground">Children</span>
                    <br />
                    {children ?? "—"}
                  </p>
                </div>
                {lines.length > 0 && (
                  <p className="text-muted-foreground">{lines.join(" · ")}</p>
                )}
                {x.events.length > 0 && (
                  <ul className="space-y-0.5 text-xs text-muted-foreground">
                    {x.events.map((event) => (
                      <li key={event.id}>{eventLine(event)}</li>
                    ))}
                  </ul>
                )}
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  <span className="text-xs text-muted-foreground">
                    Raised {stamp(x.created_at) ?? "—"}
                  </span>
                  {x.reviewed_at ? (
                    <span
                      className="flex items-center gap-1.5 text-xs"
                      data-testid={`reviewed-${x.id}`}
                    >
                      <Badge variant="outline">
                        <Check className="h-3 w-3" /> Reviewed
                      </Badge>
                      <span className="text-muted-foreground">
                        by {x.reviewed_by_display ?? "staff"} · {stamp(x.reviewed_at)}
                      </span>
                    </span>
                  ) : canReview ? (
                    <Button
                      size="sm"
                      variant="outline"
                      data-testid={`review-exception-${x.id}`}
                      disabled={pending === x.id}
                      onClick={() => review(x.id)}
                    >
                      <Check className="h-4 w-4" /> Mark reviewed
                    </Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
