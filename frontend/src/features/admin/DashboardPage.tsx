import { format } from "date-fns";
import { Bus, Clock, TriangleAlert, Users } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/features/admin/components/StatCard";
import { useActiveRuns, useBuses, useRuns, useStudents, useTodayIncidentCount } from "@/lib/queries";

const RUN_STATUS_VARIANT: Record<string, "default" | "success" | "warning"> = {
  "in-progress": "success",
  delayed: "warning",
  completed: "default",
};

const BUS_STATUS_VARIANT: Record<string, "success" | "warning" | "secondary" | "destructive"> = {
  active: "success",
  delayed: "warning",
  idle: "secondary",
  offline: "destructive",
};

export function DashboardPage() {
  const { data: buses = [] } = useBuses();
  const { data: runs = [] } = useRuns();
  // Server-side predicate (today Nairobi + non-completed), polled so ended runs
  // drop off the card without a reload.
  const { data: liveRuns = [] } = useActiveRuns();
  const { data: students = [] } = useStudents();
  const { data: todayIncidents } = useTodayIncidentCount();

  const today = new Date().toISOString().split("T")[0];
  const todayRuns = runs.filter((r: any) => r.date === today);
  const activeBuses = buses.filter((b: any) => b.status === "active").length;
  const delayed = buses.filter((b: any) => b.status === "delayed").length;
  const studentsOnBus = students.filter((s: any) => s.status === "on-bus").length;
  const incidentsToday = todayIncidents?.count ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">{format(new Date(), "EEEE, MMMM d, yyyy")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Active Buses" value={activeBuses} subtitle={`of ${buses.length} total`} icon={Bus} variant="success" />
        <StatCard label="Delayed" value={delayed} subtitle="buses behind schedule" icon={TriangleAlert} variant="warning" />
        <StatCard label="Students on Bus" value={studentsOnBus} subtitle={`of ${students.length} enrolled`} icon={Users} />
        <StatCard label="Incidents Today" value={incidentsToday} subtitle={`across ${todayRuns.length} runs`} icon={TriangleAlert} variant={incidentsToday > 0 ? "destructive" : "default"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Clock className="h-5 w-5 text-primary" /> Active Runs
            </CardTitle>
          </CardHeader>
          <CardContent>
            {liveRuns.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-1 py-16 text-center">
                <Clock className="h-8 w-8 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">No active runs right now</p>
                <p className="text-xs text-muted-foreground">Runs will appear here once started</p>
              </div>
            ) : (
              <div className="space-y-3">
                {liveRuns.map((run: any) => (
                  <div key={run.id} className="flex items-center justify-between rounded-lg border p-3">
                    <div>
                      <p className="font-medium">{run.bus_name ?? "Bus"} · {run.route_name ?? run.type}</p>
                      <p className="text-xs text-muted-foreground">
                        {run.stops_completed}/{run.total_stops} stops · {run.students_boarded}/{run.total_students} boarded
                      </p>
                    </div>
                    <Badge variant={RUN_STATUS_VARIANT[run.status] ?? "default"}>{run.status}</Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Bus className="h-5 w-5 text-primary" /> Fleet Status
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {buses.map((bus: any) => (
              <div key={bus.id} className="flex items-center justify-between gap-2">
                <div className="leading-tight">
                  <p className="font-medium">{bus.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {bus.plate_number ?? "—"}{bus.driver_name ? ` · ${bus.driver_name}` : ""}
                  </p>
                </div>
                <Badge variant={BUS_STATUS_VARIANT[bus.status] ?? "secondary"}>{bus.status}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
