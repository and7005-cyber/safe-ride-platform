import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { emailError } from "@/lib/validation";

// Create School (U13/R22, AE15 provider branch): the school with its
// generated, non-editable code, then its first director through the U8 seam
// — a NEW email answers `created` with a temporary password revealed
// EXACTLY ONCE (the StaffPage reveal pattern: the server stores a hash and
// can never show it again), an existing email answers `offered` and the
// person accepts the director role at their next sign-in.

const EMPTY = {
  name: "",
  lat: "",
  lng: "",
  morningBell: "",
  afternoonBell: "",
  directorEmail: "",
  directorName: "",
};

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

interface CreatedResult {
  school: { name: string; code: string };
  director:
    | { status: "created"; email: string; fullName: string | null; temporaryPassword: string }
    | { status: "offered"; email: string; role?: string };
}

export function CreateSchoolDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  // The reveal-once result dialog (school code + temporary password / offer).
  const [result, setResult] = useState<CreatedResult | null>(null);

  const set = (field: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [field]: e.target.value });

  // A fresh form per opening, however the dialog was opened.
  useEffect(() => {
    if (open) setForm({ ...EMPTY });
  }, [open]);

  const emailErr = emailError(form.directorEmail, true);
  const bellErr = (value: string) => value.trim() !== "" && !HHMM.test(value.trim());
  const numErr = (value: string) => value.trim() !== "" && Number.isNaN(Number(value));
  const invalid =
    !form.name.trim() ||
    !!emailErr ||
    bellErr(form.morningBell) ||
    bellErr(form.afternoonBell) ||
    numErr(form.lat) ||
    numErr(form.lng);

  const save = async () => {
    setSaving(true);
    try {
      const res = await api.post("/api/provider/schools", {
        name: form.name.trim(),
        lat: form.lat.trim() === "" ? null : Number(form.lat),
        lng: form.lng.trim() === "" ? null : Number(form.lng),
        morningBell: form.morningBell.trim() || null,
        afternoonBell: form.afternoonBell.trim() || null,
        directorEmail: form.directorEmail.trim(),
        directorName: form.directorName.trim() || null,
      });
      await qc.invalidateQueries({ queryKey: ["provider", "schools"] });
      onOpenChange(false);
      setResult({ school: res.school, director: res.director });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const copyPassword = async (password: string) => {
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
    <>
      <Dialog open={open} onOpenChange={(next) => (saving ? null : onOpenChange(next))}>
        <DialogContent className="max-w-md" data-testid="create-school-dialog">
          <DialogHeader>
            <DialogTitle>New school</DialogTitle>
            <DialogDescription>
              The school gets a generated code and its first director. A new
              director email gets an account with a temporary password shown to
              you once; an existing SafeRide account is offered the role
              instead.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="school-name">School name</Label>
              <Input id="school-name" data-testid="school-name" value={form.name} onChange={set("name")} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="school-morning-bell">Morning bell (optional)</Label>
                <Input
                  id="school-morning-bell"
                  placeholder="07:30"
                  value={form.morningBell}
                  onChange={set("morningBell")}
                />
                {bellErr(form.morningBell) && (
                  <p className="text-xs text-destructive">Use HH:MM.</p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="school-afternoon-bell">Afternoon bell (optional)</Label>
                <Input
                  id="school-afternoon-bell"
                  placeholder="15:30"
                  value={form.afternoonBell}
                  onChange={set("afternoonBell")}
                />
                {bellErr(form.afternoonBell) && (
                  <p className="text-xs text-destructive">Use HH:MM.</p>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="school-lat">Latitude (optional)</Label>
                <Input id="school-lat" placeholder="-1.3005" value={form.lat} onChange={set("lat")} />
                {numErr(form.lat) && <p className="text-xs text-destructive">A number.</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="school-lng">Longitude (optional)</Label>
                <Input id="school-lng" placeholder="36.8102" value={form.lng} onChange={set("lng")} />
                {numErr(form.lng) && <p className="text-xs text-destructive">A number.</p>}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="director-email">Director email</Label>
              <Input
                id="director-email"
                data-testid="director-email"
                type="email"
                value={form.directorEmail}
                onChange={set("directorEmail")}
              />
              {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="director-name">Director name (optional)</Label>
              <Input
                id="director-name"
                data-testid="director-name"
                value={form.directorName}
                onChange={set("directorName")}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={save} disabled={saving || invalid} data-testid="create-school-save">
              {saving ? "Creating…" : "Create school"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reveal-once result (AE15 provider branch): the school code always;
          the temporary password only for a freshly created director — the
          single chance to share it. */}
      <Dialog open={!!result} onOpenChange={(o) => (o ? null : setResult(null))}>
        <DialogContent className="max-w-sm" data-testid="school-created-dialog">
          <DialogHeader>
            <DialogTitle>School created</DialogTitle>
          </DialogHeader>
          {result && (
            <div className="space-y-4">
              <div className="rounded-md border bg-muted px-4 py-3">
                <p className="text-sm font-medium">{result.school.name}</p>
                <p className="text-sm text-muted-foreground">
                  School code:{" "}
                  <span className="font-mono font-semibold text-foreground" data-testid="school-code">
                    {result.school.code}
                  </span>
                </p>
              </div>
              {result.director.status === "created" ? (
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">
                    Share this temporary password with{" "}
                    {("fullName" in result.director && result.director.fullName) ||
                      result.director.email}{" "}
                    now — they sign in with it and must set their own password
                    immediately. It cannot be shown again.
                  </p>
                  <div className="flex items-center justify-between gap-2 rounded-md border bg-muted px-4 py-3">
                    <span
                      className="break-all font-mono text-lg font-semibold"
                      data-testid="temp-password"
                    >
                      {result.director.temporaryPassword}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        result.director.status === "created" &&
                        void copyPassword(result.director.temporaryPassword)
                      }
                    >
                      <Copy className="h-4 w-4" /> Copy
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground" data-testid="director-offered">
                  {result.director.email} already has a SafeRide account — they
                  will be asked to accept the Director role at their next
                  sign-in. No password to share.
                </p>
              )}
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setResult(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
