import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bus,
  Check,
  ChevronsUpDown,
  ClipboardList,
  Clock,
  GraduationCap,
  Heart,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  Map,
  Menu,
  Route as RouteIcon,
  Settings,
  ShieldCheck,
  UserCog,
  UserPlus,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { api } from "@/lib/apiClient";
import {
  membershipAt,
  staffMemberships,
  useAuth,
  useIsDirector,
  type SupportSession,
} from "@/lib/auth";
import { clearActiveSchoolId, useActiveSchoolId } from "@/lib/school";
import { useSwitchSchool, useUnreadAlerts } from "@/lib/queries";

// Four hours: the server's hard support-session lifetime (U13/AE29) — shown
// as "time left" on the banner; the lazy janitor settles the truth.
const SUPPORT_SESSION_HOURS = 4;

function timeLeftText(startedAt: string | null, now: number): string | null {
  if (!startedAt) return null;
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started)) return null;
  const ms = started + SUPPORT_SESSION_HOURS * 3_600_000 - now;
  if (ms <= 0) return "ending…";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours === 0 && minutes === 0) return "less than a minute left";
  return hours > 0 ? `${hours}h ${minutes}m left` : `${minutes}m left`;
}

/** The persistent step-in banner (U13/AE29): on EVERY admin page while a
 * provider works inside a school — who, where, why, how long is left, the
 * way back to the school list, and Step out. */
function SupportBanner({ support }: { support: SupportSession }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { refresh } = useAuth();
  const [busy, setBusy] = useState(false);
  // Minute tick so "time left" stays honest while the tab sits open.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const stepOut = async () => {
    setBusy(true);
    try {
      // Idempotent on the server: racing the four-hour janitor or a
      // supersede still answers ok — the outcome is the same, we leave.
      await api.post("/api/provider/step-out");
    } catch {
      // A failed call must not trap the provider inside the school UI; the
      // store is cleared either way and scoped calls would 403.
    }
    const schoolId = support.schoolId;
    clearActiveSchoolId();
    await qc.cancelQueries({ queryKey: ["school", schoolId] });
    qc.removeQueries({ queryKey: ["school", schoolId] });
    await refresh();
    navigate("/provider");
  };

  const timeLeft = timeLeftText(support.startedAt, now);

  return (
    <div
      className="border-b border-warning/40 bg-warning/15"
      data-testid="support-banner"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
        <LifeBuoy className="h-4 w-4 shrink-0 text-warning" />
        <span>
          Working in{" "}
          <span className="font-medium">{support.schoolName ?? "this school"}</span>{" "}
          <span className="font-mono text-xs text-muted-foreground">
            {support.schoolCode ?? ""}
          </span>{" "}
          as SafeRide
          {support.reason ? (
            <span className="text-muted-foreground"> — {support.reason}</span>
          ) : null}
        </span>
        {timeLeft && (
          <span className="text-xs text-muted-foreground" data-testid="support-time-left">
            {timeLeft}
          </span>
        )}
        <span className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          disabled={busy}
          onClick={() => navigate("/provider")}
          data-testid="support-school-list"
        >
          School list
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2"
          disabled={busy}
          onClick={() => void stepOut()}
          data-testid="support-step-out"
        >
          {busy ? "Stepping out…" : "Step out"}
        </Button>
      </div>
    </div>
  );
}

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/fleet-map", label: "Fleet Map", icon: Map },
  { to: "/buses", label: "Buses", icon: Bus },
  { to: "/routes", label: "Routes", icon: RouteIcon },
  { to: "/fleet-plan", label: "Fleet Plan", icon: ClipboardList },
  { to: "/students", label: "Students", icon: GraduationCap },
  { to: "/runs", label: "Run History", icon: Clock },
  { to: "/parents", label: "Parents", icon: Users },
  { to: "/drivers", label: "Drivers", icon: UserCog },
  { to: "/alerts", label: "Alerts", icon: Bell },
];

export function AdminLayout({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { user, signOut } = useAuth();
  const { data: unread } = useUnreadAlerts();
  const [open, setOpen] = useState(false);
  const unreadCount = unread?.count ?? 0;

  const activeSchoolId = useActiveSchoolId();
  const memberships = staffMemberships(user);
  // A stepped-in provider has no membership: the support session names the
  // school for the sidebar card (U13); staff keep their membership row.
  const support =
    user?.provider && user.supportSession?.schoolId === activeSchoolId
      ? user.supportSession
      : null;
  const active =
    membershipAt(user, activeSchoolId) ??
    (support
      ? {
          schoolId: support.schoolId,
          schoolName: support.schoolName,
          schoolCode: support.schoolCode,
          role: "director" as const,
        }
      : null);
  const isDirector = useIsDirector();
  const switchSchool = useSwitchSchool();

  // Staff (director-only) and Settings join the nav; the Schools page and
  // every school picker are gone — the console works inside the active school.
  const nav = [
    ...NAV,
    ...(isDirector ? [{ to: "/staff", label: "Staff", icon: UserPlus }] : []),
    { to: "/settings", label: "Settings", icon: Settings },
    // Staff who are ALSO a parent get a shortcut to their own parent view.
    // /me exposes no parent-link signal for staff, so the legacy role is the
    // only available cue (a limitation noted in U12).
    ...(user?.role === "parent" ? [{ to: "/parent", label: "Parent view", icon: Heart }] : []),
  ];

  const handleSignOut = async () => {
    await signOut();
    navigate("/auth");
  };

  const schoolCard = (
    <div className="mb-3 rounded-lg bg-sidebar-accent/40 px-3 py-2" data-testid="active-school-card">
      <p className="text-sm font-medium">{active?.schoolName ?? "—"}</p>
      <p className="text-xs text-sidebar-foreground/70">
        Code: {active?.schoolCode ?? "—"}
      </p>
    </div>
  );

  const sidebar = (
    <aside className="flex h-full w-64 flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <div>
          <p className="font-heading text-base font-semibold leading-tight">SafeRide</p>
          <p className="text-xs uppercase tracking-wider text-sidebar-foreground/60">Kenya</p>
        </div>
      </div>
      <p className="px-5 pb-1 pt-2 text-xs font-medium uppercase tracking-wider text-sidebar-foreground/50">
        Management
      </p>
      <nav className="flex-1 space-y-1 px-3 py-2">
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={(item as { end?: boolean }).end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="flex-1">{item.label}</span>
              {item.to === "/alerts" && unreadCount > 0 && (
                <Badge variant="warning" className="h-5 min-w-5 justify-center px-1.5">
                  {unreadCount}
                </Badge>
              )}
            </NavLink>
          );
        })}
      </nav>
      <div className="border-t border-sidebar-border px-4 py-4">
        {/* The active school (name + code). With several memberships the card
            becomes the switcher (U12/R4): switching drops the old school's
            cache and lands on the dashboard — never a mixed screen. */}
        {memberships.length > 1 ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="w-full text-left"
                data-testid="school-switcher"
                aria-label="Switch school"
              >
                <div className="mb-3 flex items-center justify-between gap-2 rounded-lg bg-sidebar-accent/40 px-3 py-2 hover:bg-sidebar-accent/60">
                  <div data-testid="active-school-card">
                    <p className="text-sm font-medium">{active?.schoolName ?? "—"}</p>
                    <p className="text-xs text-sidebar-foreground/70">
                      Code: {active?.schoolCode ?? "—"}
                    </p>
                  </div>
                  <ChevronsUpDown className="h-4 w-4 shrink-0 text-sidebar-foreground/60" />
                </div>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-56">
              <DropdownMenuLabel>Your schools</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {memberships.map((m) => (
                <DropdownMenuItem
                  key={m.schoolId}
                  data-testid={`switch-school-${m.schoolId}`}
                  onClick={() => void switchSchool(m.schoolId)}
                >
                  <span className="flex-1">
                    <span className="block text-sm">{m.schoolName ?? "School"}</span>
                    <span className="block text-xs text-muted-foreground">
                      {m.schoolCode ?? "—"}
                    </span>
                  </span>
                  {m.schoolId === activeSchoolId && <Check className="h-4 w-4" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          schoolCard
        )}
        <Button
          variant="ghost"
          className="w-full justify-start gap-2 text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground"
          onClick={handleSignOut}
        >
          <LogOut className="h-4 w-4" /> Sign Out
        </Button>
      </div>
    </aside>
  );

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* U13/AE29: the persistent step-in banner spans every admin page —
          above sidebar and content, impossible to scroll away. */}
      {support && <SupportBanner support={support} />}
      <div className="flex flex-1">
      <div className="hidden md:block">{sidebar}</div>
      {open && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-0 h-full">{sidebar}</div>
        </div>
      )}
      <div className="flex flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b bg-card px-4">
          <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setOpen(true)}>
            <Menu className="h-5 w-5" />
          </Button>
          <div className="flex-1" />
          <button
            className="relative"
            aria-label="Notifications"
            title="View alerts"
            onClick={() => navigate("/alerts")}
          >
            <Bell className="h-5 w-5 text-muted-foreground" />
            {unreadCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">
                {unreadCount}
              </span>
            )}
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
                title={user?.fullName ?? user?.email ?? "Account"}
                aria-label="Account menu"
              >
                {(user?.fullName ?? user?.email ?? "A").slice(0, 2).toUpperCase()}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                <p className="text-sm font-semibold leading-tight">{user?.fullName ?? "Account"}</p>
                {user?.email && (
                  <p className="text-xs font-normal text-muted-foreground">{user.email}</p>
                )}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleSignOut}>
                <LogOut className="h-4 w-4" /> Sign Out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
      </div>
    </div>
  );
}
