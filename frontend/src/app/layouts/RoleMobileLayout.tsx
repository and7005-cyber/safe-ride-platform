import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Bus, LogOut, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

// Shared mobile shell for driver and parent. The only visual difference is the
// logo tile color: driver = bg-primary, parent = bg-accent (live parity).
export function RoleMobileLayout({
  children,
  nav,
  variant,
  title,
}: {
  children: ReactNode;
  nav: NavItem[];
  variant: "primary" | "accent";
  title: string;
}) {
  const navigate = useNavigate();
  const { signOut } = useAuth();
  const logoClass = variant === "primary"
    ? "bg-primary text-primary-foreground"
    : "bg-accent text-accent-foreground";

  const handleSignOut = async () => {
    await signOut();
    navigate("/auth");
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col bg-background">
      <header className="flex items-center justify-between border-b bg-card px-4 py-3">
        <div className="flex items-center gap-2">
          <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", logoClass)}>
            <Bus className="h-4 w-4" />
          </div>
          <span className="font-heading text-base font-semibold">{title}</span>
        </div>
        <button onClick={handleSignOut} className="text-muted-foreground hover:text-destructive" aria-label="Sign out">
          <LogOut className="h-5 w-5" />
        </button>
      </header>

      <main className="flex-1 overflow-auto p-4 pb-20">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 mx-auto flex max-w-md items-center justify-around border-t bg-card py-2">
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex flex-col items-center gap-0.5 px-3 py-1 text-xs",
                  isActive ? "text-primary" : "text-muted-foreground",
                )
              }
            >
              <Icon className="h-5 w-5" />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}
