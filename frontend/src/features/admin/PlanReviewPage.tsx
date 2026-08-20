import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
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
import { PlanApplyStep } from "@/features/admin/components/PlanApplyStep";
import { PlanDraftStep } from "@/features/admin/components/PlanDraftStep";
import { PlanFleetStep } from "@/features/admin/components/PlanFleetStep";
import { PlanReviewStep } from "@/features/admin/components/PlanReviewStep";
import { api } from "@/lib/apiClient";
import { PLAN_DEGRADED_MESSAGE, flattenUnplaceable } from "@/lib/planReview";
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
//
// Each step lives in its own component (PlanFleetStep … PlanApplyStep under
// components/), every one mounted for the page's whole life with rendering
// gated on `active` — so per-step state survives step switches exactly as it
// did when it was page-level state. This shell keeps the school scope, the
// stepper, the shared plan/review queries, the cross-step draft generation
// (the apply dialog's re-draft escape calls it too), the apply/restore
// receipt, and the slot-in proposals card (visible on every step).

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

  // --- draft generation (F1 step 2 / F4) ------------------------------------
  const [seed, setSeed] = useState("");
  const [drafting, setDrafting] = useState(false);
  // The apply/restore receipt, set by PlanApplyStep and cleared by a fresh
  // draft — cross-step by mechanical necessity, so it lives here.
  const [applyResult, setApplyResult] = useState<any | null>(null);

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

  // --- shared derived state --------------------------------------------------
  // Unplaceable legs feed both the review surface (panel + Place dialog) and
  // the apply gates (counts, acknowledgment payload), so the memo stays here.
  const unplaceable = useMemo(
    () => flattenUnplaceable(review?.unplaceable),
    [review],
  );

  const degraded = Boolean(review?.plan?.degraded ?? draft?.degraded);

  // A run in progress for THIS school (System-Wide Impact): the admin runs
  // list joined to routes the way other admin pages read it.
  const runActive = useMemo(() => {
    const routeSchool = new Map((routes as any[]).map((r) => [r.id, r.school_id]));
    return (activeRuns as any[]).some(
      (r) => r.route_id && routeSchool.get(r.route_id) === schoolId,
    );
  }, [routes, activeRuns, schoolId]);

  // --- slot-in proposals (U12) ----------------------------------------------
  // Stored on the school's APPLIED plan row and generated in the background
  // when a plannable student lacks a route for a leg their pattern requires.
  // Polled at the admin cadence so a fresh enrolment's proposal appears
  // without a reload; visible whenever the school has an applied plan.
  const slotInsQ = useQuery({
    queryKey: ["slot-ins", schoolId],
    queryFn: () => api.get("/api/fleet-plans/slot-ins", { school_id: schoolId }),
    enabled: Boolean(schoolId) && Boolean(plans?.applied),
    refetchInterval: 15_000,
  });
  const slotIns: any = slotInsQ.data;
  const [slotInBusy, setSlotInBusy] = useState<string | null>(null);

  const runSlotIn = async (verb: "accept" | "dismiss", p: any) => {
    setSlotInBusy(p.id);
    try {
      await api.post(`/api/fleet-plans/slot-ins/${verb}`, {
        school_id: schoolId,
        proposal_id: p.id,
      });
      toast(
        verb === "accept"
          ? { title: `${p.student_name} placed`, description: p.text }
          : {
              title: "Proposal dismissed",
              description: `${p.student_name} stays unassigned and visible on the Students page.`,
            },
      );
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["slot-ins"] }),
        qc.invalidateQueries({ queryKey: ["routes"] }),
        qc.invalidateQueries({ queryKey: ["students"] }),
      ]);
    } catch (err) {
      // 409/422 refusals verbatim (aged out, capacity, stale route).
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSlotInBusy(null);
    }
  };

  // Adjust — the documented design choice: with an open draft, the review
  // surface is where manual placement lives (the Place dialog), so Adjust
  // jumps there; with NO draft there is nothing to review, so it deep-links
  // to the Routes page for manual placement on the live routes.
  const adjustProposal = () => {
    if (draft) setStep("review");
    else navigate("/routes");
  };

  // --- render ----------------------------------------------------------------
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
          <PlanFleetStep
            active={step === "fleet"}
            schoolId={schoolId}
            schools={schools as any[]}
            buses={buses as any[]}
            onConfirmed={() => setStep("draft")}
          />

          {/* ---- Step 2: draft ---- */}
          <PlanDraftStep
            active={step === "draft"}
            draft={draft}
            seed={seed}
            onSeedChange={setSeed}
            drafting={drafting}
            onGenerate={() => generateDraft()}
            onGoToReview={() => setStep("review")}
          />

          {/* ---- Step 3: review ---- */}
          <PlanReviewStep
            active={step === "review"}
            draft={draft}
            review={review}
            school={school}
            unplaceable={unplaceable}
            onContinueToApply={() => setStep("apply")}
          />

          {/* ---- Step 4: apply ---- */}
          <PlanApplyStep
            active={step === "apply"}
            schoolId={schoolId}
            draft={draft}
            plans={plans}
            review={review}
            students={students as any[]}
            unplaceable={unplaceable}
            runActive={runActive}
            applyResult={applyResult}
            onApplyResult={setApplyResult}
            onStepChange={setStep}
            onGenerateDraft={generateDraft}
          />

          {/* ---- Slot-in proposals (U12): shown whenever an applied plan
               exists, on every step — mid-year placements arrive here. ---- */}
          {plans?.applied && (
            <Card data-testid="plan-proposals">
              <CardHeader className="pb-2">
                <p className="font-heading text-lg font-semibold">Proposals</p>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Mid-year enrolments and address changes are slotted into the
                  applied routes with minimal disruption. A proposal never
                  applies itself: accept it, adjust the placement by hand, or
                  dismiss it. Aged proposals stay listed but can no longer be
                  accepted.
                </p>
                {(slotIns?.proposals ?? []).length === 0 &&
                (slotIns?.unplaceable ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground" data-testid="proposals-empty">
                    No pending proposals.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {(slotIns?.proposals ?? []).map((p: any) => (
                      <li
                        key={p.id}
                        className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm"
                        data-testid={`proposal-${p.id}`}
                      >
                        <span>
                          <span className="font-medium">{p.student_name}</span> — {p.text}
                        </span>
                        {p.expired && (
                          <Badge variant="warning" data-testid="proposal-aged-badge">
                            Aged out
                          </Badge>
                        )}
                        <span className="ml-auto flex gap-1">
                          <Button
                            size="sm"
                            disabled={Boolean(slotInBusy) || Boolean(p.expired)}
                            onClick={() => runSlotIn("accept", p)}
                            data-testid={`proposal-accept-${p.id}`}
                          >
                            Accept
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={Boolean(slotInBusy)}
                            onClick={adjustProposal}
                            data-testid={`proposal-adjust-${p.id}`}
                          >
                            Adjust
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={Boolean(slotInBusy)}
                            onClick={() => runSlotIn("dismiss", p)}
                            data-testid={`proposal-dismiss-${p.id}`}
                          >
                            Dismiss
                          </Button>
                        </span>
                      </li>
                    ))}
                    {(slotIns?.unplaceable ?? []).map((u: any) => (
                      <li
                        key={u.id}
                        className="flex flex-wrap items-center gap-2 rounded-md border border-warning/60 bg-warning/10 p-2 text-sm"
                        data-testid="slot-in-unplaceable"
                      >
                        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                        <span>
                          <span className="font-medium">{u.student_name}</span> — no
                          feasible insertion ({u.constraint}); place them by hand or
                          free a seat.
                        </span>
                        <span className="ml-auto flex gap-1">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={Boolean(slotInBusy)}
                            onClick={adjustProposal}
                          >
                            Adjust
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={Boolean(slotInBusy)}
                            onClick={() => runSlotIn("dismiss", u)}
                            data-testid={`proposal-dismiss-${u.id}`}
                          >
                            Dismiss
                          </Button>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
