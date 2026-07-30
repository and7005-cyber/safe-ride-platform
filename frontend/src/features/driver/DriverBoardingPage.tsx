import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, LogOut, Search, Undo2, UserX } from "lucide-react";
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
// The driver board had no label map at all and rendered raw slugs like
// "at-school" (U17). It now reads the same vocabulary as admin and parent.
import {
  STUDENT_STATUS_LABEL,
  STUDENT_STATUS_VARIANT,
  labelFor,
  variantFor,
} from "@/lib/statusVocabulary";

// Morning runs board students; afternoon runs (auto-boarded at start, R32)
// confirm drop-offs. Both are confirmed explicitly, naming the student (R29).
//
// Since U13 every child on the roster can be released from this screen without
// calling the office: marked absent whatever the stop progress, handed over
// off-route with a note, or — for something this login recorded on a run still
// open — undone. That is what the closure gate needs to be workable: it refuses
// to end a run while anyone is unaccounted for, so the driver has to be able to
// account for every case from the phone.
//
// Un-boarding is still not one of them. The boarding toggle's rejection of
// on_bus=false is a stale-client concurrency guard with its own justification;
// the undo below is a separate path that retracts a recorded outcome and tells
// the family, rather than a relaxation of that guard.

export function DriverBoardingPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data } = useDriverContext();
  const activeRun = data?.active_run;
  const runStops = data?.run_stops ?? [];
  const students = data?.students ?? [];
  const [search, setSearch] = useState("");
  // Arrived here from a tapped name on the end-run blocking list (U13/R11).
  const [params] = useSearchParams();
  const focusId = params.get("student");
  const focusRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    focusRef.current?.scrollIntoView({ block: "center" });
  }, [focusId, students.length]);

  const afternoon = activeRun?.type === "afternoon";
  // The derived status, never the raw column (U12/U13). The column is vestigial
  // since U3 and drifts — U7 stopped resetting it on run deletion — so reading
  // it showed the driver a different state from the office and the parent app
  // for the same child, and could never show the two derived-only values.
  const stateOf = (s: any): string => s.display_status ?? s.status ?? "";
  const isAbsent = (s: any) => s.absent === true || stateOf(s) === "absent";
  const isDone = (s: any) =>
    afternoon ? stateOf(s) === "dropped-off" : stateOf(s) === "on-bus";
  // Aboard covers the afternoon presumption too: the auto-board put them on the
  // bus, which is what makes a drop-off or a hand-over the right release.
  const isAboard = (s: any) =>
    stateOf(s) === "on-bus" || stateOf(s) === "expected-on-bus";

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
      // Says which run it covers (U8/R17). The mark is scoped to this trip:
      // a child missing this morning may still be riding home, and claiming
      // the whole day would strike them off that route too.
      //
      // No longer "contact the office to undo": since U5 the driver can undo
      // their own mark while the run is open, and telling them to ring the
      // office for something they can do themselves is how a correction path
      // goes unused.
      description: afternoon
        ? "This covers the trip home only, not the morning. The parent and the school "
          + "office are notified. You can undo it here while this run is open."
        : "This covers the morning trip only, not the ride home. The parent and the "
          + "school office are notified. You can undo it here while this run is open.",
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

  const handover = async (s: any) => {
    // The note is the whole point: "left the bus" without where or why is not
    // an account of anything, so the dialog blocks confirmation until it exists.
    const note = await confirm({
      title: `${s.name} left the bus off-route?`,
      description:
        "Use this when a child leaves the bus away from their own stop — a breakdown, "
        + "a closed road, a guardian collecting them at the roadside. Their family is "
        + "told they left the bus, with your note.",
      confirmLabel: "Record hand-over",
      cancelLabel: "Cancel",
      destructive: false,
      note: {
        label: "Where and to whom?",
        placeholder: "Collected by grandmother at the junction",
        maxLength: 200,
      },
    });
    if (note == null) return;
    try {
      await api.post("/api/runs/driver/handover", { student_id: s.id, note });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot record", description: (err as Error).message, variant: "destructive" });
    }
  };

  const undo = async (s: any) => {
    if (!(await confirm({
      title: `Undo your entry for ${s.name}?`,
      description:
        "Their family is told about the correction, so only undo something you "
        + "recorded by mistake.",
      confirmLabel: "Undo",
      cancelLabel: "Keep",
    }))) return;
    try {
      await api.post("/api/runs/driver/reverse", { student_id: s.id });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot undo", description: (err as Error).message, variant: "destructive" });
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
              const done = isDone(s);
              const aboard = isAboard(s);
              // Absent is offered for any child with no recorded outcome,
              // whatever the stop progress (U13/R8). It used to require the
              // stop to have been reached, which left the driver of a child who
              // was never at the stop with nothing to tap — and the closure gate
              // then refused to end the run over exactly that child.
              const canMarkAbsent = !absent && !done;
              // A hand-over releases a child who really was aboard, so it is
              // offered only then. It deliberately does not require the stop to
              // have been reached: by definition it did not happen there.
              const canHandover = aboard && !done;
              const focused = focusId === s.id;
              return (
                <Card
                  key={s.id}
                  ref={focused ? focusRef : undefined}
                  className={focused ? "ring-2 ring-primary" : undefined}
                  data-testid={`student-row-${s.id}`}
                >
                  <CardContent className="space-y-2 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="font-medium">{s.name}</p>
                        <p className="text-xs text-muted-foreground">{s.grade ?? ""}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant={variantFor(STUDENT_STATUS_VARIANT, stateOf(s))}>
                          {labelFor(STUDENT_STATUS_LABEL, stateOf(s))}
                        </Badge>
                        {/* Shown only where the server would allow it (U13/R10):
                            every terminal badge looking reversible invites
                            accidental taps, and none of them looking reversible
                            makes the path undiscoverable. */}
                        {s.can_undo && (
                          <Button
                            size="sm"
                            variant="ghost"
                            data-testid={`undo-${s.id}`}
                            onClick={() => undo(s)}
                          >
                            <Undo2 className="h-4 w-4" /> Undo
                          </Button>
                        )}
                      </div>
                    </div>

                    {(canMarkAbsent || canHandover || (!done && !absent)) && (
                      <div className="flex flex-wrap justify-end gap-2">
                        {canMarkAbsent && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                            data-testid={`absent-${s.id}`}
                            onClick={() => markAbsent(s)}
                          >
                            <UserX className="h-4 w-4" /> Absent
                          </Button>
                        )}
                        {canHandover && (
                          <Button
                            size="sm"
                            variant="outline"
                            data-testid={`handover-${s.id}`}
                            onClick={() => handover(s)}
                          >
                            <LogOut className="h-4 w-4" /> Off-route
                          </Button>
                        )}
                        {!absent && !done && (afternoon ? aboard : true) && (
                          <Button
                            size="sm"
                            disabled={!reached}
                            data-testid={`${afternoon ? "dropoff" : "board"}-${s.id}`}
                            onClick={() => (afternoon ? dropoff(s) : board(s))}
                          >
                            <Check className="h-4 w-4" /> {afternoon ? "Drop-off" : "Board"}
                          </Button>
                        )}
                      </div>
                    )}
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
