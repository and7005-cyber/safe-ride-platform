import { UserX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { legLabel, type UnplaceableEntry } from "@/lib/planReview";

// U9 — the R7 unplaceable panel: every child the draft could not seat, named
// per leg with the binding constraint ('seats', 'stop cap', 'unresolved
// address', 'unassigned'). The per-row "Place" action is the manual placement
// path until the slot-in engine ships (documented design decision): it opens
// the page's Place dialog, backed by POST /{plan}/assign.

export function PlanUnplaceablePanel({
  entries,
  busy,
  onPlace,
}: {
  /** Flattened (student, leg) rows in leg order — lib/planReview.flattenUnplaceable. */
  entries: UnplaceableEntry[];
  busy: boolean;
  onPlace: (entry: UnplaceableEntry) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <Card
      className="border-destructive/40 bg-destructive/5"
      data-testid="unplaceable-panel"
    >
      <CardHeader className="flex-row items-center gap-2 space-y-0 pb-2">
        <UserX className="h-4 w-4 shrink-0 text-destructive" />
        <p className="font-heading text-base font-semibold text-destructive">
          {entries.length} unplaceable {entries.length === 1 ? "leg" : "legs"}
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">
          These children have no seat in the draft for the named leg. Place
          each one by hand, or acknowledge them by name when you apply.
        </p>
        <ul className="space-y-1.5">
          {entries.map((u) => (
            <li
              key={`${u.student_id}-${u.leg}`}
              className="flex flex-wrap items-center gap-2 text-sm"
              data-testid={`unplaceable-${u.student_id}-${u.leg}`}
            >
              <span className="font-medium">{u.name ?? u.student_id}</span>
              <Badge variant="outline" className="text-[10px]">
                {legLabel(u.leg)}
              </Badge>
              <span className="text-xs text-muted-foreground">{u.constraint}</span>
              <Button
                variant="outline"
                size="sm"
                className="ml-auto h-7 px-2 text-xs"
                aria-label={`Place ${u.name ?? u.student_id}`}
                disabled={busy}
                onClick={() => onPlace(u)}
              >
                Place
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
