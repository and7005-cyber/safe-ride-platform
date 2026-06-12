import { useNavigate } from "react-router-dom";
import { Bell, BellOff, Bus, LogOut, Mail, MapPin, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GraduationCap } from "lucide-react";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { PARENT_NAV, useParentProfile } from "@/features/parent/parentHooks";
import { usePushNotifications } from "@/lib/usePushNotifications";
import { useAuth } from "@/lib/auth";

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

export function ParentProfilePage() {
  const navigate = useNavigate();
  const { signOut } = useAuth();
  const { data } = useParentProfile();
  const push = usePushNotifications();
  const profile = data?.profile;
  const children = data?.children ?? [];

  const pushLabel = () => {
    if (!push.supported) return "Push not supported on this device";
    if (push.permission === "denied") return "Notifications blocked in browser";
    if (push.subscribed) return "Disable Push Notifications";
    return "Enable Push Notifications";
  };

  const handleSignOut = async () => {
    await signOut();
    navigate("/auth");
  };

  return (
    <RoleMobileLayout nav={PARENT_NAV} variant="accent" title="Profile">
      <div className="space-y-4">
        <Card>
          <CardContent className="flex items-center gap-3 p-5">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
              <User className="h-6 w-6" />
            </span>
            <div className="leading-tight">
              <p className="font-heading text-lg font-semibold">{profile?.full_name ?? "Parent"}</p>
              <p className="flex items-center gap-1 text-sm text-muted-foreground">
                <Mail className="h-3.5 w-3.5" /> {profile?.email}
              </p>
              {profile?.phone && <p className="text-sm text-muted-foreground">{profile.phone}</p>}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <GraduationCap className="h-4 w-4 text-primary" /> My Children
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {children.length === 0 ? (
              <p className="text-sm text-muted-foreground">No children linked.</p>
            ) : (
              children.map((c: any) => (
                <div key={c.id} className="flex items-center gap-3 rounded-lg bg-muted/40 p-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground">
                    {initials(c.name)}
                  </span>
                  <div className="leading-tight">
                    <p className="font-medium">{c.name}</p>
                    <p className="flex items-center gap-1 text-xs text-muted-foreground">
                      {c.grade ?? ""}
                      {c.bus_name && (<><span>•</span><Bus className="h-3 w-3" /> {c.bus_name}</>)}
                    </p>
                    {c.home_address && (
                      <p className="flex items-center gap-1 text-xs text-muted-foreground">
                        <MapPin className="h-3 w-3" /> {c.home_address}
                      </p>
                    )}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Button
          variant="outline"
          className="w-full justify-start gap-2"
          disabled={!push.supported || push.permission === "denied"}
          onClick={() => (push.subscribed ? push.unsubscribe() : push.subscribe())}
        >
          {push.subscribed ? <BellOff className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
          {pushLabel()}
        </Button>

        <Button
          variant="outline"
          className="w-full justify-start gap-2 text-destructive hover:text-destructive"
          onClick={handleSignOut}
        >
          <LogOut className="h-4 w-4" /> Sign Out
        </Button>
      </div>
    </RoleMobileLayout>
  );
}
