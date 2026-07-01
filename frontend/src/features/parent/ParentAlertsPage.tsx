import { useEffect, useMemo, useRef, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { Bell, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import {
  HISTORY_WINDOW,
  PARENT_NAV,
  RECENT_WINDOW,
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
  "student-absent": "Marked Absent",
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
  "student-absent": "destructive",
  incident: "destructive",
  custom: "secondary",
};

// Type filter over the merged taxonomy (R33): notification types plus incident
// types; the never-produced `custom` type is excluded. Values stay raw type
// strings — the two namespaces don't collide.
const TYPE_FILTER_OPTIONS: { value: string; label: string }[] = [
  ...Object.entries(NOTIFICATION_LABEL).filter(([type]) => type !== "custom"),
  ...Object.entries(TYPE_LABEL),
].map(([value, label]) => ({ value, label }));

const PERIODS = [
  { value: "all", label: "All" },
  { value: "morning", label: "Morning" },
  { value: "afternoon", label: "Afternoon" },
] as const;

type Period = (typeof PERIODS)[number]["value"];

interface FeedItem {
  key: string;
  kind: "incident" | "notification";
  type: string;
  runType: string | null;
  label: string;
  variant: "destructive" | "warning" | "secondary" | "success";
  heading: string;
  body: string;
  createdAt: string | null;
  unread: boolean;
}

export function ParentAlertsPage() {
  // Recent = rolling 24h, History = last 7 days (R35); both server-windowed,
  // no rows are ever deleted.
  const [tab, setTab] = useState<"recent" | "history">("recent");
  const feedWindow = tab === "history" ? HISTORY_WINDOW : RECENT_WINDOW;
  const { data: alerts = [] } = useParentAlerts(feedWindow);
  const { data: notifications = [] } = useParentNotifications(feedWindow);
  const [period, setPeriod] = useState<Period>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const markRead = useMarkNotificationsRead();
  const markedOnce = useRef(false);

  // Opening the page clears the unread state (once per visit). Mark-read stays
  // global (R35); History simply shows read items plainly.
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
      type: a.type,
      runType: a.run_type ?? null,
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
        type: n.type,
        runType: n.run_type ?? null,
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

  // Client-side filters over the fetched window (R33). Rows without a
  // run_type only show under All.
  const visible = useMemo(
    () =>
      feed.filter(
        (item) =>
          (period === "all" || item.runType === period) &&
          (typeFilter === "all" || item.type === typeFilter),
      ),
    [feed, period, typeFilter],
  );

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="Alerts">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="font-heading text-xl font-bold">Notifications</h1>
          {visible.length > 0 && (
            <Badge variant="secondary" className="px-2">{visible.length}</Badge>
          )}
        </div>

        <Tabs value={tab} onValueChange={(v) => setTab(v as "recent" | "history")}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="recent">Recent</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="space-y-2">
          <div className="flex gap-2" role="group" aria-label="Filter by period">
            {PERIODS.map((p) => (
              <Button
                key={p.value}
                size="sm"
                variant={period === p.value ? "default" : "outline"}
                className="flex-1"
                onClick={() => setPeriod(p.value)}
              >
                {p.label}
              </Button>
            ))}
          </div>
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger aria-label="Filter by type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {TYPE_FILTER_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {visible.length === 0 ? (
          <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">
            {feed.length === 0
              ? tab === "history"
                ? "Nothing in the last 7 days."
                : "No alerts for your children's buses."
              : "No alerts match the selected filters."}
          </CardContent></Card>
        ) : (
          <div className="space-y-2">
            {visible.map((item) => (
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
