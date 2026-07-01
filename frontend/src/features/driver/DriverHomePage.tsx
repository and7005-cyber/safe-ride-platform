import { useNavigate } from "react-router-dom";
import { Bus, Clock, Home, MapPin, PlayCircle, TriangleAlert, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { useDriverContext } from "@/features/driver/driverHooks";
import { useAuth } from "@/lib/auth";

export const DRIVER_NAV = [
  { to: "/driver", label: "Home", icon: Home, end: true },
  { to: "/driver/run", label: "Run", icon: MapPin },
  { to: "/driver/boarding", label: "Board", icon: Bus },
  { to: "/driver/incident", label: "Incident", icon: TriangleAlert },
];

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
  const { user } = useAuth();
  const { data, isLoading } = useDriverContext();

  const firstName = user?.fullName ?? "Driver";
  const bus = data?.bus;
  const activeRun = data?.active_run;
  const routes = data?.routes ?? [];
  const students = data?.students ?? [];

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
                // Starting a run always goes through the Run page's explicit
                // route selection (R27) — the tile never starts one directly.
                <Button onClick={() => navigate("/driver/run")} disabled={routes.length === 0}>Start Run</Button>
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
