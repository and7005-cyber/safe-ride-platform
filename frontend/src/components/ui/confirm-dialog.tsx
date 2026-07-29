import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// App-wide "are you sure?" confirmation (#6). Mount <ConfirmProvider> once at
// the root; any component calls `const confirm = useConfirm()` then
// `if (!(await confirm({...}))) return;` before a destructive action.

export interface ConfirmOptions {
  title?: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  /**
   * Ask for a short written reason alongside the confirmation (U13/R9).
   *
   * The hand-over action needs one: recording that a child left the bus away
   * from their stop is only useful to the office if it says where and why, and
   * the dialog previously rendered a title, a description and two buttons with
   * nowhere to type. Confirmation stays blocked while it is empty — an
   * unexplained hand-over is the claim this action exists to avoid.
   */
  note?: {
    label: string;
    placeholder?: string;
    maxLength?: number;
  };
}

interface ConfirmFn {
  (opts?: ConfirmOptions & { note?: undefined }): Promise<boolean>;
  /** With a note requested, resolves to the text entered, or null if cancelled. */
  (opts: ConfirmOptions & { note: NonNullable<ConfirmOptions["note"]> }): Promise<string | null>;
}

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<ConfirmOptions>({});
  const [note, setNote] = useState("");
  const resolver = useRef<((value: any) => void) | null>(null);

  const confirm = useCallback(((options: ConfirmOptions = {}) => {
    setOpts(options);
    setNote("");
    setOpen(true);
    return new Promise((resolve) => {
      resolver.current = resolve;
    });
  }) as ConfirmFn, []);

  const settle = useCallback(
    (ok: boolean, text = "") => {
      setOpen(false);
      // A note dialog resolves to the text, so a caller can pass it straight to
      // the request; without one it stays the plain boolean every other caller
      // already awaits.
      resolver.current?.(opts.note ? (ok ? text : null) : ok);
      resolver.current = null;
    },
    [opts.note],
  );

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={open} onOpenChange={(next) => (next ? null : settle(false))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{opts.title ?? "Are you sure you want to cancel?"}</DialogTitle>
            {opts.description && <DialogDescription>{opts.description}</DialogDescription>}
          </DialogHeader>
          {opts.note && (
            <div className="space-y-1.5">
              <Label htmlFor="confirm-note">{opts.note.label}</Label>
              <Textarea
                id="confirm-note"
                data-testid="confirm-note"
                autoFocus
                rows={3}
                maxLength={opts.note.maxLength ?? 200}
                placeholder={opts.note.placeholder}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => settle(false)}>
              {opts.cancelLabel ?? "Keep"}
            </Button>
            <Button
              variant={opts.destructive === false ? "default" : "destructive"}
              disabled={opts.note != null && note.trim() === ""}
              onClick={() => settle(true, note.trim())}
            >
              {opts.confirmLabel ?? "Yes, cancel"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within ConfirmProvider");
  return ctx;
}
