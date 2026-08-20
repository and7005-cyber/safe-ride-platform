import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ACTIVE_RUN_WARNING,
  FIRST_APPLY_MESSAGE,
  UNPLACEABLE_CONSEQUENCE,
  ackKey,
  legLabel,
  showDiscardEscape,
  type GateGroup,
  type UnplaceableEntry,
} from "@/lib/planReview";

// U9 — the apply gates as one explicit dialog:
//  * R22: the basis-drift list grouped by kind, each row individually
//    confirmed, with a "Discard and re-draft" escape when the list is long
//    (strictly more than GATE_ESCAPE_THRESHOLD rows — the documented design
//    decision: at that size per-row confirmation is busywork and the honest
//    act is a fresh draft).
//  * R23: every unplaceable child acknowledged BY NAME and leg, with the
//    same-day consequence stated.
//  * The active-run warning (System-Wide Impact): parent live-tracking
//    desynchronizes until the running run ends; changes take effect from the
//    next run — which can be this same afternoon.
// The server re-checks every gate under its locks; a mismatch surfaces here
// verbatim via `error` (the FleetMapPage saveError pattern).

export function PlanApplyDialog({
  open,
  onOpenChange,
  gateGroups,
  gateCount,
  confirmedKeys,
  onToggleConfirm,
  ackEntries,
  ackKeys,
  onToggleAck,
  runActive,
  firstApply,
  notifiedFamilyCount,
  error,
  applying,
  onApply,
  onDiscardAndRedraft,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  gateGroups: GateGroup[];
  gateCount: number;
  confirmedKeys: Set<string>;
  onToggleConfirm: (key: string) => void;
  ackEntries: UnplaceableEntry[];
  ackKeys: Set<string>;
  onToggleAck: (key: string) => void;
  runActive: boolean;
  firstApply: boolean;
  notifiedFamilyCount: number;
  error: string;
  applying: boolean;
  onApply: () => void;
  onDiscardAndRedraft: () => void;
}) {
  const allConfirmed = gateGroups.every((g) =>
    g.rows.every((r) => confirmedKeys.has(`${r.kind}|${r.student_id}`)),
  );
  const allAcked = ackEntries.every((u) => ackKeys.has(ackKey(u.student_id, u.leg)));

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && applying) return;
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto" data-testid="apply-dialog">
        <DialogHeader>
          <DialogTitle>Apply this plan?</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Applying replaces this school's live routes in one act and notifies
            every affected family ({notifiedFamilyCount}{" "}
            {notifiedFamilyCount === 1 ? "family" : "families"}). The previous
            routes are preserved and can be restored — one step back.
          </p>
          {firstApply && (
            <p className="text-sm font-medium" data-testid="apply-first-apply-copy">
              {FIRST_APPLY_MESSAGE}
            </p>
          )}

          {runActive && (
            <div
              className="flex items-start gap-2 rounded-md border border-warning/60 bg-warning/10 p-3"
              data-testid="active-run-warning"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-sm">{ACTIVE_RUN_WARNING}</p>
            </div>
          )}

          {gateCount > 0 && (
            <div className="space-y-3" data-testid="gate-list">
              <p className="text-sm font-semibold">
                The school has changed since this draft was generated
              </p>
              <p className="text-xs text-muted-foreground">
                Confirm each change to apply the plan as drafted, or discard and
                re-draft against today's school.
              </p>
              {gateGroups.map((group) => (
                <div key={group.kind} className="space-y-1" data-testid={`gate-group-${group.kind}`}>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {group.label}
                  </p>
                  {group.rows.map((row) => {
                    const key = `${row.kind}|${row.student_id}`;
                    return (
                      <label
                        key={key}
                        className="flex items-center gap-2 text-sm"
                        data-testid={`gate-row-${row.kind}-${row.student_id}`}
                      >
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-primary"
                          checked={confirmedKeys.has(key)}
                          disabled={applying}
                          onChange={() => onToggleConfirm(key)}
                          aria-label={`Confirm ${row.kind}: ${row.name}`}
                        />
                        <span>{row.name}</span>
                      </label>
                    );
                  })}
                </div>
              ))}
              {showDiscardEscape(gateCount) && (
                <Button
                  variant="outline"
                  disabled={applying}
                  onClick={onDiscardAndRedraft}
                  data-testid="discard-redraft"
                >
                  Discard and re-draft
                </Button>
              )}
            </div>
          )}

          {ackEntries.length > 0 && (
            <div className="space-y-1.5" data-testid="ack-list">
              <p className="text-sm font-semibold">
                Unplaceable children — acknowledge each by name
              </p>
              <p className="text-xs text-muted-foreground">{UNPLACEABLE_CONSEQUENCE}</p>
              {ackEntries.map((u) => {
                const key = ackKey(u.student_id, u.leg);
                return (
                  <label
                    key={key}
                    className="flex items-center gap-2 text-sm"
                    data-testid={`ack-row-${u.student_id}-${u.leg}`}
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-primary"
                      checked={ackKeys.has(key)}
                      disabled={applying}
                      onChange={() => onToggleAck(key)}
                      aria-label={`Acknowledge ${u.name ?? u.student_id} — ${legLabel(u.leg)}`}
                    />
                    <span>
                      {u.name ?? u.student_id}
                      <span className="text-xs text-muted-foreground">
                        {" "}
                        — {legLabel(u.leg)} ({u.constraint})
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          )}

          {error && (
            <p className="text-sm text-destructive" data-testid="apply-error">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={applying} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={onApply}
            disabled={applying || !allConfirmed || !allAcked}
            data-testid="apply-confirm"
          >
            {applying ? "Applying…" : "Apply plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
