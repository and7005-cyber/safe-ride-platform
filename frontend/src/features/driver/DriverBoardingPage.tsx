import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { DRIVER_NAV } from "@/features/driver/DriverHomePage";
import { useDriverContext } from "@/features/driver/driverHooks";
import { api } from "@/lib/apiClient";

export function DriverBoardingPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data } = useDriverContext();
  const activeRun = data?.active_run;
  const runStops = data?.run_stops ?? [];
  const students = data?.students ?? [];
  const [search, setSearch] = useState("");

  const boarded = (students as any[]).filter((s) => s.status === "on-bus").length;
  const remaining = students.length - boarded;
  const filtered = useMemo(
    () => (students as any[]).filter((s) => s.name.toLowerCase().includes(search.toLowerCase())),
    [students, search],
  );

  const orderForStudent = (studentId: string): number | null => {
    const stop = runStops.find((s: any) => s.student_id === studentId);
    return stop ? stop.stop_order : null;
  };

  const toggle = async (studentId: string, onBus: boolean) => {
    try {
      await api.post("/api/runs/driver/boarding", { student_id: studentId, on_bus: onBus });
      await qc.invalidateQueries({ queryKey: ["driver-context"] });
    } catch (err) {
      toast({ title: "Cannot update", description: (err as Error).message, variant: "destructive" });
    }
  };

  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title="Student Boarding">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Card>
            <CardContent className="flex flex-col items-center gap-0.5 py-4">
              <span className="font-heading text-lg font-bold">{boarded}</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Boarded</span>
            </CardContent>
          </Card>
          <Card className="bg-destructive/5">
            <CardContent className="flex flex-col items-center gap-0.5 py-4">
              <span className="font-heading text-lg font-bold text-destructive">{remaining}</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Remaining</span>
            </CardContent>
          </Card>
        </div>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-9" placeholder="Search students…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>

        <p className="text-sm font-semibold">Students ({filtered.length})</p>

        {students.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No students on this bus.</CardContent></Card>
        ) : (
          <div className="space-y-2">
            {filtered.map((s: any) => {
              const order = orderForStudent(s.id);
              const reached = activeRun != null && order != null && order <= activeRun.stops_completed;
              const onBus = s.status === "on-bus";
              return (
                <Card key={s.id}>
                  <CardContent className="flex items-center justify-between gap-3 p-3">
                    <div>
                      <p className="font-medium">{s.name}</p>
                      <p className="text-xs text-muted-foreground">{s.grade ?? ""}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {onBus ? (
                        <Badge variant="success">On bus</Badge>
                      ) : (
                        <Badge variant="secondary">{s.status}</Badge>
                      )}
                      {onBus ? (
                        <Button size="sm" variant="outline" onClick={() => toggle(s.id, false)}>
                          <X className="h-4 w-4" /> Off
                        </Button>
                      ) : (
                        <Button size="sm" disabled={!reached} onClick={() => toggle(s.id, true)}>
                          <Check className="h-4 w-4" /> Board
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </RoleMobileLayout>
  );
}
