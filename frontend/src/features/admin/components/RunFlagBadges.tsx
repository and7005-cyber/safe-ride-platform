import { AlertTriangle, PhoneCall } from "lucide-react";
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
  run: { no_progress?: boolean; stale?: boolean; contact_pending?: number | string | null };
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
    </>
  );
}
