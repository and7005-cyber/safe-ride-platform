import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";

// U9 step 1 — fleet confirmation (F1 step 1), extracted from PlanReviewPage.
// Mounted for the whole page life (render gated on `active`) so the seeded
// selection survives step switches exactly as it did as page-level state.

export function PlanFleetStep({
  active,
  schoolId,
  schools,
  buses,
  onConfirmed,
}: {
  active: boolean;
  schoolId: string;
  schools: any[];
  buses: any[];
  onConfirmed: () => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();

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
      onConfirmed();
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setConfirmingFleet(false);
    }
  };

  const busList = buses as any[];
  const claimableRow = (b: any) => {
    const foreign = b.school_id && b.school_id !== schoolId;
    const claimingSchool = foreign
      ? ((schools as any[]).find((s) => s.id === b.school_id)?.name ?? "another school")
      : null;
    return { foreign, claimingSchool };
  };

  if (!active) return null;

  return (
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
  );
}
