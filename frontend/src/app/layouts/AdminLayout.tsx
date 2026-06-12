import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  Bell,
  Bus,
  Clock,
  GraduationCap,
  LayoutDashboard,
  LogOut,
  Map,
  MapPin,
  Menu,
  Route as RouteIcon,
  School,
  ShieldCheck,
  UserCog,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { useUnreadAlerts } from "@/lib/queries";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/fleet-map", label: "Fleet Map", icon: Map },
  { to: "/buses", label: "Buses", icon: Bus },
  { to: "/routes", label: "Routes", icon: RouteIcon },
  { to: "/students", label: "Students", icon: GraduationCap },
  { to: "/runs", label: "Run History", icon: Clock },
  { to: "/schools", label: "Schools", icon: School },
  { to: "/parent-assignments", label: "Parent Assignments", icon: MapPin },
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

  const handleSignOut = async () => {
    await signOut();
    navigate("/auth");
  };

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
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
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
        <div className="mb-3 rounded-lg bg-sidebar-accent/40 px-3 py-2">
          <p className="text-sm font-medium">Greenfield Academy</p>
          <p className="text-xs text-sidebar-foreground/70">Beta Programme</p>
        </div>
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
          <button className="relative" aria-label="Notifications">
            <Bell className="h-5 w-5 text-muted-foreground" />
            {unreadCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">
                {unreadCount}
              </span>
            )}
          </button>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
              {(user?.fullName ?? user?.email ?? "A").slice(0, 2).toUpperCase()}
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
