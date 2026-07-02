import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import * as XLSX from "xlsx";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";

// Google waypoint optimisation handles at most 25 points; keeping the planner
// at 24 stops leaves room for the school anchor the backend may add.
export const PLANNER_STOPS_CAP = 24;

export interface PlannerCsvRow {
  address: string;
  pickup_time: string;
  lat?: number | null;
  lng?: number | null;
}

export interface PlannerCsvImport {
  /** Full planner row list after the import (existing non-empty rows + accepted rows). */
  rows: PlannerCsvRow[];
  /** How many file rows were accepted. */
  added: number;
  /** Per-row problems (bad rows never block valid ones) plus a cap-overflow count. */
  errors: string[];
}

const TIME_RE = /^\d{2}:\d{2}$/;

const normalizeKey = (k: string) => k.trim().toLowerCase().replace(/\s+/g, "_");

const isBlank = (v: unknown) => v == null || String(v).trim() === "";

/**
 * Pure row-parsing/validation for the planner CSV import (R21).
 *
 * `sheetRows` are `sheet_to_json` records (header row already mapped to keys);
 * keys are matched case-insensitively. `address` is required, `pickup_time`
 * must be HH:MM when present, and `lat`/`lng` must be numeric and provided
 * together. Valid rows append to the existing non-empty planner rows (a lone
 * empty starter row is replaced); rows past the {@link PLANNER_STOPS_CAP}
 * total are rejected with a count message.
 */
export function importPlannerRows(
  sheetRows: Record<string, unknown>[],
  existingRows: PlannerCsvRow[],
): PlannerCsvImport {
  const kept = existingRows.filter((r) => r.address.trim() !== "");
  const accepted: PlannerCsvRow[] = [];
  const errors: string[] = [];
  let overCap = 0;

  sheetRows.forEach((raw, i) => {
    const rowNo = i + 2; // 1-based, after the header row.
    const norm: Record<string, unknown> = {};
    Object.entries(raw).forEach(([k, v]) => {
      norm[normalizeKey(k)] = v;
    });

    const address = isBlank(norm.address) ? "" : String(norm.address).trim();
    if (!address) {
      errors.push(`Row ${rowNo}: address is required`);
      return;
    }

    const pickupTime = isBlank(norm.pickup_time) ? "" : String(norm.pickup_time).trim();
    if (pickupTime && !TIME_RE.test(pickupTime)) {
      errors.push(`Row ${rowNo}: pickup_time must be HH:MM (got "${pickupTime}")`);
      return;
    }

    const hasLat = !isBlank(norm.lat);
    const hasLng = !isBlank(norm.lng);
    if (hasLat !== hasLng) {
      errors.push(`Row ${rowNo}: lat and lng must be provided together`);
      return;
    }
    let lat: number | null = null;
    let lng: number | null = null;
    if (hasLat && hasLng) {
      lat = Number(norm.lat);
      lng = Number(norm.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        errors.push(`Row ${rowNo}: lat/lng must be numbers`);
        return;
      }
    }

    if (kept.length + accepted.length >= PLANNER_STOPS_CAP) {
      overCap += 1;
      return;
    }
    accepted.push({ address, pickup_time: pickupTime, lat, lng });
  });

  if (overCap > 0) {
    errors.push(
      `${overCap} row(s) skipped: the planner is capped at ${PLANNER_STOPS_CAP} stops`,
    );
  }

  const rows = [...kept, ...accepted];
  return {
    rows: rows.length ? rows : [{ address: "", pickup_time: "" }],
    added: accepted.length,
    errors,
  };
}

const TEMPLATE = `address,pickup_time,lat,lng
Yaya Centre Argwings Kodhek Rd Nairobi,06:45,,
Sarit Centre Westlands Nairobi,07:00,-1.2606,36.8028`;

export function PlannerCsvDialog({
  open,
  onOpenChange,
  existingRows,
  onImport,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  existingRows: PlannerCsvRow[];
  onImport: (rows: PlannerCsvRow[]) => void;
}) {
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<{ added: number; errors: string[] } | null>(null);
  const [parsing, setParsing] = useState(false);

  const handleFile = async (file: File) => {
    setParsing(true);
    setResult(null);
    try {
      const buf = await file.arrayBuffer();
      const wb = XLSX.read(buf, { type: "array" });
      const sheet = wb.Sheets[wb.SheetNames[0]];
      const sheetRows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: "" });
      const { rows, added, errors } = importPlannerRows(sheetRows, existingRows);
      if (added > 0) onImport(rows);
      setResult({ added, errors });
    } catch (err) {
      toast({ title: "Upload failed", description: (err as Error).message, variant: "destructive" });
    } finally {
      setParsing(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const downloadTemplate = () => {
    const blob = new Blob([TEMPLATE], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "planner-stops-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setResult(null);
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader><DialogTitle>Upload planner stops</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Upload a CSV or Excel file with a header row: <code>address</code> (required),{" "}
            <code>pickup_time</code> (optional, HH:MM), <code>lat</code>/<code>lng</code>{" "}
            (optional, together). Valid rows are added to the planner — up to{" "}
            {PLANNER_STOPS_CAP} stops in total. Addresses without coordinates are geocoded
            when you calculate the route.
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
            <Button onClick={() => fileRef.current?.click()} disabled={parsing}>
              <Upload className="h-4 w-4" /> {parsing ? "Reading…" : "Choose file"}
            </Button>
          </div>
          {result && (
            <div className="space-y-2 rounded-md border p-3 text-sm" data-testid="planner-csv-result">
              <p>
                <span className="font-medium text-success">{result.added}</span>{" "}
                stop{result.added === 1 ? "" : "s"} added to the planner.
              </p>
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
