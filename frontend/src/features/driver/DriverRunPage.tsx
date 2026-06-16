import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Flag, MapPin, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { DRIVER_NAV } from "@/features/driver/DriverHomePage";
import { useDriverContext } from "@/features/driver/driverHooks";
import { api } from "@/lib/apiClient";

function morningFirst(routes: any[]) {
  return [...routes].sort(
    (a, b) => Number(b.type === "morning") - Number(a.type === "morning"),
  );
}

export function DriverRunPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data } = useDriverContext();
  const [routeId, setRouteId] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const activeRun = data?.active_run;
  const routes = data?.routes ?? [];

  // Default the pre-run route to the morning route (KTD-9.2), never routes[0].
  useEffect(() => {
    if (!routeId && routes.length > 0) setRouteId(morningFirst(routes)[0].id);
  }, [routes, routeId]);

  // Bus position is derived from stop arrivals on the backend (no device GPS):
  // the admin's/driver's device location must never become the bus position.

  const refresh = () => qc.invalidateQueries({ queryKey: ["driver-context"] });

  const start = async () => {
    setBusy(true);
    try {
      await api.post("/api/runs/driver/start", { route_id: routeId });
      await refresh();
    } catch (err) {
      toast({ title: "Cannot start run", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const arrive = async () => {
    if (!activeRun) return;
    setBusy(true);
    try {
      await api.post("/api/runs/driver/arrive", { run_id: activeRun.id });
      await refresh();
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const end = async () => {
    if (!activeRun) return;
    setBusy(true);
    try {
      await api.post("/api/runs/driver/end", { run_id: activeRun.id });
      await refresh();
      toast({ title: "Run completed" });
      navigate("/driver");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const stops = (data?.run_stops ?? []).reduce((acc: any[], s: any) => {
    if (!acc.find((x) => x.stop_order === s.stop_order)) acc.push(s);
    return acc;
  }, []);

  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title="Active Run">
      {!activeRun ? (
        <Card>
          <CardHeader><CardTitle className="text-lg">Start a run</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {routes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No routes assigned to your bus.</p>
            ) : (
              <>
                <Select value={routeId} onValueChange={setRouteId}>
                  <SelectTrigger><SelectValue placeholder="Choose route" /></SelectTrigger>
                  <SelectContent>
                    {morningFirst(routes).map((r: any) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button className="w-full" onClick={start} disabled={busy || !routeId}>
                  <Play className="h-4 w-4" /> Start Run
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-2 p-5">
              <Badge variant="success">Run in progress</Badge>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full bg-primary" style={{ width: `${activeRun.total_stops ? (activeRun.stops_completed / activeRun.total_stops) * 100 : 0}%` }} />
              </div>
              <p className="text-sm text-muted-foreground">{activeRun.stops_completed}/{activeRun.total_stops} stops completed</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Stops</CardTitle></CardHeader>
            <CardContent>
              <ol className="space-y-2">
                {stops.map((s: any) => {
                  const done = s.stop_order <= activeRun.stops_completed;
                  return (
                    <li key={s.stop_order} className="flex items-center gap-2 text-sm">
                      {done ? <CheckCircle2 className="h-4 w-4 text-success" /> : <MapPin className="h-4 w-4 text-muted-foreground" />}
                      <span className={done ? "text-muted-foreground line-through" : ""}>{s.stop_order}. {s.name}</span>
                      {s.is_school_gate && <Badge variant="outline" className="ml-auto">Gate</Badge>}
                    </li>
                  );
                })}
              </ol>
            </CardContent>
          </Card>

          <div className="grid grid-cols-2 gap-3">
            <Button onClick={arrive} disabled={busy || activeRun.stops_completed >= activeRun.total_stops}>
              <MapPin className="h-4 w-4" /> Arrive Next Stop
            </Button>
            <Button variant="destructive" onClick={end} disabled={busy}>
              <Flag className="h-4 w-4" /> End Run
            </Button>
          </div>
        </div>
      )}
    </RoleMobileLayout>
  );
}
