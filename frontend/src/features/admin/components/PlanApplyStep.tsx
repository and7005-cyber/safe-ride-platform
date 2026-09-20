import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, History, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { PlanApplyDialog } from "@/features/admin/components/PlanApplyDialog";
import { api } from "@/lib/apiClient";
import { useSchoolKey } from "@/lib/queries";
import {
  ACTIVE_RUN_WARNING,
  ALREADY_APPLIED_DESCRIPTION,
  ALREADY_APPLIED_TITLE,
  RESTORE_CONFIRM_MESSAGE,
  RESTORE_GATE_KIND_LABEL,
  buildAcknowledgmentPayload,
  buildGateList,
  groupGateRows,
  isFirstApplyDiff,
  type GateRow,
  type UnplaceableEntry,
} from "@/lib/planReview";

// U9 step 4 — apply / restore (U6/U7), extracted from PlanReviewPage.
// Mounted for the whole page life (render gated on `active`) so an in-flight
// apply/restore survives a step switch exactly as it did as page-level state.
// The restore gate dialog and PlanApplyDialog render unconditionally, as they
// did at the page root — a closed dialog contributes nothing to the DOM. The
// apply/restore receipt (`applyResult`) lives in the shell: drafting again
// clears it, so the state sits where both steps can reach it.

export function PlanApplyStep({
  active,
  schoolId,
  draft,
  plans,
  review,
  students,
  unplaceable,
  runActive,
  applyResult,
  onApplyResult,
  onStepChange,
  onGenerateDraft,
}: {
  active: boolean;
  schoolId: string;
  draft: any;
  plans: any;
  review: any;
  students: any[];
  unplaceable: UnplaceableEntry[];
  runActive: boolean;
  applyResult: any | null;
  onApplyResult: (result: any | null) => void;
  onStepChange: (step: "fleet" | "draft" | "review" | "apply") => void;
  onGenerateDraft: (opts?: { skipConfirm?: boolean }) => Promise<void>;
}) {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const { toast } = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();

  const discardDraft = async () => {
    if (!draft) return;
    if (
      !(await confirm({
        title: "Discard this draft?",
        description:
          "The draft and every review edit in it are discarded. Live routes are untouched.",
        confirmLabel: "Discard draft",
      }))
    )
      return;
    try {
      await api.post(`/api/fleet-plans/${draft.id}/discard`);
      await qc.invalidateQueries({ queryKey: schoolKey("fleet-plans") });
      await qc.invalidateQueries({ queryKey: schoolKey("fleet-plan-review") });
      toast({ title: "Draft discarded" });
      onStepChange("draft");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  // --- apply / restore (U6/U7) ----------------------------------------------
  const gateRows = useMemo(() => {
    if (!draft?.basis?.students) return [];
    const live = (students as any[]).filter((s) => s.school_id === schoolId);
    return buildGateList(draft.basis.students, live);
  }, [draft, students, schoolId]);
  const gateGroups = useMemo(() => groupGateRows(gateRows), [gateRows]);

  const [applyOpen, setApplyOpen] = useState(false);
  const [confirmedKeys, setConfirmedKeys] = useState<Set<string>>(new Set());
  const [ackKeys, setAckKeys] = useState<Set<string>>(new Set());
  const [applyError, setApplyError] = useState("");
  const [applying, setApplying] = useState(false);
  const [restoring, setRestoring] = useState(false);

  // Restore's R22 gate: the server's `previous.drift` rows (departed/enrolled
  // since the capture — the same {kind, student_id, name} shape as the apply
  // gate list, computed by the same DAO helper as the gate itself), so the
  // confirmations can be assembled here instead of a blind empty POST.
  const restoreDrift = useMemo<GateRow[]>(
    () => ((plans?.previous?.drift ?? []) as GateRow[]),
    [plans],
  );
  const restoreGroups = useMemo(
    () => groupGateRows(restoreDrift, RESTORE_GATE_KIND_LABEL),
    [restoreDrift],
  );
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoreConfirmedKeys, setRestoreConfirmedKeys] = useState<Set<string>>(new Set());
  const [restoreError, setRestoreError] = useState("");

  const openApply = () => {
    setConfirmedKeys(new Set());
    setAckKeys(new Set());
    setApplyError("");
    setApplyOpen(true);
  };

  const toggleKey = (setter: typeof setConfirmedKeys) => (key: string) =>
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const applyPlan = async () => {
    if (!draft) return;
    setApplying(true);
    setApplyError("");
    try {
      const res = await api.post(`/api/fleet-plans/${draft.id}/apply`, {
        confirmations: gateRows.map((r) => ({ student_id: r.student_id, kind: r.kind })),
        acknowledgments: buildAcknowledgmentPayload(unplaceable),
      });
      setApplyOpen(false);
      if (res.already_applied) {
        // Idempotent answer (a gateway-timeout retry hitting the now-applied
        // row): no counts in the response, so no receipt card to build.
        onApplyResult(null);
        toast({ title: ALREADY_APPLIED_TITLE, description: ALREADY_APPLIED_DESCRIPTION });
      } else {
        onApplyResult({ act: "apply", ...res });
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: schoolKey("fleet-plans") }),
        qc.invalidateQueries({ queryKey: schoolKey("fleet-plan-review") }),
        qc.invalidateQueries({ queryKey: schoolKey("routes") }),
        qc.invalidateQueries({ queryKey: schoolKey("students") }),
        qc.invalidateQueries({ queryKey: schoolKey("buses") }),
      ]);
      onStepChange("apply");
    } catch (err) {
      // 409/422 gate refusals verbatim — the server names every unconfirmed
      // change and unacknowledged child (the saveError house pattern).
      setApplyError((err as Error).message);
    } finally {
      setApplying(false);
    }
  };

  const discardAndRedraft = async () => {
    // The R22 escape (>10 drift rows): the escape button IS the explicit act,
    // so the supersede confirm is skipped — one click, one fresh draft.
    setApplyOpen(false);
    await onGenerateDraft({ skipConfirm: true });
  };

  const restorePlan = async () => {
    const previous = plans?.previous;
    if (!previous) return;
    if (restoreDrift.length > 0) {
      // Roster drift since the capture: restore's R22 gate demands each row
      // confirmed by (kind, student), so open the gate dialog instead of
      // dead-ending on the 409 an empty-confirmation POST would earn.
      setRestoreConfirmedKeys(new Set());
      setRestoreError("");
      setRestoreOpen(true);
      return;
    }
    if (
      !(await confirm({
        title: "Restore the previous plan?",
        description: RESTORE_CONFIRM_MESSAGE,
        confirmLabel: "Restore",
      }))
    )
      return;
    await submitRestore([], { viaDialog: false });
  };

  const submitRestore = async (
    confirmations: Array<{ kind: string; student_id: string }>,
    opts: { viaDialog: boolean },
  ) => {
    const previous = plans?.previous;
    if (!previous) return;
    setRestoring(true);
    setRestoreError("");
    try {
      const res = await api.post(`/api/fleet-plans/${previous.id}/restore`, {
        confirmations,
        acknowledgments: [],
      });
      setRestoreOpen(false);
      if (res.already_applied) {
        // Idempotent answer (a gateway-timeout retry hitting the now-applied
        // row): the response carries no counts, so the toast claims none.
        onApplyResult(null);
        toast({ title: ALREADY_APPLIED_TITLE, description: ALREADY_APPLIED_DESCRIPTION });
      } else {
        onApplyResult({ act: "restore", ...res });
        toast({
          title: "Previous plan restored",
          description: `${res.routes_written} routes written · ${res.notified_family_count} families notified`,
        });
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: schoolKey("fleet-plans") }),
        qc.invalidateQueries({ queryKey: schoolKey("fleet-plan-review") }),
        qc.invalidateQueries({ queryKey: schoolKey("routes") }),
        qc.invalidateQueries({ queryKey: schoolKey("students") }),
        qc.invalidateQueries({ queryKey: schoolKey("buses") }),
      ]);
    } catch (err) {
      // Residual drift 409s verbatim — fleet drift (a vanished/shrunk/
      // re-claimed bus) stays string-only by design; the admin fixes the
      // fleet or re-drafts instead. Shown in the gate dialog when one is
      // open, as the toast otherwise.
      if (opts.viaDialog) setRestoreError((err as Error).message);
      else toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setRestoring(false);
    }
  };

  return (
    <>
      {active && (
        <div className="space-y-4">
          {runActive && (
            <div
              className="flex items-start gap-2 rounded-md border border-warning/60 bg-warning/10 p-3"
              data-testid="active-run-warning-page"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p className="text-sm">{ACTIVE_RUN_WARNING}</p>
            </div>
          )}

          {draft && (
            <Card>
              <CardHeader className="pb-2">
                <p className="font-heading text-lg font-semibold">Apply the draft</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Applying replaces this school's live routes in one
                  audit-logged act and notifies every affected family.
                  Changes take effect from the next run — which can be this
                  same afternoon. The displaced routes are preserved; one
                  step back exists.
                </p>
                <ul className="space-y-1 text-sm">
                  <li>
                    <span className="font-medium">
                      {review?.diff?.notified_family_count ?? 0}
                    </span>{" "}
                    families will be notified
                  </li>
                  <li>
                    <span className="font-medium">{gateRows.length}</span> changes since
                    drafting to confirm
                  </li>
                  <li>
                    <span className="font-medium">{unplaceable.length}</span> unplaceable
                    legs to acknowledge by name
                  </li>
                </ul>
                <div className="flex gap-2">
                  <Button onClick={openApply} disabled={applying} data-testid="plan-apply">
                    Apply plan…
                  </Button>
                  <Button
                    variant="outline"
                    onClick={discardDraft}
                    disabled={applying}
                    data-testid="plan-discard"
                  >
                    <Trash2 className="h-4 w-4" /> Discard draft
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {applyResult && (
            <Card data-testid="apply-result">
              <CardHeader className="pb-2">
                <p className="font-heading text-lg font-semibold">
                  {applyResult.act === "restore" ? "Plan restored" : "Plan applied"}
                </p>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p>
                  <span className="font-medium">{applyResult.routes_written}</span> routes
                  written · <span className="font-medium">{applyResult.routes_retired}</span>{" "}
                  retired ·{" "}
                  <span className="font-medium">{applyResult.notified_family_count}</span>{" "}
                  families notified
                </p>
                <p className="text-xs text-muted-foreground">
                  {(applyResult.refresh?.refreshed ?? []).length} routes refreshed with road
                  geometry
                  {(applyResult.refresh?.degraded ?? []).length > 0 &&
                    ` · ${(applyResult.refresh?.degraded ?? []).length} kept their planned times but couldn't fetch geometry — they carry a warning badge on the Routes page`}
                  .
                </p>
                <Button size="sm" variant="outline" onClick={() => navigate("/routes")}>
                  View routes
                </Button>
              </CardContent>
            </Card>
          )}

          {plans?.previous && (
            <Card>
              <CardHeader className="flex-row items-center gap-2 space-y-0 pb-2">
                <History className="h-4 w-4 text-primary" />
                <p className="font-heading text-lg font-semibold">Previous plan</p>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-sm text-muted-foreground" data-testid="restore-one-level">
                  {RESTORE_CONFIRM_MESSAGE}
                </p>
                <Button
                  variant="outline"
                  onClick={restorePlan}
                  disabled={restoring}
                  data-testid="plan-restore"
                >
                  {restoring ? "Restoring…" : "Restore previous plan"}
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Restore gate dialog: R22 for restore — roster drift since the
          preserved capture (departed/enrolled; addresses never gate restore)
          confirmed per row before the POST, the PlanApplyDialog gate
          pattern. Residual 409s (fleet drift stays string-only by design)
          surface verbatim below the list. */}
      <Dialog
        open={restoreOpen}
        onOpenChange={(o) => {
          if (!o && restoring) return;
          setRestoreOpen(o);
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto" data-testid="restore-dialog">
          <DialogHeader>
            <DialogTitle>Restore the previous plan?</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{RESTORE_CONFIRM_MESSAGE}</p>
            <div className="space-y-3" data-testid="restore-gate-list">
              <p className="text-sm font-semibold">
                The school has changed since this plan was preserved
              </p>
              <p className="text-xs text-muted-foreground">
                Confirm each change to restore the plan as preserved.
              </p>
              {restoreGroups.map((group) => (
                <div
                  key={group.kind}
                  className="space-y-1"
                  data-testid={`restore-gate-group-${group.kind}`}
                >
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {group.label}
                  </p>
                  {group.rows.map((row) => {
                    const key = `${row.kind}|${row.student_id}`;
                    return (
                      <label
                        key={key}
                        className="flex items-center gap-2 text-sm"
                        data-testid={`restore-gate-row-${row.kind}-${row.student_id}`}
                      >
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-primary"
                          checked={restoreConfirmedKeys.has(key)}
                          disabled={restoring}
                          onChange={() => toggleKey(setRestoreConfirmedKeys)(key)}
                          aria-label={`Confirm ${row.kind}: ${row.name}`}
                        />
                        <span>{row.name}</span>
                      </label>
                    );
                  })}
                </div>
              ))}
            </div>
            {restoreError && (
              <p className="text-sm text-destructive" data-testid="restore-error">
                {restoreError}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={restoring} onClick={() => setRestoreOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                // The house R22 rule: the checkboxes gate the button, never
                // the payload — every drift row is sent as a confirmation.
                submitRestore(
                  restoreDrift.map((r) => ({ kind: r.kind, student_id: r.student_id })),
                  { viaDialog: true },
                )
              }
              disabled={
                restoring ||
                !restoreDrift.every((r) =>
                  restoreConfirmedKeys.has(`${r.kind}|${r.student_id}`),
                )
              }
              data-testid="restore-confirm"
            >
              {restoring ? "Restoring…" : "Restore plan"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PlanApplyDialog
        open={applyOpen}
        onOpenChange={setApplyOpen}
        gateGroups={gateGroups}
        gateCount={gateRows.length}
        confirmedKeys={confirmedKeys}
        onToggleConfirm={toggleKey(setConfirmedKeys)}
        ackEntries={unplaceable}
        ackKeys={ackKeys}
        onToggleAck={toggleKey(setAckKeys)}
        runActive={runActive}
        firstApply={isFirstApplyDiff(review?.diff?.rows ?? [])}
        notifiedFamilyCount={review?.diff?.notified_family_count ?? 0}
        error={applyError}
        applying={applying}
        onApply={applyPlan}
        onDiscardAndRedraft={discardAndRedraft}
      />
    </>
  );
}
