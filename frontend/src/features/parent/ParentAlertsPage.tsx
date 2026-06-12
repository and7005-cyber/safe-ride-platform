import { useEffect, useMemo, useRef } from "react";
import { formatDistanceToNow } from "date-fns";
import { Bell, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import {
  PARENT_NAV,
  useMarkNotificationsRead,
  useParentAlerts,
  useParentNotifications,
} from "@/features/parent/parentHooks";

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

// Typed parent notifications (the push feed mirror).
const NOTIFICATION_LABEL: Record<string, string> = {
  "run-started": "Bus On The Way",
  "student-boarded": "Boarded the Bus",
  "bus-approaching": "Bus Approaching",
  "reached-school": "Arrived at School",
  "on-way-home": "On the Way Home",
  "dropped-off": "Dropped Off",
  incident: "Bus Incident",
  custom: "Notice",
};

const NOTIFICATION_VARIANT: Record<string, "destructive" | "warning" | "secondary" | "success"> = {
  "run-started": "secondary",
  "student-boarded": "success",
  "bus-approaching": "warning",
  "reached-school": "success",
  "on-way-home": "secondary",
  "dropped-off": "success",
  incident: "destructive",
  custom: "secondary",
};

interface FeedItem {
  key: string;
  kind: "incident" | "notification";
  label: string;
  variant: "destructive" | "warning" | "secondary" | "success";
  heading: string;
  body: string;
  createdAt: string | null;
  unread: boolean;
}

export function ParentAlertsPage() {
  const { data: alerts = [] } = useParentAlerts();
  const { data: notifications = [] } = useParentNotifications();
  const markRead = useMarkNotificationsRead();
  const markedOnce = useRef(false);

  // Opening the page clears the unread state (once per visit).
  const hasUnread = notifications.some((n: any) => !n.read);
  useEffect(() => {
    if (hasUnread && !markedOnce.current) {
      markedOnce.current = true;
      markRead.mutate();
    }
  }, [hasUnread, markRead]);

  const feed = useMemo<FeedItem[]>(() => {
    const incidentItems: FeedItem[] = alerts.map((a: any) => ({
      key: `incident-${a.id}`,
      kind: "incident" as const,
      label: TYPE_LABEL[a.type] ?? a.type,
      variant: TYPE_VARIANT[a.type] ?? "secondary",
      heading: a.bus_name ?? "",
      body: a.description ?? "",
      createdAt: a.created_at ?? null,
      unread: false,
    }));
    // Incident pushes mirror incidents already in the list; skip them here.
    const notificationItems: FeedItem[] = notifications
      .filter((n: any) => n.type !== "incident")
      .map((n: any) => ({
        key: `notification-${n.id}`,
        kind: "notification" as const,
        label: NOTIFICATION_LABEL[n.type] ?? n.type,
        variant: NOTIFICATION_VARIANT[n.type] ?? "secondary",
        heading: n.title ?? "",
        body: n.body ?? "",
        createdAt: n.created_at ?? null,
        unread: !n.read,
      }));
    return [...incidentItems, ...notificationItems].sort((a, b) => {
      const ta = a.createdAt ? new Date(a.createdAt).getTime() : 0;
      const tb = b.createdAt ? new Date(b.createdAt).getTime() : 0;
      return tb - ta;
    });
  }, [alerts, notifications]);

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="Alerts">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="font-heading text-xl font-bold">Notifications</h1>
          {feed.length > 0 && (
            <Badge variant="secondary" className="px-2">{feed.length}</Badge>
          )}
        </div>
        {feed.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No alerts for your children's buses.</CardContent></Card>
        ) : (
          <div className="space-y-2">
            {feed.map((item) => (
              <Card key={item.key} className={item.unread ? "border-primary/40" : undefined}>
                <CardContent className="flex gap-3 p-4">
                  {item.kind === "incident" ? (
                    <TriangleAlert className="mt-0.5 h-5 w-5 text-muted-foreground" />
                  ) : (
                    <Bell className="mt-0.5 h-5 w-5 text-muted-foreground" />
                  )}
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Badge variant={item.variant}>{item.label}</Badge>
                      <span className="text-sm font-medium">{item.heading}</span>
                      {item.unread && (
                        <span className="h-2 w-2 rounded-full bg-primary" aria-label="unread" />
                      )}
                    </div>
                    <p className="text-sm">{item.body}</p>
                    <p className="text-xs text-muted-foreground">
                      {item.createdAt
                        ? formatDistanceToNow(new Date(item.createdAt), { addSuffix: true })
                        : ""}
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
