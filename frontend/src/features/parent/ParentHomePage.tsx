import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Bus, CalendarX, Clock, MapPin, Phone, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { cn } from "@/lib/utils";
import { api } from "@/lib/apiClient";
import {
  PARENT_NAV,
  useChildren,
  type CancelScope,
  type ChildCancellation,
} from "@/features/parent/parentHooks";

const STATUS_VARIANT: Record<string, "secondary" | "success" | "warning" | "destructive"> = {
  "at-home": "secondary",
  "at-school": "secondary",
  "on-bus": "success",
  "dropped-off": "warning",
  absent: "destructive",
};

const STATUS_LABEL: Record<string, string> = {
  "at-home": "At home",
  "at-school": "At School",
  "on-bus": "On the bus",
  "dropped-off": "Dropped off",
  absent: "Absent",
};

// Cancel-a-Ride pure pieces (U13: R14, R18; AE4) ------------------------------

/** Pending-state chip copy per cancellation scope (R18's visible state). */
export const CANCELLATION_CHIP_LABEL: Record<CancelScope, string> = {
  morning: "AM ride cancelled",
  afternoon: "PM ride cancelled",
  day: "Rides cancelled today",
};

/** The cancel dialog's three same-day choices, in display order (R14). */
export const CANCEL_SCOPE_OPTIONS: { scope: CancelScope; label: string }[] = [
  { scope: "morning", label: "Morning" },
  { scope: "afternoon", label: "Afternoon" },
  { scope: "day", label: "Rest of day" },
];

/** Dialog copy: same-day only, and the server's day-after-completed-morning
 * rule (a 'day' cancel then records the afternoon). */
export const CANCEL_DIALOG_NOTE =
  "Cancellations apply to today only. 'Rest of day' after this morning's run cancels the afternoon ride.";

export interface WithdrawChoice {
  scope: CancelScope;
  label: string;
}

/**
 * Withdraw options for a pending cancellation, mirroring the server's
 * withdraw semantics (U5): a partial row withdraws its own scope (one
 * choice, no dialog); a merged 'day' row offers each half (DELETE downgrades
 * to the other half) and the whole day (DELETE scope 'day' removes the row
 * atomically — never two sequential half-calls, which could strand the
 * parent halfway when the second 409s). Nothing while not withdrawable —
 * eligibility beyond that single server-computed flag stays server-side.
 */
export function withdrawChoices(
  cancellation: ChildCancellation | null | undefined,
): WithdrawChoice[] {
  if (!cancellation?.withdrawable) return [];
  if (cancellation.scope === "day") {
    return [
      { scope: "morning", label: "Morning ride" },
      { scope: "afternoon", label: "Afternoon ride" },
      { scope: "day", label: "Both rides" },
    ];
  }
  return [
    {
      scope: cancellation.scope,
      label: cancellation.scope === "morning" ? "Morning ride" : "Afternoon ride",
    },
  ];
}

/**
 * Highlighted child status chip (R36). Renders the server-derived
 * `display_status` (absence- and staleness-aware) and falls back to the raw
 * operational `status` for older payloads. Solid fill, slightly larger than
 * the default badge. Shared with the Profile page's My Children card.
 */
export function ChildStatusBadge({
  child,
  className,
}: {
  child: { display_status?: string | null; status?: string | null };
  className?: string;
}) {
  const status = child.display_status ?? child.status ?? "";
  return (
    <Badge
      data-testid="child-status-badge"
      variant={STATUS_VARIANT[status] ?? "secondary"}
      className={cn("px-3 py-1 text-sm", className)}
    >
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

// Live parity: ETA is a mock ~3–13 min (KTD-10), seeded from the child id so it
// stays stable across the 5s polling re-renders.
function mockEta(childId: string): number {
  let hash = 0;
  for (let i = 0; i < childId.length; i++) hash = (hash * 31 + childId.charCodeAt(i)) | 0;
  return 3 + (Math.abs(hash) % 11);
}

export function ParentHomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: children = [], isLoading } = useChildren();

  // Cancel dialog state: the child is captured by id and re-derived from the
  // live rows each render, so the 5s poll keeps the dialog honest. No
  // pre-disabled options and no optimistic writes — the chip renders from
  // server state after invalidation, and guard rejections come back as the
  // server's parent-readable 409 detail, shown verbatim inside the dialog.
  const [cancelId, setCancelId] = useState<string | null>(null);
  const [cancelScope, setCancelScope] = useState<CancelScope | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const [withdrawId, setWithdrawId] = useState<string | null>(null);
  const [withdrawPending, setWithdrawPending] = useState(false);

  const cancelChild = (children as any[]).find((c) => c.id === cancelId) ?? null;
  const withdrawChild = (children as any[]).find((c) => c.id === withdrawId) ?? null;
  const withdrawDialogChoices = withdrawChild ? withdrawChoices(withdrawChild.cancellation) : [];

  // When the poll invalidates an open withdraw dialog (co-parent withdrew,
  // rides no longer withdrawable), drop the captured id too — otherwise a
  // later cancellation for the same child would re-open the dialog uninvited.
  useEffect(() => {
    if (withdrawId && !withdrawPending && withdrawDialogChoices.length === 0) {
      setWithdrawId(null);
    }
  }, [withdrawId, withdrawPending, withdrawDialogChoices.length]);

  const openCancel = (child: any) => {
    setCancelScope(null);
    setCancelError(null);
    setCancelId(child.id);
  };

  const submitCancel = async () => {
    if (!cancelId || !cancelScope) return;
    setCancelPending(true);
    setCancelError(null);
    try {
      await api.post("/api/parent-portal/cancel-ride", {
        student_id: cancelId,
        scope: cancelScope,
      });
      await queryClient.invalidateQueries({ queryKey: ["parent-children"] });
      setCancelId(null);
    } catch (err) {
      // 404/409/429 detail verbatim — the copy is written for parents; the
      // dialog stays open so they can pick a different scope or back out.
      setCancelError((err as Error).message);
    } finally {
      setCancelPending(false);
    }
  };

  const performWithdraw = async (studentId: string, scope: CancelScope) => {
    setWithdrawPending(true);
    try {
      await api.del("/api/parent-portal/cancel-ride", { student_id: studentId, scope });
      await queryClient.invalidateQueries({ queryKey: ["parent-children"] });
      setWithdrawId(null);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setWithdrawPending(false);
    }
  };

  const clickWithdraw = (child: any) => {
    const choices = withdrawChoices(child.cancellation);
    // One honest choice (partial row): withdraw it directly. A merged 'day'
    // row opens the small dialog to pick the half (or both).
    if (choices.length === 1) void performWithdraw(child.id, choices[0].scope);
    else if (choices.length > 1) setWithdrawId(child.id);
  };

  const firstName = children[0]?.parent_name?.split(" ")[0] ?? "Parent";
  const etas = useMemo(
    () => Object.fromEntries((children as any[]).map((c) => [c.id, mockEta(c.id)])),
    [children],
  );
  // "Call Driver" global action dials the first child that has a bus + driver phone.
  const callChild = (children as any[]).find((c) => c.bus_name && c.driver_phone);

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="SafeRide Parent">
      <div className="space-y-4">
        <div>
          <h2 className="font-heading text-xl font-bold">Good morning, {firstName} 👋</h2>
          <p className="text-sm text-muted-foreground">Today's bus status at a glance</p>
        </div>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : children.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No children are linked to your account yet.</CardContent></Card>
        ) : (
          <>
            {children.map((child: any) => {
              // The derived display_status is the trustworthy state (R36):
              // the mock ETA only shows while the child is really on a bus.
              const onBus = (child.display_status ?? child.status) === "on-bus";
              const cancellation = child.cancellation as ChildCancellation | null | undefined;
              const canWithdraw = withdrawChoices(cancellation).length > 0;
              return (
                <Card key={child.id}>
                  <CardContent className="space-y-3 p-4">
                    <div className="flex items-start gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-secondary text-sm font-semibold text-secondary-foreground">
                        {initials(child.name)}
                      </span>
                      <div className="flex-1">
                        <p className="font-heading text-base font-semibold">{child.name}</p>
                        <p className="text-sm text-muted-foreground">
                          {child.grade ?? ""}{child.bus_name ? ` • ${child.bus_name}` : ""}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <ChildStatusBadge child={child} />
                        {cancellation && (
                          <Badge variant="warning" data-testid="cancellation-chip">
                            {CANCELLATION_CHIP_LABEL[cancellation.scope] ?? "Ride cancelled"}
                          </Badge>
                        )}
                        {canWithdraw && (
                          <button
                            type="button"
                            className="text-xs font-medium text-primary underline underline-offset-2 disabled:opacity-50"
                            disabled={withdrawPending}
                            onClick={() => clickWithdraw(child)}
                            data-testid="withdraw-cancellation"
                          >
                            Withdraw
                          </button>
                        )}
                      </div>
                    </div>

                    {onBus && (
                      <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Clock className="h-4 w-4" /> Arriving in ~{etas[child.id]} min
                      </p>
                    )}

                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Bus className="h-4 w-4" /> Driver: {child.bus_name ? (child.driver_name ?? "—") : "Not assigned"}
                    </p>

                    <div className="grid grid-cols-2 gap-2">
                      {/* Always offered — eligibility is run-state-dependent and
                          owned by the server guards, never pre-judged here. */}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => openCancel(child)}
                        data-testid="cancel-ride-button"
                      >
                        <CalendarX className="h-4 w-4" /> Cancel ride
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => navigate("/parent/track")}>
                        <MapPin className="h-4 w-4" /> Track
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}

            <div className="grid grid-cols-2 gap-3">
              <Card className="cursor-pointer transition-colors hover:bg-muted/50" onClick={() => navigate("/parent/alerts")}>
                <CardContent className="flex flex-col items-center gap-1 py-5">
                  <TriangleAlert className="h-5 w-5 text-warning" />
                  <span className="text-sm font-medium">Alerts</span>
                </CardContent>
              </Card>
              {callChild ? (
                <a href={`tel:${callChild.driver_phone}`} className="block">
                  <Card className="cursor-pointer transition-colors hover:bg-muted/50">
                    <CardContent className="flex flex-col items-center gap-1 py-5">
                      <Phone className="h-5 w-5 text-primary" />
                      <span className="text-sm font-medium">Call Driver</span>
                    </CardContent>
                  </Card>
                </a>
              ) : (
                <Card className="opacity-50">
                  <CardContent className="flex flex-col items-center gap-1 py-5">
                    <Phone className="h-5 w-5 text-muted-foreground" />
                    <span className="text-sm font-medium">Call Driver</span>
                  </CardContent>
                </Card>
              )}
            </div>
          </>
        )}
      </div>

      <Dialog
        open={Boolean(cancelChild)}
        onOpenChange={(o) => {
          if (!o && !cancelPending) setCancelId(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="pr-6">Cancel a ride — {cancelChild?.name}</DialogTitle>
            <DialogDescription>{CANCEL_DIALOG_NOTE}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2" role="radiogroup" aria-label="Which ride to cancel">
            {CANCEL_SCOPE_OPTIONS.map((opt) => (
              <Button
                key={opt.scope}
                type="button"
                role="radio"
                aria-checked={cancelScope === opt.scope}
                variant={cancelScope === opt.scope ? "default" : "outline"}
                disabled={cancelPending}
                onClick={() => setCancelScope(opt.scope)}
                data-testid={`cancel-scope-${opt.scope}`}
              >
                {opt.label}
              </Button>
            ))}
          </div>
          {cancelError && (
            <p className="text-sm font-medium text-destructive" data-testid="cancel-error">
              {cancelError}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={cancelPending} onClick={() => setCancelId(null)}>
              Keep the ride
            </Button>
            <Button
              disabled={cancelPending || !cancelScope}
              onClick={submitCancel}
              data-testid="cancel-confirm"
            >
              {cancelPending ? "Cancelling…" : "Cancel ride"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(withdrawChild) && withdrawDialogChoices.length > 0}
        onOpenChange={(o) => {
          if (!o && !withdrawPending) setWithdrawId(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="pr-6">Withdraw cancellation — {withdrawChild?.name}</DialogTitle>
            <DialogDescription>Choose which cancelled ride to restore.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            {withdrawDialogChoices.map((choice) => (
              <Button
                key={choice.scope}
                type="button"
                variant="outline"
                disabled={withdrawPending}
                onClick={() => void performWithdraw(withdrawChild.id, choice.scope)}
                data-testid={`withdraw-${choice.scope}`}
              >
                {choice.label}
              </Button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </RoleMobileLayout>
  );
}
