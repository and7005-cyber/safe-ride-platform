import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ListToolbar } from "@/features/admin/components/ListToolbar";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useBuses, useRoutes, useRuns } from "@/lib/queries";

const RUN_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "in-progress", label: "In progress" },
  { value: "delayed", label: "Delayed" },
  { value: "completed", label: "Completed" },
];
const RUN_TYPE_FILTERS = [
  { value: "all", label: "All types" },
  { value: "morning", label: "Morning" },
  { value: "afternoon", label: "Afternoon" },
];

const STATUS_VARIANT: Record<string, "success" | "warning" | "secondary"> = {
  "in-progress": "success",
  delayed: "warning",
  completed: "secondary",
};

const EMPTY = { bus_id: "none", route_id: "none", type: "morning", date: "", status: "in-progress" };

/** Labeled value in the report grid; nulls render as a dash (R14). */
function ReportField({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="font-medium">{value ?? "—"}</p>
    </div>
  );
}

export function RunsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: runs = [] } = useRuns();
  const { data: buses = [] } = useBuses();
  const { data: routes = [] } = useRoutes();
  const [open, setOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [reportId, setReportId] = useState<string | null>(null);

  // Post-run audit report (R14): fetched fresh each time a row's dialog opens.
  const { data: report } = useQuery({
    queryKey: ["run-report", reportId],
    queryFn: () => api.get(`/api/runs/${reportId}/report`),
    enabled: Boolean(reportId),
  });

  const filtered = useMemo(
    () =>
      (runs as any[]).filter((r) => {
        const matchesStatus = statusFilter === "all" || r.status === statusFilter;
        const matchesType = typeFilter === "all" || r.type === typeFilter;
        const q = search.toLowerCase();
        const matchesSearch =
          !q ||
          [r.bus_name, r.route_name, r.date].some((v: string) =>
            (v ?? "").toLowerCase().includes(q),
          );
        return matchesStatus && matchesType && matchesSearch;
      }),
    [runs, search, statusFilter, typeFilter],
  );

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (r: any) => {
    setEditId(r.id);
    setForm({ bus_id: r.bus_id ?? "none", route_id: r.route_id ?? "none", type: r.type, date: r.date, status: r.status });
    setOpen(true);
  };

  const save = async () => {
    try {
      const payload = {
        bus_id: form.bus_id === "none" ? null : form.bus_id,
        route_id: form.route_id === "none" ? null : form.route_id,
        type: form.type, date: form.date || null, status: form.status,
      };
      if (editId) await api.put(`/api/runs/${editId}`, payload);
      else await api.post("/api/runs", payload);
      await qc.invalidateQueries({ queryKey: ["runs"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const remove = async (id: string) => {
    if (!(await confirm({
      title: "Delete this run?",
      description: "This permanently removes the run record.",
      confirmLabel: "Delete run",
    }))) return;
    await api.del(`/api/runs/${id}`);
    await qc.invalidateQueries({ queryKey: ["runs"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Run History" subtitle="Track all bus runs and incidents" />

      <ListToolbar
        search={search}
        onSearch={setSearch}
        placeholder="Search buses, routes, dates…"
        filters={[
          { value: statusFilter, onChange: setStatusFilter, options: RUN_STATUS_FILTERS },
          { value: typeFilter, onChange: setTypeFilter, options: RUN_TYPE_FILTERS },
        ]}
        actions={<Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Run</Button>}
      />

      <p className="text-sm text-muted-foreground">
        {filtered.length} of {runs.length} runs
      </p>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Bus</TableHead>
              <TableHead>Route</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground">No runs match your filters.</TableCell></TableRow>
            ) : (
              filtered.map((r: any) => (
                <TableRow key={r.id} className="cursor-pointer" onClick={() => setReportId(r.id)}>
                  <TableCell className="font-medium">{r.bus_name ?? "—"}</TableCell>
                  <TableCell>{r.route_name ?? r.type}</TableCell>
                  <TableCell>{r.date}</TableCell>
                  <TableCell>{r.stops_completed}/{r.total_stops} stops · {r.students_boarded}/{r.total_students} boarded</TableCell>
                  <TableCell><Badge variant={STATUS_VARIANT[r.status] ?? "secondary"}>{r.status}</Badge></TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" onClick={(e) => { e.stopPropagation(); startEdit(r); }}><Pencil className="h-4 w-4" /></Button>
                    <Button variant="ghost" size="icon" onClick={(e) => { e.stopPropagation(); remove(r.id); }}><Trash2 className="h-4 w-4" /></Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{editId ? "Edit Run" : "Add Run"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Bus</Label>
              <Select value={form.bus_id} onValueChange={(v) => setForm({ ...form, bus_id: v })}>
                <SelectTrigger><SelectValue placeholder="Select bus" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— None —</SelectItem>
                  {buses.map((b: any) => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Route</Label>
              <Select value={form.route_id} onValueChange={(v) => setForm({ ...form, route_id: v })}>
                <SelectTrigger><SelectValue placeholder="Select route" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— None —</SelectItem>
                  {routes.map((r: any) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Type</Label>
                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="morning">Morning</SelectItem>
                    <SelectItem value="afternoon">Afternoon</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2"><Label>Date</Label><Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></div>
            </div>
            <div className="space-y-2">
              <Label>Status</Label>
              <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="in-progress">In progress</SelectItem>
                  <SelectItem value="delayed">Delayed</SelectItem>
                  <SelectItem value="completed">Completed</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button onClick={save}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(reportId)} onOpenChange={(o) => { if (!o) setReportId(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Run Report</DialogTitle></DialogHeader>
          {!report ? (
            <p className="text-sm text-muted-foreground">Loading report…</p>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{report.route_name ?? "—"}</span>
                <Badge variant="outline">{report.type}</Badge>
                <Badge variant={STATUS_VARIANT[report.status] ?? "secondary"}>{report.status}</Badge>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-3">
                <ReportField
                  label="Bus"
                  value={report.bus_name ? [report.bus_name, report.plate_number].filter(Boolean).join(" · ") : null}
                />
                <ReportField label="Driver" value={report.driver_name} />
                <ReportField label="Date" value={report.date} />
                <ReportField label="Start time" value={report.start_time} />
                <ReportField label="End time" value={report.end_time} />
                <ReportField label="Students" value={report.total_students} />
                <ReportField label="Stops" value={`${report.stops_completed}/${report.total_stops}`} />
                <ReportField
                  label={report.type === "afternoon" ? "Dropped off" : "Boarded"}
                  value={report.students_boarded}
                />
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium">Absent students</p>
                  {report.approximate && <Badge variant="warning">approximate</Badge>}
                </div>
                {(report.absent_students ?? []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No absences recorded for this run.</p>
                ) : (
                  <ul className="space-y-1 text-sm">
                    {report.absent_students.map((a: any) => (
                      <li key={a.student_id ?? a.student_name}>
                        {a.student_name}
                        {a.reason && <span className="text-muted-foreground"> — {a.reason}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
