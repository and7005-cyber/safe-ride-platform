import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Bus, Clock, Home, MapPin, PlayCircle, TriangleAlert, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { useDriverContext } from "@/features/driver/driverHooks";
import { api } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { useState } from "react";

export const DRIVER_NAV = [
  { to: "/driver", label: "Home", icon: Home, end: true },
  { to: "/driver/run", label: "Run", icon: MapPin },
  { to: "/driver/boarding", label: "Board", icon: Bus },
  { to: "/driver/incident", label: "Incident", icon: TriangleAlert },
];

function morningFirst(routes: any[]) {
  return [...routes].sort(
    (a, b) => Number(b.type === "morning") - Number(a.type === "morning"),
  );
}

function StatTile({ value, label, icon: Icon }: { value: string | number; label: string; icon: any }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-0.5 py-4">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="font-heading text-lg font-bold">{value}</span>
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      </CardContent>
    </Card>
  );
}

export function DriverHomePage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const { user } = useAuth();
  const { data, isLoading } = useDriverContext();
  const [busy, setBusy] = useState(false);

  const firstName = user?.fullName ?? "Driver";
  const bus = data?.bus;
  const activeRun = data?.active_run;
  const routes = data?.routes ?? [];
  const students = data?.students ?? [];

  const start = async () => {
    const route = morningFirst(routes)[0];
    if (!route) return;
    setBusy(true);
    try {
      await api.post("/api/runs/driver/start", { route_id: route.id });
      await qc.invalidateQueries({ queryKey: ["driver-context"] });
      navigate("/driver/run");
    } catch (err) {
      toast({ title: "Cannot start run", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title="SafeRide Driver">
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : !bus ? (
        <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">No bus is assigned to you yet. Contact your administrator.</CardContent></Card>
      ) : (
        <div className="space-y-4">
          <div>
            <h2 className="font-heading text-xl font-bold">Hello, {firstName} 👋</h2>
            <p className="text-sm text-muted-foreground">{bus.name} · {bus.plate_number ?? "—"}</p>
          </div>

          <Card>
            <CardContent className="flex flex-col items-center gap-3 py-8">
              <PlayCircle className="h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                {activeRun ? `Run in progress · ${activeRun.stops_completed}/${activeRun.total_stops} stops` : "No active run"}
              </p>
              {activeRun ? (
                <Button onClick={() => navigate("/driver/run")}>Continue Run</Button>
              ) : (
                <Button onClick={start} disabled={busy || routes.length === 0}>Start Run</Button>
              )}
            </CardContent>
          </Card>

          <div className="grid grid-cols-3 gap-3">
            <StatTile value={activeRun?.total_stops ?? 0} label="Stops" icon={MapPin} />
            <StatTile value={students.length} label="Students" icon={Users} />
            <StatTile value={activeRun?.start_time ?? "—"} label="Depart" icon={Clock} />
          </div>

          {routes.length === 0 && (
            <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No routes assigned to this bus yet.</CardContent></Card>
          )}
        </div>
      )}
    </RoleMobileLayout>
  );
}
