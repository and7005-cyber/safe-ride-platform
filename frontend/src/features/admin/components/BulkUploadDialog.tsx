import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { api } from "@/lib/apiClient";
import { bulkStudentRowError } from "@/lib/validation";

// `parent_phone2` is Parent 2's phone (pre-existing header, reused column).
const TEMPLATE = `name,grade,parent_name,parent_phone,parent_email,parent2_name,parent_phone2,parent2_email,home_address,home_lat,home_lng,pickup_time,route_name
Asha Kamau,Grade 3,Jane Kamau,+254700111222,jane@example.com,Peter Kamau,+254700333444,peter@example.com,Kileleshwa,-1.2820,36.7780,06:45,Express 1 — Morning`;

interface Result {
  inserted: number;
  parentAssignments: number;
  errors: string[];
}

export function BulkUploadDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [uploading, setUploading] = useState(false);

  const normalizeKey = (k: string) => k.trim().toLowerCase().replace(/\s+/g, "_");

  const handleFile = async (file: File) => {
    setUploading(true);
    setResult(null);
    try {
      const buf = await file.arrayBuffer();
      const wb = XLSX.read(buf, { type: "array" });
      const sheet = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: "" });
      const students = rows.map((row) => {
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
          route_name: (norm.route_name as string) ?? null,
        };
      });
      // Client-side mirror of the backend's per-row invariant (parent 1 name,
      // ≥1 phone, ≥1 email): bad rows are reported here with the same wording
      // and only clean rows are uploaded.
      const clientErrors: string[] = [];
      const valid = students.filter((row, index) => {
        const error = bulkStudentRowError(row, index);
        if (error) clientErrors.push(error);
        return !error;
      });
      const res: Result = valid.length
        ? await api.post("/api/students/bulk", { students: valid })
        : { inserted: 0, parentAssignments: 0, errors: [] };
      setResult({ ...res, errors: [...clientErrors, ...res.errors] });
      await qc.invalidateQueries({ queryKey: ["students"] });
    } catch (err) {
      toast({ title: "Upload failed", description: (err as Error).message, variant: "destructive" });
    } finally {
      setUploading(false);
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Bulk Upload Students</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Upload a CSV or Excel file. Each row needs a name, grade, and parent name, plus at
            least one parent phone and one parent email across the two parents
            (parent_phone/parent_phone2, parent_email/parent2_email).
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
            <Button onClick={() => fileRef.current?.click()} disabled={uploading}>
              <Upload className="h-4 w-4" /> {uploading ? "Uploading…" : "Choose file"}
            </Button>
          </div>
          {result && (
            <div className="space-y-2 rounded-md border p-3 text-sm">
              <p><span className="font-medium text-success">{result.inserted}</span> students inserted.</p>
              <p><span className="font-medium">{result.parentAssignments}</span> parent assignments created.</p>
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
