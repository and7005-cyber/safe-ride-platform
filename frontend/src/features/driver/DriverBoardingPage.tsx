import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Search, UserX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { DRIVER_NAV } from "@/features/driver/DriverHomePage";
import { useDriverContext } from "@/features/driver/driverHooks";
import { api } from "@/lib/apiClient";

// Morning runs board students; afternoon runs (auto-boarded at start, R32)
// confirm drop-offs. Both actions are final after an explicit confirmation
// naming the student (R29) — there is no un-board/undo control.

export function DriverBoardingPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data } = useDriverContext();
  const activeRun = data?.active_run;
  const runStops = data?.run_stops ?? [];
  const students = data?.students ?? [];
  const [search, setSearch] = useState("");

  const afternoon = activeRun?.type === "afternoon";
  const isAbsent = (s: any) => s.absent === true || s.status === "absent";
  const isDone = (s: any) => (afternoon ? s.status === "dropped-off" : s.status === "on-bus");

  const done = (students as any[]).filter(isDone).length;
  const remaining = (students as any[]).filter((s) => !isDone(s) && !isAbsent(s)).length;
  const filtered = useMemo(
    () => (students as any[]).filter((s) => s.name.toLowerCase().includes(search.toLowerCase())),
    [students, search],
  );

  const orderForStudent = (studentId: string): number | null => {
    const stop = runStops.find((s: any) => s.student_id === studentId);
    return stop ? stop.stop_order : null;
  };

  const refresh = () => qc.invalidateQueries({ queryKey: ["driver-context"] });

  const board = async (s: any) => {
    if (!(await confirm({
      title: `Board ${s.name}?`,
      confirmLabel: "Board",
      cancelLabel: "Cancel",
      destructive: false,
    }))) return;
    try {
      await api.post("/api/runs/driver/boarding", { student_id: s.id, on_bus: true });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot update", description: (err as Error).message, variant: "destructive" });
    }
  };

  const dropoff = async (s: any) => {
    if (!(await confirm({
      title: `Drop off ${s.name}?`,
      confirmLabel: "Drop off",
      cancelLabel: "Cancel",
      destructive: false,
    }))) return;
    try {
      await api.post("/api/runs/driver/dropoff", { student_id: s.id });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot update", description: (err as Error).message, variant: "destructive" });
    }
  };

  const markAbsent = async (s: any) => {
    if (!(await confirm({
      title: `Mark ${s.name} absent?`,
      description: "The parent and the school office will be notified. Contact the office to undo.",
      confirmLabel: "Mark absent",
      cancelLabel: "Cancel",
    }))) return;
    try {
      await api.post("/api/runs/driver/absent", { student_id: s.id });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot update", description: (err as Error).message, variant: "destructive" });
    }
  };

  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title={afternoon ? "Student Drop-off" : "Student Boarding"}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Card>
            <CardContent className="flex flex-col items-center gap-0.5 py-4">
              <span className="font-heading text-lg font-bold">{done}</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{afternoon ? "Dropped off" : "Boarded"}</span>
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
              const absent = isAbsent(s);
              const onBus = s.status === "on-bus";
              const droppedOff = s.status === "dropped-off";
              // Absent is offered where a no-show is observable (R24):
              // morning at a reached stop before boarding; afternoon while the
              // student is still on the (auto-boarded) roster.
              const canMarkAbsent = afternoon ? onBus : reached && !onBus;
              return (
                <Card key={s.id}>
                  <CardContent className="flex items-center justify-between gap-3 p-3">
                    <div>
                      <p className="font-medium">{s.name}</p>
                      <p className="text-xs text-muted-foreground">{s.grade ?? ""}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {absent ? (
                        <Badge variant="destructive">Absent</Badge>
                      ) : afternoon ? (
                        droppedOff ? (
                          <Badge variant="success">Dropped off</Badge>
                        ) : onBus ? (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                              onClick={() => markAbsent(s)}
                            >
                              <UserX className="h-4 w-4" /> Absent
                            </Button>
                            <Button size="sm" disabled={!reached} onClick={() => dropoff(s)}>
                              <Check className="h-4 w-4" /> Drop-off
                            </Button>
                          </>
                        ) : (
                          <Badge variant="secondary">{s.status}</Badge>
                        )
                      ) : onBus ? (
                        <Badge variant="success">On bus</Badge>
                      ) : (
                        <>
                          <Badge variant="secondary">{s.status}</Badge>
                          {canMarkAbsent && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                              onClick={() => markAbsent(s)}
                            >
                              <UserX className="h-4 w-4" /> Absent
                            </Button>
                          )}
                          <Button size="sm" disabled={!reached} onClick={() => board(s)}>
                            <Check className="h-4 w-4" /> Board
                          </Button>
                        </>
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
