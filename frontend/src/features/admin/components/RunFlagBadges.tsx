import { AlertTriangle, Flag, PhoneCall } from "lucide-react";
import { Badge } from "@/components/ui/badge";

/**
 * The condition badges beside a run's status (U14/R26), shared by the Runs
 * page and the Dashboard's Active Runs card so a run reads the same wherever
 * the office meets it. The status badge itself stays with the caller: it comes
 * from the vocabulary, and both surfaces already render it.
 */
export function RunFlagBadges({
  run,
}: {
  run: {
    no_progress?: boolean;
    stale?: boolean;
    contact_pending?: number | string | null;
    exception_count?: number | string | null;
  };
}) {
  return (
    <>
      {/* Distinct from the office-set 'delayed' status: delay is a human
          judgement about the schedule, this is the absence of arrival taps —
          often a dead phone, which is the case force-close exists for. */}
      {run.no_progress && (
        <Badge variant="destructive" data-testid="no-progress">
          <AlertTriangle className="h-3 w-3" /> No taps
        </Badge>
      )}
      {/* A run open past its service day. It has fallen out of every driver
          path, so only the office can end it. */}
      {run.stale && (
        <Badge variant="warning" data-testid="stale-run">Needs closing</Badge>
      )}
      {/* Survives the force-close dialog being dismissed — the obligation is
          the point, so it has to be rediscoverable. */}
      {Number(run.contact_pending ?? 0) > 0 && (
        <Badge variant="destructive" data-testid="contact-pending">
          <PhoneCall className="h-3 w-3" /> {run.contact_pending} to call
        </Badge>
      )}
      {/* Stop exceptions nobody in the office has looked at yet (GPS plan
          U4/R21). Counted from the stored review stamp, never from whether the
          exception is still open: a reviewed exception may still be open, and
          an open one the office has seen no longer needs the badge. Absent
          from a driver's list altogether (R22). */}
      {Number(run.exception_count ?? 0) > 0 && (
        <Badge variant="warning" data-testid="exceptions-to-review">
          <Flag className="h-3 w-3" /> {run.exception_count} to review
        </Badge>
      )}
    </>
  );
}
