import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// U9 step 2 — draft generation (F1 step 2 / F4), extracted from
// PlanReviewPage. The seed input and generateDraft itself stay in the shell:
// the apply dialog's "Discard and re-draft" escape calls the same
// generateDraft with the same seed, so both live where both steps reach them.

export function PlanDraftStep({
  active,
  draft,
  seed,
  onSeedChange,
  drafting,
  onGenerate,
  onGoToReview,
}: {
  active: boolean;
  draft: any;
  seed: string;
  onSeedChange: (value: string) => void;
  drafting: boolean;
  onGenerate: () => void;
  onGoToReview: () => void;
}) {
  if (!active) return null;

  return (
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
              onChange={(e) => onSeedChange(e.target.value)}
              data-testid="plan-seed"
            />
          </div>
          <Button
            onClick={() => onGenerate()}
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
            <Button size="sm" variant="outline" onClick={onGoToReview}>
              Go to review
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
