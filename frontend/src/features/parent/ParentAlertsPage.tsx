import { formatDistanceToNow } from "date-fns";
import { TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { PARENT_NAV, useParentAlerts } from "@/features/parent/parentHooks";

// Parent-side labels (traffic = "Traffic Delay", distinct from the admin label).
const TYPE_LABEL: Record<string, string> = {
  breakdown: "Vehicle Breakdown",
  accident: "Road Accident",
  student: "Student Issue",
  traffic: "Traffic Delay",
  arrival: "Bus Arrived at School",
  other: "Notice",
};

const TYPE_VARIANT: Record<string, "destructive" | "warning" | "secondary" | "success"> = {
  breakdown: "destructive",
  accident: "destructive",
  student: "warning",
  traffic: "warning",
  arrival: "success",
  other: "secondary",
};

export function ParentAlertsPage() {
  const { data: alerts = [] } = useParentAlerts();

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="Alerts">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="font-heading text-xl font-bold">Notifications</h1>
          {alerts.length > 0 && (
            <Badge variant="secondary" className="px-2">{alerts.length}</Badge>
          )}
        </div>
        {alerts.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No alerts for your children's buses.</CardContent></Card>
        ) : (
          <div className="space-y-2">
            {alerts.map((a: any) => (
              <Card key={a.id}>
                <CardContent className="flex gap-3 p-4">
                  <TriangleAlert className="mt-0.5 h-5 w-5 text-muted-foreground" />
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Badge variant={TYPE_VARIANT[a.type] ?? "secondary"}>{TYPE_LABEL[a.type] ?? a.type}</Badge>
                      <span className="text-sm font-medium">{a.bus_name ?? ""}</span>
                    </div>
                    <p className="text-sm">{a.description}</p>
                    <p className="text-xs text-muted-foreground">
                      {a.created_at ? formatDistanceToNow(new Date(a.created_at), { addSuffix: true }) : ""}
                    </p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </RoleMobileLayout>
  );
}
