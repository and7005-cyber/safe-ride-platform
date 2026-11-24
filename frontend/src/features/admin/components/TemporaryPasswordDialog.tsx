import { Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import {
  TEMPORARY_PASSWORD_TITLE,
  TEMPORARY_PASSWORD_TYPING_HINT,
  groupInFours,
  temporaryPasswordRevealText,
} from "@/features/auth/authCopy";

/**
 * Reveal-once dialog for a parent's temporary password (hardening U9; the
 * StaffPage reveal, plus DL6): the server keeps only a hash, so this is the
 * single chance to read it out. Shown in groups of four for reading over the
 * phone; Copy gives the ungrouped value. The parent may type it either way —
 * any case, with or without the spaces — and the server accepts it.
 */
export function TemporaryPasswordDialog({
  reveal,
  onClose,
}: {
  reveal: { who: string; password: string } | null;
  onClose: () => void;
}) {
  const { toast } = useToast();

  const copy = async (password: string) => {
    try {
      await navigator.clipboard.writeText(password);
      toast({ title: "Password copied" });
    } catch {
      toast({
        title: "Copy failed",
        description: "Copy the password manually.",
        variant: "destructive",
      });
    }
  };

  return (
    <Dialog open={!!reveal} onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent className="max-w-sm" data-testid="temp-password-dialog">
        <DialogHeader>
          <DialogTitle>{TEMPORARY_PASSWORD_TITLE}</DialogTitle>
        </DialogHeader>
        {reveal && (
          <div className="space-y-3">
            <DialogDescription>{temporaryPasswordRevealText(reveal.who)}</DialogDescription>
            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted px-4 py-3">
              <span
                className="font-mono text-lg font-semibold tracking-wider"
                data-testid="temp-password"
                data-value={reveal.password}
                aria-label={`Temporary password ${reveal.password.split("").join(" ")}`}
              >
                {groupInFours(reveal.password)}
              </span>
              <Button variant="outline" size="sm" onClick={() => copy(reveal.password)}>
                <Copy className="h-4 w-4" /> Copy
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{TEMPORARY_PASSWORD_TYPING_HINT}</p>
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
