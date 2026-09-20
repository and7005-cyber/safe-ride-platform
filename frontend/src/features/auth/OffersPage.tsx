import { useState } from "react";
import { format } from "date-fns";
import { Mail } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { useAuth, type PendingOffer } from "@/lib/auth";

// Pending role offers (U12/AE15). Rendered by ProtectedRoute before anything
// else whenever /me carries pendingOffers: an offer is an explicit accept —
// nobody is silently added to a school. The card shows the school NAME and
// non-editable CODE plus who offered the role and when, so a spoofed-name
// offer is recognisable by its code (the plan's phishing mitigation).

const ROLE_LABEL: Record<string, string> = {
  director: "Director",
  coordinator: "Coordinator",
  driver: "Driver",
};

function offeredAtText(offeredAt: string | null): string | null {
  if (!offeredAt) return null;
  const date = new Date(offeredAt);
  return Number.isNaN(date.getTime()) ? null : format(date, "d MMM yyyy");
}

export function OffersPage() {
  const { user, refresh, signOut } = useAuth();
  const { toast } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const answer = async (offer: PendingOffer, verb: "accept" | "decline") => {
    setBusyId(offer.id);
    try {
      await api.post(`/api/auth/offers/${offer.id}/${verb}`);
      toast({
        title:
          verb === "accept"
            ? `You joined ${offer.schoolName ?? "the school"}`
            : "Offer declined",
      });
      await refresh();
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusyId(null);
    }
  };

  const offers = user?.pendingOffers ?? [];

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-2 text-center">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Mail className="h-7 w-7" />
          </div>
          <h1 className="font-heading text-2xl font-bold text-foreground">SafeRide</h1>
          <p className="text-sm text-muted-foreground">
            You have {offers.length === 1 ? "a pending invitation" : "pending invitations"}
          </p>
        </div>
        {offers.map((offer) => (
          <Card key={offer.id} data-testid={`offer-${offer.id}`}>
            <CardHeader className="pb-2">
              <CardTitle className="font-heading text-lg">
                {offer.schoolName ?? "A school"}
              </CardTitle>
              <CardDescription>
                School code:{" "}
                <span className="font-mono font-medium text-foreground">
                  {offer.schoolCode ?? "—"}
                </span>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="secondary">{ROLE_LABEL[offer.role] ?? offer.role}</Badge>
                <span className="text-muted-foreground">
                  Offered{offer.offeredBy ? ` by ${offer.offeredBy}` : ""}
                  {offeredAtText(offer.offeredAt)
                    ? ` on ${offeredAtText(offer.offeredAt)}`
                    : ""}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                Check the school code with the person who invited you before
                accepting — accepting gives that school's console access to
                your account.
              </p>
              <div className="flex gap-2">
                <Button
                  className="flex-1"
                  disabled={busyId !== null}
                  onClick={() => answer(offer, "accept")}
                >
                  Accept
                </Button>
                <Button
                  variant="outline"
                  className="flex-1"
                  disabled={busyId !== null}
                  onClick={() => answer(offer, "decline")}
                >
                  Decline
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
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
