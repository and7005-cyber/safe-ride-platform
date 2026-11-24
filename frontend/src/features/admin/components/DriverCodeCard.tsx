import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { Copy, KeyRound, MessageSquareText, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { driverSignInLink, formatDriverCode } from "@/lib/driverSchool";
import { useSchoolKey } from "@/lib/queries";
import {
  COPY_DRIVER_CODE_LABEL,
  COPY_DRIVER_MESSAGE_LABEL,
  COPY_LINK_LABEL,
  DRIVER_CODE_CARD_LEAD,
  DRIVER_CODE_CARD_TITLE,
  QR_ACCESSIBLE_NAME,
  REGENERATED_TOAST,
  REGENERATE_CANCEL_LABEL,
  REGENERATE_CONFIRM_LABEL,
  REGENERATE_CONFIRM_TEXT,
  REGENERATE_CONFIRM_TITLE,
  REGENERATE_LABEL,
  SHARE_BY_LINK_TITLE,
  driverMessage,
} from "@/features/admin/driverCodeCopy";

/**
 * The school's driver code (hardening U10; DL5, DL7, DL17). Led by the large
 * grouped code and Copy — the typed code is what works on every phone, iOS's
 * installed app included — then the ready-to-send message, then the link and
 * its QR. Regenerate is director-only, like Delete, and asks first: links and
 * QR codes already sent stop working, phones already set up keep working.
 */
export function DriverCodeCard({
  school,
  isDirector,
}: {
  school: { name?: string | null; driver_code?: string | null } | undefined;
  isDirector: boolean;
}) {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const confirm = useConfirm();
  const { toast } = useToast();
  const [regenerating, setRegenerating] = useState(false);

  const code = school?.driver_code ? formatDriverCode(school.driver_code) : null;
  const link = school?.driver_code ? driverSignInLink(window.location.origin, school.driver_code) : null;

  const copy = async (text: string, done: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ title: done });
    } catch {
      toast({ title: "Copy failed", description: "Select the text and copy it manually.", variant: "destructive" });
    }
  };

  const regenerate = async () => {
    if (
      !(await confirm({
        title: REGENERATE_CONFIRM_TITLE,
        description: REGENERATE_CONFIRM_TEXT,
        confirmLabel: REGENERATE_CONFIRM_LABEL,
        cancelLabel: REGENERATE_CANCEL_LABEL,
      }))
    )
      return;
    setRegenerating(true);
    try {
      await api.post("/api/accounts/school/driver-code/regenerate");
      await qc.invalidateQueries({ queryKey: schoolKey("school-settings") });
      toast({ title: REGENERATED_TOAST });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <Card className="p-5" data-testid="driver-code-card" aria-labelledby="driver-code-card-title">
      <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
        <div className="space-y-3 min-w-0">
          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 id="driver-code-card-title" className="font-heading text-base font-semibold">
              {DRIVER_CODE_CARD_TITLE}
            </h2>
          </div>
          <p className="text-sm text-muted-foreground max-w-prose">{DRIVER_CODE_CARD_LEAD}</p>
          {code ? (
            <p className="font-mono text-3xl font-semibold tracking-[0.2em]" data-testid="driver-code-value">
              {code}
            </p>
          ) : (
            <Skeleton className="h-9 w-48" aria-label="Loading the driver code" />
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-10"
              disabled={!code}
              onClick={() => code && copy(code, "Driver code copied")}
            >
              <Copy className="h-4 w-4" aria-hidden="true" /> {COPY_DRIVER_CODE_LABEL}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-10"
              disabled={!code || !link}
              onClick={() =>
                code && link && copy(driverMessage(school?.name ?? "", code, link), "Message copied")
              }
            >
              <MessageSquareText className="h-4 w-4" aria-hidden="true" /> {COPY_DRIVER_MESSAGE_LABEL}
            </Button>
            {isDirector && (
              <Button
                variant="ghost"
                size="sm"
                className="h-10"
                disabled={!code || regenerating}
                onClick={regenerate}
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" /> {regenerating ? "Replacing…" : REGENERATE_LABEL}
              </Button>
            )}
          </div>
        </div>

        <div className="space-y-2 md:w-72">
          <h3 className="text-sm font-medium">{SHARE_BY_LINK_TITLE}</h3>
          {link ? (
            <div className="flex items-start gap-3">
              <div className="rounded-md border bg-white p-2 shrink-0">
                <QRCodeSVG
                  value={link}
                  size={112}
                  role="img"
                  aria-label={QR_ACCESSIBLE_NAME}
                  data-testid="driver-code-qr"
                />
              </div>
              <div className="min-w-0 space-y-2">
                <p className="break-all font-mono text-xs" data-testid="driver-code-link">
                  {link}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-10"
                  onClick={() => copy(link, "Link copied")}
                >
                  <Copy className="h-4 w-4" aria-hidden="true" /> {COPY_LINK_LABEL}
                </Button>
              </div>
            </div>
          ) : (
            <Skeleton className="h-28 w-full" aria-label="Loading the sign-in link" />
          )}
        </div>
      </div>
    </Card>
  );
}
