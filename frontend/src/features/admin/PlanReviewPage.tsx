import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, History, RotateCcw, Trash2, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
import { PageHeader } from "@/features/admin/components/PageHeader";
import { PlanApplyDialog } from "@/features/admin/components/PlanApplyDialog";
import { PlanBusRoster, type ReviewBus, type ReviewStudent } from "@/features/admin/components/PlanBusRoster";
import { PlanDiffPanel } from "@/features/admin/components/PlanDiffPanel";
import { PlanDraftMap, type PlanMapBus } from "@/features/admin/components/PlanDraftMap";
import { PlanUnplaceablePanel } from "@/features/admin/components/PlanUnplaceablePanel";
import { api } from "@/lib/apiClient";
import {
  ACTIVE_RUN_WARNING,
  PLAN_DEGRADED_MESSAGE,
  PLAN_LEGS,
  RESOLVE_CONFIRM_MESSAGE,
  RESTORE_CONFIRM_MESSAGE,
  buildAcknowledgmentPayload,
  buildGateList,
  flattenUnplaceable,
  fmtDriving,
  fmtRide,
  groupGateRows,
  isFirstApplyDiff,
  legLabel,
  type UnplaceableEntry,
} from "@/lib/planReview";
import {
  useActiveRuns,
  useBuses,
  useFleetPlans,
  usePlanReview,
  useRoutes,
  useSchools,
  useStudents,
} from "@/lib/queries";

// U9 — the plan review surface (F1–F4): a stepper of fleet confirmation →
// draft → review → apply over the U4–U7 endpoints. The Fleet Map planner
// stays as-is; this page is the students-first path. Every gate the server
// enforces is assembled client-side first (lib/planReview) so the dialogs
// show exactly what the payload will claim, and the server's own re-check
// under its locks answers any drift verbatim.

// Distinct colours per bus, deterministic by sorted id (FleetMapPage palette).
const PALETTE = [
  "#2f6f4f", "#2563eb", "#dc2626", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#475569", "#ea580c",
];

type Step = "fleet" | "draft" | "review" | "apply";

const STEPS: Array<{ key: Step; label: string }> = [
  { key: "fleet", label: "1 · Fleet" },
  { key: "draft", label: "2 · Draft" },
  { key: "review", label: "3 · Review" },
  { key: "apply", label: "4 · Apply" },
];

export function PlanReviewPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();

  const { data: schools = [] } = useSchools();
  const { data: buses = [] } = useBuses();
  const { data: students = [] } = useStudents();
  const { data: routes = [] } = useRoutes();
  const { data: activeRuns = [] } = useActiveRuns();

  // --- school scope ---------------------------------------------------------
  const [schoolId, setSchoolId] = useState("");
  useEffect(() => {
    // A single-school deployment needs no selector round-trip.
    if (!schoolId && (schools as any[]).length === 1) setSchoolId((schools as any[])[0].id);
  }, [schools, schoolId]);
  const school = (schools as any[]).find((s) => s.id === schoolId) ?? null;

  const plansQ = useFleetPlans(schoolId || null);
  const plans = plansQ.data;
  const draft = plans?.draft ?? null;
  const reviewQ = usePlanReview(schoolId || null, Boolean(draft));
  const review = reviewQ.data;

  // --- stepper --------------------------------------------------------------
  const [step, setStep] = useState<Step>("fleet");
  const landed = useRef<string | null>(null);
  useEffect(() => {
    // On first load (per school): land on Review when a draft already exists.
    if (!plans || landed.current === schoolId) return;
    landed.current = schoolId;
    setStep(plans.draft ? "review" : "fleet");
  }, [plans, schoolId]);

  const stepEnabled = (key: Step): boolean => {
    if (!schoolId) return false;
    if (key === "review") return Boolean(draft);
    if (key === "apply") return Boolean(draft || plans?.applied || plans?.previous);
    return true;
  };

  // --- fleet confirmation (F1 step 1) ---------------------------------------
  const [fleetSel, setFleetSel] = useState<Set<string>>(new Set());
  const [fleetNotices, setFleetNotices] = useState<any[]>([]);
  const [confirmingFleet, setConfirmingFleet] = useState(false);
  const seededFleet = useRef<string | null>(null);
  useEffect(() => {
    if (!schoolId || (buses as any[]).length === 0) return;
    if (seededFleet.current === schoolId) return;
    seededFleet.current = schoolId;
    setFleetSel(
      new Set((buses as any[]).filter((b) => b.school_id === schoolId).map((b) => b.id)),
    );
    setFleetNotices([]);
  }, [schoolId, buses]);

  const toggleBus = (busId: string) =>
    setFleetSel((prev) => {
      const next = new Set(prev);
      if (next.has(busId)) next.delete(busId);
      else next.add(busId);
      return next;
    });

  const confirmFleet = async () => {
    setConfirmingFleet(true);
    try {
      const res = await api.post("/api/fleet-plans/confirm-fleet", {
        school_id: schoolId,
        bus_ids: [...fleetSel],
      });
      setFleetNotices(res.notices ?? []);
      await qc.invalidateQueries({ queryKey: ["buses"] });
      toast({
        title: `Fleet confirmed — ${res.buses.length} bus${res.buses.length === 1 ? "" : "es"} claimed`,
      });
      setStep("draft");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setConfirmingFleet(false);
    }
  };

  // --- draft generation (F1 step 2 / F4) ------------------------------------
  const [seed, setSeed] = useState("");
  const [drafting, setDrafting] = useState(false);

  const generateDraft = async (opts?: { skipConfirm?: boolean }) => {
    const supersede = Boolean(draft);
    if (supersede && !opts?.skipConfirm) {
      const ok = await confirm({
        title: "Start a new draft?",
        description:
          "An open draft already exists for this school. Drafting again supersedes " +
          "it — its review edits are discarded and cannot be recovered.",
        confirmLabel: "Supersede and re-draft",
      });
      if (!ok) return;
    }
    setDrafting(true);
    try {
      const res = await api.post("/api/fleet-plans/draft", {
        school_id: schoolId,
        seed: seed.trim() === "" ? null : Number(seed),
        supersede,
      });
      await qc.invalidateQueries({ queryKey: ["fleet-plans"] });
      await qc.invalidateQueries({ queryKey: ["fleet-plan-review"] });
      if (res.degraded) toast({ title: PLAN_DEGRADED_MESSAGE });
      setApplyResult(null);
      setStep("review");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setDrafting(false);
    }
  };

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

  const unplaceable = useMemo(
    () => flattenUnplaceable(review?.unplaceable),
    [review],
  );

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
      await qc.invalidateQueries({ queryKey: ["fleet-plans"] });
      await qc.invalidateQueries({ queryKey: ["fleet-plan-review"] });
      toast({ title: "Draft discarded" });
      setStep("draft");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
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

  const degraded = Boolean(review?.plan?.degraded ?? draft?.degraded);
  const objective: number[] | null = review?.objective ?? null;

  // A run in progress for THIS school (System-Wide Impact): the admin runs
  // list joined to routes the way other admin pages read it.
  const runActive = useMemo(() => {
    const routeSchool = new Map((routes as any[]).map((r) => [r.id, r.school_id]));
    return (activeRuns as any[]).some(
      (r) => r.route_id && routeSchool.get(r.route_id) === schoolId,
    );
  }, [routes, activeRuns, schoolId]);

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
  const [applyResult, setApplyResult] = useState<any | null>(null);
  const [restoring, setRestoring] = useState(false);

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
      setApplyResult({ act: "apply", ...res });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["fleet-plans"] }),
        qc.invalidateQueries({ queryKey: ["fleet-plan-review"] }),
        qc.invalidateQueries({ queryKey: ["routes"] }),
        qc.invalidateQueries({ queryKey: ["students"] }),
        qc.invalidateQueries({ queryKey: ["buses"] }),
      ]);
      setStep("apply");
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
    await generateDraft({ skipConfirm: true });
  };

  const restorePlan = async () => {
    const previous = plans?.previous;
    if (!previous) return;
    if (
      !(await confirm({
        title: "Restore the previous plan?",
        description: RESTORE_CONFIRM_MESSAGE,
        confirmLabel: "Restore",
      }))
    )
      return;
    setRestoring(true);
    try {
      const res = await api.post(`/api/fleet-plans/${previous.id}/restore`, {
        confirmations: [],
        acknowledgments: [],
      });
      setApplyResult({ act: "restore", ...res });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["fleet-plans"] }),
        qc.invalidateQueries({ queryKey: ["fleet-plan-review"] }),
        qc.invalidateQueries({ queryKey: ["routes"] }),
        qc.invalidateQueries({ queryKey: ["students"] }),
        qc.invalidateQueries({ queryKey: ["buses"] }),
      ]);
      toast({
        title: "Previous plan restored",
        description: `${res.routes_written} routes written · ${res.notified_family_count} families notified`,
      });
    } catch (err) {
      // Roster/fleet drift since the capture 409s naming each item — surfaced
      // verbatim; the admin fixes the fleet or re-drafts instead.
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setRestoring(false);
    }
  };

  // --- render ----------------------------------------------------------------
  const busList = buses as any[];
  const claimableRow = (b: any) => {
    const foreign = b.school_id && b.school_id !== schoolId;
    const claimingSchool = foreign
      ? ((schools as any[]).find((s) => s.id === b.school_id)?.name ?? "another school")
      : null;
    return { foreign, claimingSchool };
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Fleet Plan"
        subtitle="Draft a complete plan from enrolled students, review it, and apply it as the school's live routes"
      />

      <div className="flex flex-wrap items-center gap-3">
        {(schools as any[]).length > 1 && (
          <div className="w-64">
            <Select value={schoolId} onValueChange={(v) => setSchoolId(v)}>
              <SelectTrigger data-testid="plan-school-select">
                <SelectValue placeholder="Choose a school" />
              </SelectTrigger>
              <SelectContent>
                {(schools as any[]).map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="flex gap-2">
          {STEPS.map((s) => (
            <Button
              key={s.key}
              size="sm"
              variant={step === s.key ? "default" : "outline"}
              disabled={!stepEnabled(s.key)}
              onClick={() => setStep(s.key)}
              data-testid={`plan-step-${s.key}`}
            >
              {s.label}
            </Button>
          ))}
        </div>
        {degraded && draft && (
          <Badge variant="warning" data-testid="plan-degraded-badge">
            {PLAN_DEGRADED_MESSAGE}
          </Badge>
        )}
      </div>

      {!schoolId ? (
        <p className="text-sm text-muted-foreground">
          Choose a school to plan for — every plan belongs to one school.
        </p>
      ) : (
        <>
          {/* ---- Step 1: fleet confirmation ---- */}
          {step === "fleet" && (
            <Card>
              <CardHeader className="pb-2">
                <p className="font-heading text-lg font-semibold">Confirm the fleet</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Tick every bus this school's plan may use. A claimed bus is
                  unavailable to other schools; deselected buses are released
                  unless they carry this school's applied plan routes.
                </p>
                {busList.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No buses in the fleet yet — add them on the Buses page first.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {busList.map((b) => {
                      const { foreign, claimingSchool } = claimableRow(b);
                      return (
                        <li key={b.id} className="flex items-center gap-3 text-sm">
                          <label className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              className="h-4 w-4 accent-primary"
                              checked={fleetSel.has(b.id)}
                              disabled={Boolean(foreign) || confirmingFleet}
                              onChange={() => toggleBus(b.id)}
                              aria-label={`Use ${b.name}`}
                              data-testid={`fleet-bus-${b.id}`}
                            />
                            <span className="font-medium">{b.name}</span>
                          </label>
                          <span className="text-xs text-muted-foreground">
                            {b.capacity ?? "—"} seats
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {b.depot_address ? `Depot: ${b.depot_address}` : "No depot set"}
                          </span>
                          {b.availability === "out-of-service" && (
                            <Badge variant="destructive" className="text-[10px]">
                              Out of service
                            </Badge>
                          )}
                          {foreign && (
                            <Badge variant="outline" className="text-[10px]">
                              Claimed by {claimingSchool}
                            </Badge>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
                {fleetNotices.length > 0 && (
                  <ul className="space-y-1 rounded-md border bg-muted/30 p-3" data-testid="fleet-notices">
                    {fleetNotices.map((n, i) => (
                      <li key={i} className="text-xs text-muted-foreground">
                        {n.message}
                      </li>
                    ))}
                  </ul>
                )}
                <Button
                  onClick={confirmFleet}
                  disabled={confirmingFleet || busList.length === 0}
                  data-testid="plan-confirm-fleet"
                >
                  {confirmingFleet ? "Confirming…" : "Confirm fleet"}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* ---- Step 2: draft ---- */}
          {step === "draft" && (
            <Card>
              <CardHeader className="pb-2">
                <p className="font-heading text-lg font-semibold">Generate a draft</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Drafting reads every enrolled student's home pin and the
                  confirmed fleet, and proposes who rides which bus in which
                  order — morning and afternoon. A draft never changes live
                  routes; only Apply does.
                </p>
                <div className="flex items-end gap-2">
                  <div className="space-y-1">
                    <Label>Solver seed (optional)</Label>
                    <Input
                      type="number"
                      className="w-36"
                      value={seed}
                      placeholder="0"
                      onChange={(e) => setSeed(e.target.value)}
                      data-testid="plan-seed"
                    />
                  </div>
                  <Button
                    onClick={() => generateDraft()}
                    disabled={drafting}
                    data-testid="plan-generate"
                  >
                    <Wand2 className="h-4 w-4" />{" "}
                    {drafting ? "Drafting…" : draft ? "Draft again" : "Generate draft"}
                  </Button>
                </div>
                {draft && (
                  <div className="space-y-1 rounded-md border bg-muted/30 p-3 text-sm">
                    <p>
                      Open draft from {String(draft.created_at).slice(0, 16).replace("T", " ")}{" "}
                      · seed {draft.solver_seed}
                    </p>
                    {(draft.basis?.excluded_buses ?? []).map((b: any) => (
                      <p key={b.id} className="text-xs text-muted-foreground">
                        {b.name}: {b.reason}
                      </p>
                    ))}
                    <Button size="sm" variant="outline" onClick={() => setStep("review")}>
                      Go to review
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* ---- Step 3: review ---- */}
          {step === "review" && draft && (
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
                  <Button size="sm" onClick={() => setStep("apply")} data-testid="plan-to-apply">
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

          {/* ---- Step 4: apply ---- */}
          {step === "apply" && (
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
        </>
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
    </div>
  );
}
