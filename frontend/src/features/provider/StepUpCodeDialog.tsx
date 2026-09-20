import { useCallback, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// Promise-based authenticator-code prompt for step-up-gated actions (U13):
// remove account, reset a peer's second factor — actions whose trigger is a
// row button, not a form that could host the code input itself. Mount the
// returned `dialog` once on the page and await `promptCode()`; it resolves
// the entered code, or null when the person backs out. The useConfirm
// promise pattern, specialised to a 6-digit code.

export function useStepUpPrompt(): {
  promptCode: (message?: string) => Promise<string | null>;
  stepUpDialog: ReactNode;
} {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | undefined>(undefined);
  const [code, setCode] = useState("");
  const resolver = useRef<((value: string | null) => void) | null>(null);

  const promptCode = useCallback((nextMessage?: string) => {
    setMessage(nextMessage);
    setCode("");
    setOpen(true);
    return new Promise<string | null>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  const settle = (value: string | null) => {
    setOpen(false);
    resolver.current?.(value);
    resolver.current = null;
  };

  const stepUpDialog = (
    <Dialog open={open} onOpenChange={(next) => (next ? null : settle(null))}>
      <DialogContent className="max-w-sm" data-testid="step-up-dialog">
        <DialogHeader>
          <DialogTitle>Confirm with your code</DialogTitle>
          <DialogDescription>
            {message ??
              "This action needs a fresh authenticator code from your app."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (code.length === 6) settle(code);
          }}
        >
          <Label htmlFor="step-up-code">Authenticator code</Label>
          <Input
            id="step-up-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            placeholder="123456"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            data-testid="step-up-code"
          />
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => settle(null)}>
            Cancel
          </Button>
          <Button
            disabled={code.length !== 6}
            onClick={() => settle(code)}
            data-testid="step-up-confirm"
          >
            Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  return { promptCode, stepUpDialog };
}
