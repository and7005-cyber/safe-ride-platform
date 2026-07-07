import { useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Bus, Check, Trash2, TriangleAlert, User } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/apiClient";
import { useIncidents } from "@/lib/queries";

// Admin-side labels (note: traffic differs from the parent-side "Traffic Delay";
// arrival has no mapping here — the live admin feed shows the raw "arrival").
// 'cancellation' is the parent Cancel-a-Ride alert (R17/U14): student-stamped,
// bus context from the covered route, the acting parent named in the description.
const TYPE_LABEL: Record<string, string> = {
  breakdown: "Vehicle Breakdown",
  accident: "Road Accident",
  student: "Student Issue",
  traffic: "Heavy Traffic / Delay",
  other: "Notice",
  cancellation: "Ride Cancellation",
};

const TYPE_VARIANT: Record<string, "destructive" | "warning" | "secondary" | "success"> = {
  breakdown: "destructive",
  accident: "destructive",
  student: "warning",
  traffic: "warning",
  arrival: "success",
  other: "secondary",
  cancellation: "warning",
};

export function AlertsPage() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data: incidents = [] } = useIncidents();
  const unacked = incidents.filter((i: any) => !i.acknowledged).length;

  const acknowledge = async (id: string) => {
    await api.post(`/api/incidents/${id}/acknowledge`);
    await qc.invalidateQueries({ queryKey: ["incidents"] });
    await qc.invalidateQueries({ queryKey: ["unread-alerts"] });
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this alert?",
      description: "This permanently removes the alert from the feed.",
      confirmLabel: "Delete alert",
    }))) return;
    await api.del(`/api/incidents/${id}`);
    await qc.invalidateQueries({ queryKey: ["incidents"] });
    await qc.invalidateQueries({ queryKey: ["unread-alerts"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Driver Alerts" subtitle={unacked > 0 ? `${unacked} unacknowledged` : "All caught up"} />

      <div className="space-y-3">
        {incidents.length === 0 ? (
          <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">No alerts right now.</CardContent></Card>
        ) : (
          incidents.map((incident: any) => (
            <Card key={incident.id} className={incident.acknowledged ? "opacity-60" : ""}>
              <CardContent className="flex items-start gap-4 p-4" style={{ minHeight: 80 }}>
                <div className="mt-0.5 text-muted-foreground"><TriangleAlert className="h-5 w-5" /></div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{TYPE_LABEL[incident.type] ?? incident.type}</span>
                    {!incident.acknowledged ? (
                      <Badge variant="warning" className="px-1.5 py-0 text-[10px]">New</Badge>
                    ) : (
                      <Badge variant="outline">Acknowledged</Badge>
                    )}
                  </div>
                  <p className="text-sm">{incident.description}</p>
                  <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    {incident.driver_name && (
                      <span className="flex items-center gap-1"><User className="h-3 w-3" /> {incident.driver_name}</span>
                    )}
                    {incident.bus_name && (
                      <span className="flex items-center gap-1"><Bus className="h-3 w-3" /> {incident.bus_name}</span>
                    )}
                    {incident.created_at && (
                      <span>{formatDistanceToNow(new Date(incident.created_at), { addSuffix: true })}</span>
                    )}
                  </p>
                </div>
                <div className="flex gap-1">
                  {!incident.acknowledged && (
                    <Button variant="outline" size="sm" onClick={() => acknowledge(incident.id)}>
                      <Check className="h-4 w-4" /> Ack
                    </Button>
                  )}
                  <Button variant="ghost" size="icon" onClick={() => remove(incident.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
