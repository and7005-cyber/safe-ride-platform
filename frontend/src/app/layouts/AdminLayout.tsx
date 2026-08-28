import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
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
import { membershipAt, staffMemberships, useAuth, useIsDirector } from "@/lib/auth";
import { useActiveSchoolId } from "@/lib/school";
import { useSwitchSchool, useUnreadAlerts } from "@/lib/queries";

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
  const active = membershipAt(user, activeSchoolId);
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
    <div className="flex min-h-screen bg-background">
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
  );
}
