import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, MapPin, Upload } from "lucide-react";
import * as XLSX from "xlsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";
import { PlacePicker, type ResolvedPlace } from "@/features/admin/components/PlacePicker";
import { api } from "@/lib/apiClient";
import { useSchoolKey } from "@/lib/queries";
import { bulkStudentRowError } from "@/lib/validation";

// `parent_phone2` is Parent 2's phone (pre-existing header, reused column).
// route_name is retired (U10/R17): routes come from the fleet plan, so the
// template no longer offers the column. Old files that still carry it upload
// fine — the column is simply ignored, with a per-row note.
const TEMPLATE = `name,grade,parent_name,parent_phone,parent_email,parent2_name,parent_phone2,parent2_email,home_address,home_lat,home_lng,pickup_time
Asha Kamau,Grade 3,Jane Kamau,+254700111222,jane@example.com,Peter Kamau,+254700333444,peter@example.com,Kileleshwa,-1.2820,36.7780,06:45`;

interface BulkRowPayload {
  name: string;
  grade: string | null;
  parent_name: string | null;
  parent_phone: string | null;
  parent_email: string | null;
  parent2_name: string | null;
  parent_phone2: string | null;
  parent2_email: string | null;
  home_address: string | null;
  home_lat: number | null;
  home_lng: number | null;
  pickup_time: string | null;
  route_name: string | null;
}

/** One row of the validate response — the triage table's source of truth. */
interface TriageRow {
  index: number;
  name: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  provider: string | null;
  label: string | null;
  status: "resolved" | "ambiguous" | "failed";
  route_name: string | null;
  route_note: string | null;
  duplicate_of: { id: string; name: string; home_address?: string | null } | null;
}

/** The operator's per-row decisions, keyed by triage index. */
interface Resolution {
  /** Ambiguous row: the proposed pin was accepted in one click. */
  confirmed?: boolean;
  /** Failed row: a pin hand-placed via the PlacePicker (provenance 'picked'). */
  place?: ResolvedPlace;
  /** Duplicate row: what to do with the existing student. */
  duplicateAction?: "skip" | "update";
}

interface CommitResult {
  inserted: number;
  updated: number;
  skipped: number;
  parentAssignments: number;
  notes: string[];
  errors: string[];
}

const EMPTY_PLACE: ResolvedPlace = { address: "", lat: null, lng: null, provenance: "imported" };

export function BulkUploadDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);

  // U12: the upload is scoped to the tab's ACTIVE school — the request scope
  // (X-School-Id) stamps every committed row, so the picker is gone.
  const [rows, setRows] = useState<BulkRowPayload[]>([]);
  const [clientErrors, setClientErrors] = useState<string[]>([]);
  const [triage, setTriage] = useState<TriageRow[] | null>(null);
  const [resolutions, setResolutions] = useState<Record<number, Resolution>>({});
  const [pickerFor, setPickerFor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CommitResult | null>(null);

  const resetFlow = () => {
    setRows([]);
    setClientErrors([]);
    setTriage(null);
    setResolutions({});
    setPickerFor(null);
    setResult(null);
  };

  const normalizeKey = (k: string) => k.trim().toLowerCase().replace(/\s+/g, "_");

  // Step 1: parse the file client-side, then ask the server to triage every
  // row (geocode tiers + duplicates) WITHOUT inserting anything.
  const handleFile = async (file: File) => {
    setBusy(true);
    resetFlow();
    try {
      const buf = await file.arrayBuffer();
      const wb = XLSX.read(buf, { type: "array" });
      const sheet = wb.Sheets[wb.SheetNames[0]];
      const sheetRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: "" });
      const students: BulkRowPayload[] = sheetRows.map((row) => {
        const norm: Record<string, unknown> = {};
        Object.entries(row).forEach(([k, v]) => {
          norm[normalizeKey(k)] = v === "" ? null : v;
        });
        return {
          name: (norm.name as string) ?? "",
          grade: (norm.grade as string) ?? null,
          parent_name: (norm.parent_name as string) ?? null,
          parent_phone: norm.parent_phone != null ? String(norm.parent_phone) : null,
          parent_email: (norm.parent_email as string) ?? null,
          parent2_name: (norm.parent2_name as string) ?? null,
          parent_phone2: norm.parent_phone2 != null ? String(norm.parent_phone2) : null,
          parent2_email: (norm.parent2_email as string) ?? null,
          home_address: (norm.home_address as string) ?? null,
          home_lat: norm.home_lat != null ? Number(norm.home_lat) : null,
          home_lng: norm.home_lng != null ? Number(norm.home_lng) : null,
          pickup_time: norm.pickup_time != null ? String(norm.pickup_time) : null,
          // Tolerated but retired: parsed so old files upload, assigns nothing.
          route_name: (norm.route_name as string) ?? null,
        };
      });
      // Client-side mirror of the backend's per-row invariant (parent 1 name,
      // ≥1 phone, ≥1 email): bad rows are reported here with the same wording
      // and only clean rows go to triage.
      const errors: string[] = [];
      const valid = students.filter((row, index) => {
        const error = bulkStudentRowError(row, index);
        if (error) errors.push(error);
        return !error;
      });
      setClientErrors(errors);
      setRows(valid);
      if (valid.length) {
        const res = await api.post("/api/students/bulk/validate", {
          students: valid,
        });
        setTriage(res.rows as TriageRow[]);
      }
    } catch (err) {
      toast({ title: "Upload failed", description: (err as Error).message, variant: "destructive" });
      resetFlow();
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const resolutionOf = (index: number): Resolution => resolutions[index] ?? {};
  const setResolution = (index: number, patch: Resolution) =>
    setResolutions((prev) => ({ ...prev, [index]: { ...prev[index], ...patch } }));

  const placedPin = (index: number): ResolvedPlace | null => {
    const place = resolutionOf(index).place;
    return place && place.lat != null && place.lng != null ? place : null;
  };

  /** A row's tier after the operator's fixes. */
  const effectiveStatus = (t: TriageRow): TriageRow["status"] => {
    if (t.status === "ambiguous" && resolutionOf(t.index).confirmed) return "resolved";
    if (t.status === "failed" && placedPin(t.index)) return "resolved";
    return t.status;
  };

  const counts = (triage ?? []).reduce(
    (acc, t) => {
      acc[effectiveStatus(t)] += 1;
      if (t.duplicate_of) acc.duplicates += 1;
      return acc;
    },
    { resolved: 0, ambiguous: 0, failed: 0, duplicates: 0 },
  );
  const undecidedDuplicates = (triage ?? []).filter(
    (t) => t.duplicate_of && !resolutionOf(t.index).duplicateAction,
  ).length;
  // Commit gates: every proposed pin explicitly confirmed, every duplicate
  // decided. Unlocated rows may still import (they surface as "unresolved
  // address" on the plan side and can be pinned later from the student record).
  const readyToCommit =
    triage !== null && rows.length > 0 && counts.ambiguous === 0 && undecidedDuplicates === 0;

  /** A triage row needs a line in the table; clean resolved rows are a count. */
  const needsAttention = (t: TriageRow) =>
    t.status !== "resolved" || t.duplicate_of !== null || t.route_note !== null;

  // Step 2: commit — the rows plus the operator's resolutions (confirmed pins
  // as plain coordinates, hand-placed pins with provenance 'picked', and the
  // per-duplicate skip/update choice).
  const commit = async () => {
    if (!triage) return;
    setBusy(true);
    try {
      const students = rows.map((row, index) => {
        const t = triage[index];
        const res = resolutionOf(index);
        const out: BulkRowPayload & { provenance?: string; duplicate_action?: string } = { ...row };
        if (t?.status === "ambiguous" && res.confirmed) {
          // One-click confirm: the proposal's coordinates, provenance stays
          // 'imported' (only a deliberate map pin earns 'picked').
          out.home_lat = t.lat;
          out.home_lng = t.lng;
        }
        const pin = placedPin(index);
        if (pin) {
          out.home_lat = pin.lat;
          out.home_lng = pin.lng;
          if (pin.address) out.home_address = pin.address;
          out.provenance = pin.provenance; // 'picked' — never downgraded later
        }
        if (t?.duplicate_of && res.duplicateAction) out.duplicate_action = res.duplicateAction;
        return out;
      });
      const res: CommitResult = await api.post("/api/students/bulk", {
        students,
      });
      setResult(res);
      setTriage(null);
      await qc.invalidateQueries({ queryKey: schoolKey("students") });
    } catch (err) {
      toast({ title: "Import failed", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  const downloadTemplate = () => {
    const blob = new Blob([TEMPLATE], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "students-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const tierBadge = (t: TriageRow) => {
    const status = effectiveStatus(t);
    if (status === "resolved") return <Badge variant="secondary">Located</Badge>;
    if (status === "ambiguous") return <Badge variant="outline">Confirm pin</Badge>;
    return <Badge variant="destructive">Not located</Badge>;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) resetFlow();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader><DialogTitle>Bulk Upload Students</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Upload a CSV or Excel file. Each row needs a name, grade, and parent name, plus at
            least one parent phone and one parent email across the two parents
            (parent_phone/parent_phone2, parent_email/parent2_email). Routes are not part of
            the upload — they come from the fleet plan. Every imported student is enrolled at
            this school.
          </p>
          <div className="flex gap-2">
            <Button variant="outline" onClick={downloadTemplate}>Download template</Button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
            />
            <Button onClick={() => fileRef.current?.click()} disabled={busy}>
              <Upload className="h-4 w-4" /> {busy && !triage ? "Checking…" : "Choose file"}
            </Button>
          </div>
          {clientErrors.length > 0 && (
            <div className="rounded-md border p-3 text-sm">
              <p className="font-medium text-destructive">{clientErrors.length} row error(s):</p>
              <ul className="ml-4 list-disc text-muted-foreground">
                {clientErrors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
          {triage && (
            <div className="space-y-3 rounded-md border p-3 text-sm" data-testid="bulk-triage">
              <p className="font-medium" data-testid="bulk-triage-summary">
                {counts.resolved} located · {counts.ambiguous} to confirm · {counts.failed} not
                located{counts.duplicates > 0 ? ` · ${counts.duplicates} already enrolled` : ""}
              </p>
              <div className="max-h-72 space-y-2 overflow-y-auto">
                {triage.filter(needsAttention).map((t) => (
                  <div key={t.index} className="space-y-2 rounded-md border p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{t.name}</span>
                      {tierBadge(t)}
                    </div>
                    {t.status === "ambiguous" && !resolutionOf(t.index).confirmed && (
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-muted-foreground">
                          Proposed: {t.label ?? `${t.lat?.toFixed(4)}, ${t.lng?.toFixed(4)}`}
                        </span>
                        <Button
                          size="sm"
                          data-testid={`bulk-confirm-${t.index}`}
                          onClick={() => setResolution(t.index, { confirmed: true })}
                        >
                          <Check className="h-4 w-4" /> Confirm pin
                        </Button>
                      </div>
                    )}
                    {t.status === "ambiguous" && resolutionOf(t.index).confirmed && (
                      <p className="text-muted-foreground">Proposed pin confirmed.</p>
                    )}
                    {t.status === "failed" && (
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-muted-foreground">
                          {placedPin(t.index)
                            ? "Pin placed by hand."
                            : `"${t.address}" couldn't be located.`}
                        </span>
                        <Button
                          size="sm"
                          variant="outline"
                          data-testid={`bulk-place-${t.index}`}
                          onClick={() => setPickerFor(pickerFor === t.index ? null : t.index)}
                        >
                          <MapPin className="h-4 w-4" />{" "}
                          {placedPin(t.index) ? "Adjust pin" : "Place on map"}
                        </Button>
                      </div>
                    )}
                    {pickerFor === t.index && (
                      <div className="space-y-2">
                        <p className="text-xs text-muted-foreground">
                          Place {t.name}'s home pin — drop or drag the pin, or search an address.
                        </p>
                        <PlacePicker
                          value={resolutionOf(t.index).place ?? { ...EMPTY_PLACE, address: t.address ?? "" }}
                          onChange={(place) => setResolution(t.index, { place })}
                          testId={`bulk-picker-${t.index}`}
                        />
                      </div>
                    )}
                    {t.duplicate_of && (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-muted-foreground">
                          Already enrolled at this school. Skip the row, or update their
                          contacts and address?
                        </span>
                        <div className="flex gap-1">
                          <Button
                            size="sm"
                            variant={resolutionOf(t.index).duplicateAction === "skip" ? "default" : "outline"}
                            data-testid={`bulk-skip-${t.index}`}
                            onClick={() => setResolution(t.index, { duplicateAction: "skip" })}
                          >
                            Skip
                          </Button>
                          <Button
                            size="sm"
                            variant={resolutionOf(t.index).duplicateAction === "update" ? "default" : "outline"}
                            data-testid={`bulk-update-${t.index}`}
                            onClick={() => setResolution(t.index, { duplicateAction: "update" })}
                          >
                            Update
                          </Button>
                        </div>
                      </div>
                    )}
                    {t.route_note && (
                      <p className="text-xs italic text-muted-foreground">{t.route_note}</p>
                    )}
                  </div>
                ))}
              </div>
              {counts.failed > 0 && (
                <p className="text-xs text-muted-foreground">
                  Rows without a pin still import — the student is flagged "unresolved address"
                  and can be pinned later from their record or the pin map.
                </p>
              )}
              <Button
                className="w-full"
                data-testid="bulk-commit"
                disabled={busy || !readyToCommit}
                onClick={commit}
              >
                {busy
                  ? "Importing…"
                  : readyToCommit
                    ? `Import ${rows.length} student${rows.length === 1 ? "" : "s"}`
                    : "Resolve the rows above to import"}
              </Button>
            </div>
          )}
          {result && (
            <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="bulk-result">
              <p><span className="font-medium text-success">{result.inserted}</span> students inserted.</p>
              {result.updated > 0 && <p><span className="font-medium">{result.updated}</span> existing students updated.</p>}
              {result.skipped > 0 && <p><span className="font-medium">{result.skipped}</span> duplicate rows skipped.</p>}
              <p><span className="font-medium">{result.parentAssignments}</span> parent assignments created.</p>
              {result.notes.length > 0 && (
                <ul className="ml-4 list-disc text-xs italic text-muted-foreground">
                  {result.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              )}
              {result.errors.length > 0 && (
                <div>
                  <p className="font-medium text-destructive">{result.errors.length} row error(s):</p>
                  <ul className="ml-4 list-disc text-muted-foreground">
                    {result.errors.map((e, i) => <li key={i}>{e}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
