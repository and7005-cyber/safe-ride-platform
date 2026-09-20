import { type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { ArrowRight, Building2, LogOut, ScrollText, ShieldCheck, Users } from "lucide-react";
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
import { useAuth } from "@/lib/auth";

// The provider console shell (U13/R22): Kuumbai's cross-school surface. A
// deliberately DIFFERENT shape from the school console — a top bar, no
// sidebar, no school card — so a provider always knows at a glance whether
// they are outside every school (here) or inside one (the admin layout with
// its support banner, entered only through a step-in).

const NAV = [
  { to: "/provider", label: "Schools", icon: Building2, end: true },
  { to: "/provider/accounts", label: "Provider accounts", icon: Users },
  { to: "/provider/audit", label: "Audit", icon: ScrollText },
];

export function ProviderLayout({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { user, signOut } = useAuth();

  const handleSignOut = async () => {
    await signOut();
    navigate("/auth");
  };

  return (
    <div className="flex min-h-screen flex-col bg-background" data-testid="provider-layout">
      <header className="border-b bg-sidebar text-sidebar-foreground">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
              <ShieldCheck className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <p className="font-heading text-sm font-semibold">SafeRide</p>
              <p className="text-[10px] uppercase tracking-wider text-sidebar-foreground/60">
                Provider console
              </p>
            </div>
          </div>
          <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
            {NAV.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
                    )
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="hidden sm:inline">{item.label}</span>
                </NavLink>
              );
            })}
          </nav>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
                title={user?.fullName ?? user?.email ?? "Account"}
                aria-label="Account menu"
              >
                {(user?.fullName ?? user?.email ?? "P").slice(0, 2).toUpperCase()}
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
        </div>
      </header>
      {user?.supportSession && (
        // A live step-in while browsing the console: name it and offer the
        // way back in — the admin surface owns the Step out action (U13).
        <div className="border-b border-warning/40 bg-warning/15">
          <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
            <Badge variant="warning">Stepped in</Badge>
            <span>
              You are working in{" "}
              <span className="font-medium">
                {user.supportSession.schoolName ?? "a school"}
              </span>{" "}
              as SafeRide.
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2"
              onClick={() => navigate("/")}
              data-testid="resume-step-in"
            >
              Open its console <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
      <main className="mx-auto w-full max-w-6xl flex-1 p-6">{children}</main>
    </div>
  );
}
