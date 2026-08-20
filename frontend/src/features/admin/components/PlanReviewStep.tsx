import { useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { PlanBusRoster, type ReviewBus, type ReviewStudent } from "@/features/admin/components/PlanBusRoster";
import { PlanDiffPanel } from "@/features/admin/components/PlanDiffPanel";
import { PlanDraftMap, type PlanMapBus } from "@/features/admin/components/PlanDraftMap";
import { PlanUnplaceablePanel } from "@/features/admin/components/PlanUnplaceablePanel";
import { api } from "@/lib/apiClient";
import {
  PLAN_LEGS,
  RESOLVE_CONFIRM_MESSAGE,
  fmtDriving,
  fmtRide,
  legLabel,
  type UnplaceableEntry,
} from "@/lib/planReview";

// U9 step 3 — the review surface (map, rosters, U5 edit verbs, move/place
// dialogs), extracted from PlanReviewPage. Mounted for the whole page life
// (render gated on `active`) so the chosen map leg, dialog state, and the
// request-generation guard survive step switches exactly as they did as
// page-level state. The move/place dialogs render unconditionally, as they
// did at the page root — a closed dialog contributes nothing to the DOM.

// Distinct colours per bus, deterministic by sorted id (FleetMapPage palette).
const PALETTE = [
  "#2f6f4f", "#2563eb", "#dc2626", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#475569", "#ea580c",
];

export function PlanReviewStep({
  active,
  draft,
  review,
  school,
  unplaceable,
  onContinueToApply,
}: {
  active: boolean;
  draft: any;
  review: any;
  school: any;
  unplaceable: UnplaceableEntry[];
  onContinueToApply: () => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();

  // --- review edits (U5 verbs) ----------------------------------------------
  // Request generation (the FleetMapPage stale-guard): every edit bumps the
  // counter; a response applies its invalidation/toast only while it is still
  // the newest request, so a superseded round-trip can neither clobber fresher
  // data nor raise a stale error. The busy flag disables every edit control —
  // a double-fired reorder would echo a stale full-order payload.
  const requestGeneration = useRef(0);
  const [editBusy, setEditBusy] = useState(false);

  const runEdit = async (fn: () => Promise<any>): Promise<boolean> => {
    const generation = ++requestGeneration.current;
    setEditBusy(true);
    try {
      await fn();
      if (generation !== requestGeneration.current) return true;
      await qc.invalidateQueries({ queryKey: ["fleet-plan-review"] });
      await qc.invalidateQueries({ queryKey: ["fleet-plans"] });
      return true;
    } catch (err) {
      if (generation !== requestGeneration.current) return false;
      // 422s name the violated constraint ('capacity' / 'stop cap') verbatim.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
      return false;
    } finally {
      setEditBusy(false);
    }
  };

  const planId = review?.plan?.id ?? draft?.id;

  const reorder = (busId: string, leg: string, order: string[]) =>
    runEdit(() => api.post(`/api/fleet-plans/${planId}/reorder`, { bus_id: busId, leg, order }));

  const changePattern = (studentId: string, pattern: string) => {
    if (patternOf(studentId) === pattern) return;
    runEdit(() => api.post(`/api/fleet-plans/${planId}/pattern`, { student_id: studentId, pattern }));
  };

  const togglePin = (student: ReviewStudent, isPinned: boolean) => {
    if (isPinned) {
      runEdit(() =>
        api.post(`/api/fleet-plans/${planId}/pin`, { student_id: student.id, unpin: "all" }),
      );
      return;
    }
    // Pin the child to their CURRENT bus per placed leg, so re-solve keeps
    // exactly the arrangement the admin is looking at.
    const busMap: Record<string, string> = {};
    for (const bus of (review?.buses ?? []) as ReviewBus[]) {
      for (const leg of PLAN_LEGS) {
        for (const stop of bus.legs?.[leg]?.stops ?? []) {
          if (stop.students.some((s) => s.id === student.id)) busMap[leg] = bus.bus_id;
        }
      }
    }
    if (Object.keys(busMap).length === 0) return;
    runEdit(() =>
      api.post(`/api/fleet-plans/${planId}/pin`, { student_id: student.id, bus: busMap }),
    );
  };

  // Move dialog (both legs by default; a one-leg move creates a split).
  const [moveTarget, setMoveTarget] = useState<
    { student: ReviewStudent; fromBusId: string } | null
  >(null);
  const [moveBusId, setMoveBusId] = useState("");
  const [moveLegs, setMoveLegs] = useState<"both" | "morning" | "afternoon">("both");

  const openMove = (student: ReviewStudent, fromBusId: string) => {
    setMoveTarget({ student, fromBusId });
    const other = (review?.buses ?? []).find((b: ReviewBus) => b.bus_id !== fromBusId);
    setMoveBusId(other?.bus_id ?? fromBusId);
    setMoveLegs("both");
  };

  const saveMove = async () => {
    if (!moveTarget) return;
    const ok = await runEdit(() =>
      api.post(`/api/fleet-plans/${planId}/move`, {
        student_id: moveTarget.student.id,
        to_bus_id: moveBusId,
        legs: moveLegs === "both" ? null : [moveLegs],
      }),
    );
    if (ok) setMoveTarget(null);
  };

  // Place dialog (documented design decision: the unplaceable→placed path is
  // a per-row "Place" action opening a small dialog with bus + position
  // selects per pattern leg, backed by POST /{plan}/assign).
  const [placeTarget, setPlaceTarget] = useState<
    { student_id: string; name: string; legs: string[] } | null
  >(null);
  const [placeBusId, setPlaceBusId] = useState("");
  const [placePositions, setPlacePositions] = useState<Record<string, string>>({});

  const openPlace = (entry: UnplaceableEntry) => {
    const legs = unplaceable
      .filter((u) => u.student_id === entry.student_id)
      .map((u) => u.leg);
    setPlaceTarget({
      student_id: entry.student_id,
      name: entry.name ?? entry.student_id,
      legs,
    });
    setPlaceBusId((review?.buses ?? [])[0]?.bus_id ?? "");
    setPlacePositions({});
  };

  const savePlace = async () => {
    if (!placeTarget) return;
    const positions: Record<string, number> = {};
    for (const [leg, value] of Object.entries(placePositions)) {
      if (value !== "end") positions[leg] = Number(value);
    }
    const ok = await runEdit(() =>
      api.post(`/api/fleet-plans/${planId}/assign`, {
        student_id: placeTarget.student_id,
        bus_id: placeBusId,
        legs: placeTarget.legs,
        position: Object.keys(positions).length > 0 ? positions : null,
      }),
    );
    if (ok) setPlaceTarget(null);
  };

  const resolveDraft = async () => {
    if (
      !(await confirm({
        title: "Re-solve this draft?",
        description: RESOLVE_CONFIRM_MESSAGE,
        confirmLabel: "Re-solve",
        destructive: false,
      }))
    )
      return;
    runEdit(() => api.post(`/api/fleet-plans/${planId}/resolve`));
  };

  // --- derived review surface ------------------------------------------------
  const colorMap = useMemo(() => {
    const ids = ((review?.buses ?? []) as ReviewBus[]).map((b) => b.bus_id).sort();
    const m: Record<string, string> = {};
    ids.forEach((id, i) => {
      m[id] = PALETTE[i % PALETTE.length];
    });
    return m;
  }, [review]);

  const basisPattern = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of draft?.basis?.students ?? []) m.set(String(s.id), s.pattern ?? "both_ways");
    return m;
  }, [draft]);

  const patternOf = (studentId: string): string =>
    review?.patterns?.[studentId] ?? basisPattern.get(studentId) ?? "both_ways";

  const pinnedOf = (studentId: string): boolean => Boolean(review?.pins?.[studentId]);

  const [mapLeg, setMapLeg] = useState("morning");
  const mapBuses: PlanMapBus[] = useMemo(
    () =>
      ((review?.buses ?? []) as ReviewBus[]).map((b) => ({
        bus_id: b.bus_id,
        bus_name: b.bus_name,
        color: colorMap[b.bus_id] ?? PALETTE[0],
        stops: (b.legs?.[mapLeg]?.stops ?? []).map((s) => ({
          key: s.key,
          name: s.name,
          lat: s.lat,
          lng: s.lng,
        })),
      })),
    [review, colorMap, mapLeg],
  );

  const objective: number[] | null = review?.objective ?? null;

  return (
    <>
      {active && draft && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1">
              {PLAN_LEGS.map((leg) => (
                <Button
                  key={leg}
                  size="sm"
                  variant={mapLeg === leg ? "default" : "outline"}
                  onClick={() => setMapLeg(leg)}
                  data-testid={`map-leg-${leg}`}
                >
                  {legLabel(leg)}
                </Button>
              ))}
            </div>
            {objective && (
              <span className="text-sm text-muted-foreground" data-testid="plan-objective">
                Worst ride {fmtRide(objective[0])} · Total ride {fmtRide(objective[1])} ·
                Driving {fmtDriving(review?.total_driving_seconds)}
              </span>
            )}
            <span className="ml-auto flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={editBusy}
                onClick={resolveDraft}
                data-testid="plan-resolve"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Re-solve
              </Button>
              <Button size="sm" onClick={onContinueToApply} data-testid="plan-to-apply">
                Continue to apply
              </Button>
            </span>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <PlanDraftMap
                buses={mapBuses}
                leg={mapLeg}
                school={
                  school?.lat != null && school?.lng != null
                    ? { lat: school.lat, lng: school.lng }
                    : null
                }
              />
            </div>
            <div className="space-y-4">
              <PlanUnplaceablePanel
                entries={unplaceable}
                busy={editBusy}
                onPlace={openPlace}
              />
              <PlanDiffPanel
                rows={review?.diff?.rows ?? []}
                notifiedFamilyCount={review?.diff?.notified_family_count ?? 0}
              />
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {((review?.buses ?? []) as ReviewBus[]).map((bus) => (
              <PlanBusRoster
                key={bus.bus_id}
                bus={bus}
                color={colorMap[bus.bus_id] ?? PALETTE[0]}
                busy={editBusy}
                pinned={pinnedOf}
                patternOf={patternOf}
                onReorder={reorder}
                onMove={(student, fromBusId) => openMove(student, fromBusId)}
                onPinToggle={togglePin}
                onPatternChange={changePattern}
              />
            ))}
          </div>
        </div>
      )}

      {/* Move-to-bus dialog: both legs by default, one-leg move = split. */}
      <Dialog
        open={!!moveTarget}
        onOpenChange={(o) => {
          if (!o && !editBusy) setMoveTarget(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Move {moveTarget?.student.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>To bus</Label>
              <Select value={moveBusId} onValueChange={setMoveBusId}>
                <SelectTrigger data-testid="move-bus-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {((review?.buses ?? []) as ReviewBus[]).map((b) => (
                    <SelectItem key={b.bus_id} value={b.bus_id}>
                      {b.bus_name ?? b.bus_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>Legs</Label>
              <Select value={moveLegs} onValueChange={(v) => setMoveLegs(v as typeof moveLegs)}>
                <SelectTrigger data-testid="move-legs-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="both">Both legs (default)</SelectItem>
                  <SelectItem value="morning">Morning only — creates a split</SelectItem>
                  <SelectItem value="afternoon">Afternoon only — creates a split</SelectItem>
                </SelectContent>
              </Select>
              {moveLegs !== "both" && (
                <p className="text-xs text-muted-foreground">
                  A one-leg move puts the child on different buses per leg and
                  marks their pattern as split.
                </p>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={editBusy} onClick={() => setMoveTarget(null)}>
              Cancel
            </Button>
            <Button onClick={saveMove} disabled={editBusy || !moveBusId} data-testid="move-save">
              Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Place dialog: the unplaceable→placed path (assign endpoint). */}
      <Dialog
        open={!!placeTarget}
        onOpenChange={(o) => {
          if (!o && !editBusy) setPlaceTarget(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Place {placeTarget?.name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>Bus</Label>
              <Select value={placeBusId} onValueChange={setPlaceBusId}>
                <SelectTrigger data-testid="place-bus-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {((review?.buses ?? []) as ReviewBus[]).map((b) => (
                    <SelectItem key={b.bus_id} value={b.bus_id}>
                      {b.bus_name ?? b.bus_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {placeTarget?.legs.map((leg) => {
              const stops =
                ((review?.buses ?? []) as ReviewBus[]).find((b) => b.bus_id === placeBusId)
                  ?.legs?.[leg]?.stops ?? [];
              return (
                <div key={leg} className="space-y-1">
                  <Label>{legLabel(leg)} position</Label>
                  <Select
                    value={placePositions[leg] ?? "end"}
                    onValueChange={(v) =>
                      setPlacePositions((prev) => ({ ...prev, [leg]: v }))
                    }
                  >
                    <SelectTrigger data-testid={`place-position-${leg}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="end">At the end</SelectItem>
                      {stops.map((s, i) => (
                        <SelectItem key={s.key} value={String(i)}>
                          Before stop {i + 1} — {s.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              );
            })}
            <p className="text-xs text-muted-foreground">
              Placing checks the same hard constraints as every review edit —
              a full bus or an over-cap leg refuses with the constraint named.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={editBusy} onClick={() => setPlaceTarget(null)}>
              Cancel
            </Button>
            <Button onClick={savePlace} disabled={editBusy || !placeBusId} data-testid="place-save">
              Place
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
