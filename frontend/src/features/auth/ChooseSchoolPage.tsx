import { School } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { setActiveSchoolId } from "@/lib/school";
import { staffMemberships, useAuth } from "@/lib/auth";

// First landing with several memberships and no school in this tab (U12/R4).
// Rendered by ProtectedRoute IN PLACE of the requested page, so the deep-link
// destination is preserved: picking a school only sets the per-tab store and
// the originally requested URL renders scoped to it.

const ROLE_LABEL: Record<string, string> = {
  director: "Director",
  coordinator: "Coordinator",
};

export function ChooseSchoolPage() {
  const { user, signOut } = useAuth();
  const schools = staffMemberships(user);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-2 text-center">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <School className="h-7 w-7" />
          </div>
          <h1 className="font-heading text-2xl font-bold text-foreground">SafeRide</h1>
          <p className="text-sm text-muted-foreground">Choose a school to work in</p>
        </div>
        <div className="space-y-3">
          {schools.map((m) => (
            <Card key={m.schoolId} data-testid={`choose-school-${m.schoolId}`}>
              <CardContent className="p-0">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-3 rounded-lg p-4 text-left hover:bg-accent"
                  onClick={() => setActiveSchoolId(m.schoolId)}
                >
                  <span>
                    <span className="block font-heading font-semibold">
                      {m.schoolName ?? "School"}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      Code: {m.schoolCode ?? "—"}
                    </span>
                  </span>
                  <Badge variant="secondary">{ROLE_LABEL[m.role] ?? m.role}</Badge>
                </button>
              </CardContent>
            </Card>
          ))}
        </div>
        <p className="text-center text-xs text-muted-foreground">
          Each browser tab works in one school at a time — you can switch from
          the sidebar later.
        </p>
        <div className="text-center">
          <button
            type="button"
            className="text-sm text-muted-foreground hover:underline"
            onClick={() => void signOut()}
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
