import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Bus, Clock, MapPin, Phone, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { cn } from "@/lib/utils";
import { PARENT_NAV, useChildren } from "@/features/parent/parentHooks";

const STATUS_VARIANT: Record<string, "secondary" | "success" | "warning" | "destructive"> = {
  "at-home": "secondary",
  "at-school": "secondary",
  "on-bus": "success",
  "dropped-off": "warning",
  absent: "destructive",
};

const STATUS_LABEL: Record<string, string> = {
  "at-home": "At home",
  "at-school": "At School",
  "on-bus": "On the bus",
  "dropped-off": "Dropped off",
  absent: "Absent",
};

/**
 * Highlighted child status chip (R36). Renders the server-derived
 * `display_status` (absence- and staleness-aware) and falls back to the raw
 * operational `status` for older payloads. Solid fill, slightly larger than
 * the default badge. Shared with the Profile page's My Children card.
 */
export function ChildStatusBadge({
  child,
  className,
}: {
  child: { display_status?: string | null; status?: string | null };
  className?: string;
}) {
  const status = child.display_status ?? child.status ?? "";
  return (
    <Badge
      data-testid="child-status-badge"
      variant={STATUS_VARIANT[status] ?? "secondary"}
      className={cn("px-3 py-1 text-sm", className)}
    >
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

// Live parity: ETA is a mock ~3–13 min (KTD-10), seeded from the child id so it
// stays stable across the 5s polling re-renders.
function mockEta(childId: string): number {
  let hash = 0;
  for (let i = 0; i < childId.length; i++) hash = (hash * 31 + childId.charCodeAt(i)) | 0;
  return 3 + (Math.abs(hash) % 11);
}

export function ParentHomePage() {
  const navigate = useNavigate();
  const { data: children = [], isLoading } = useChildren();

  const firstName = children[0]?.parent_name?.split(" ")[0] ?? "Parent";
  const etas = useMemo(
    () => Object.fromEntries((children as any[]).map((c) => [c.id, mockEta(c.id)])),
    [children],
  );
  // "Call Driver" global action dials the first child that has a bus + driver phone.
  const callChild = (children as any[]).find((c) => c.bus_name && c.driver_phone);

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="SafeRide Parent">
      <div className="space-y-4">
        <div>
          <h2 className="font-heading text-xl font-bold">Good morning, {firstName} 👋</h2>
          <p className="text-sm text-muted-foreground">Today's bus status at a glance</p>
        </div>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : children.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No children are linked to your account yet.</CardContent></Card>
        ) : (
          <>
            {children.map((child: any) => {
              // The derived display_status is the trustworthy state (R36):
              // the mock ETA only shows while the child is really on a bus.
              const onBus = (child.display_status ?? child.status) === "on-bus";
              return (
                <Card key={child.id}>
                  <CardContent className="space-y-3 p-4">
                    <div className="flex items-start gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-secondary text-sm font-semibold text-secondary-foreground">
                        {initials(child.name)}
                      </span>
                      <div className="flex-1">
                        <p className="font-heading text-base font-semibold">{child.name}</p>
                        <p className="text-sm text-muted-foreground">
                          {child.grade ?? ""}{child.bus_name ? ` • ${child.bus_name}` : ""}
                        </p>
                      </div>
                      <ChildStatusBadge child={child} />
                    </div>

                    {onBus && (
                      <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Clock className="h-4 w-4" /> Arriving in ~{etas[child.id]} min
                      </p>
                    )}

                    <div className="flex items-center justify-between">
                      <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Bus className="h-4 w-4" /> Driver: {child.bus_name ? (child.driver_name ?? "—") : "Not assigned"}
                      </p>
                      <Button variant="outline" size="sm" onClick={() => navigate("/parent/track")}>
                        <MapPin className="h-4 w-4" /> Track
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}

            <div className="grid grid-cols-2 gap-3">
              <Card className="cursor-pointer transition-colors hover:bg-muted/50" onClick={() => navigate("/parent/alerts")}>
                <CardContent className="flex flex-col items-center gap-1 py-5">
                  <TriangleAlert className="h-5 w-5 text-warning" />
                  <span className="text-sm font-medium">Alerts</span>
                </CardContent>
              </Card>
              {callChild ? (
                <a href={`tel:${callChild.driver_phone}`} className="block">
                  <Card className="cursor-pointer transition-colors hover:bg-muted/50">
                    <CardContent className="flex flex-col items-center gap-1 py-5">
                      <Phone className="h-5 w-5 text-primary" />
                      <span className="text-sm font-medium">Call Driver</span>
                    </CardContent>
                  </Card>
                </a>
              ) : (
                <Card className="opacity-50">
                  <CardContent className="flex flex-col items-center gap-1 py-5">
                    <Phone className="h-5 w-5 text-muted-foreground" />
                    <span className="text-sm font-medium">Call Driver</span>
                  </CardContent>
                </Card>
              )}
            </div>
          </>
        )}
      </div>
    </RoleMobileLayout>
  );
}
