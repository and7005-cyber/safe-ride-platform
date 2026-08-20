import { useMemo } from "react";
import { Diff } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  FIRST_APPLY_MESSAGE,
  isFirstApplyDiff,
  legLabel,
  shapeDiff,
  type PlanDiffRow,
} from "@/lib/planReview";

// U9 — the R24 diff-vs-live panel, fed by the shared server diff (the same
// function apply's fan-out consumes, so this preview and the actual sends
// cannot drift): children changing bus, stops moving ≥5 minutes against what
// families were told, newly unplaceable children, removed legs, and the
// notified-family count.

function Section({
  title,
  rows,
  render,
}: {
  title: string;
  rows: PlanDiffRow[];
  render: (row: PlanDiffRow) => string;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title} ({rows.length})
      </p>
      <ul className="space-y-0.5">
        {rows.map((r) => (
          <li key={`${r.student_id}-${r.leg}-${title}`} className="text-sm">
            <span className="font-medium">{r.student_name}</span>{" "}
            <span className="text-xs text-muted-foreground">
              {legLabel(r.leg)} — {render(r)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PlanDiffPanel({
  rows,
  notifiedFamilyCount,
}: {
  rows: PlanDiffRow[];
  notifiedFamilyCount: number;
}) {
  const shaped = useMemo(() => shapeDiff(rows), [rows]);
  const firstApply = isFirstApplyDiff(rows);

  return (
    <Card data-testid="diff-panel">
      <CardHeader className="flex-row items-center gap-2 space-y-0 pb-2">
        <Diff className="h-4 w-4 shrink-0 text-primary" />
        <p className="font-heading text-base font-semibold">Changes vs live routes</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm" data-testid="notified-count">
          <span className="font-semibold">{notifiedFamilyCount}</span>{" "}
          {notifiedFamilyCount === 1 ? "family" : "families"} will be notified when
          this plan is applied.
        </p>
        {firstApply && (
          <p className="text-sm text-muted-foreground" data-testid="first-apply-copy">
            {FIRST_APPLY_MESSAGE}
          </p>
        )}
        {rows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No material changes — no family is notified.
          </p>
        )}
        {!firstApply && (
          <>
            <Section
              title="Bus changes"
              rows={shaped.busChanges}
              render={(r) =>
                `now on ${r.current?.bus_name ?? "another bus"}`
              }
            />
            <Section
              title="Stop time moves (5 min or more)"
              rows={shaped.timeMoves}
              render={(r) =>
                `${r.baseline?.scheduled_time ?? "—"} → ${r.current?.scheduled_time ?? "—"}`
              }
            />
            <Section
              title="Stop place changes"
              rows={shaped.placeChanges}
              render={(r) => `now at ${r.current?.stop_name ?? "a new stop"}`}
            />
            <Section
              title="First communications"
              rows={shaped.firstCommunications}
              render={(r) =>
                `${r.current?.stop_name ?? "stop"} at ${r.current?.scheduled_time ?? "—"}`
              }
            />
            <Section
              title="Newly unplaceable"
              rows={shaped.newlyUnplaceable}
              render={() => "loses their seat — acknowledged by name at apply"}
            />
            <Section
              title="Legs removed"
              rows={shaped.legRemoved}
              render={() => "this leg is no longer served"}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}
