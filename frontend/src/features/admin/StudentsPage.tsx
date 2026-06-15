import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, Upload, UserCheck, UserX } from "lucide-react";
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
import { BulkUploadDialog } from "@/features/admin/components/BulkUploadDialog";
import { ListToolbar } from "@/features/admin/components/ListToolbar";
import { MapPicker } from "@/features/admin/components/MapPicker";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { emailError, phoneError } from "@/lib/validation";
import { useAbsences, useRoutes, useSchools, useStudents } from "@/lib/queries";

const STUDENT_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "at-school", label: "At school" },
  { value: "on-bus", label: "On bus" },
  { value: "dropped-off", label: "Dropped off" },
  { value: "absent", label: "Absent" },
];

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

const STATUS_VARIANT: Record<string, "secondary" | "success" | "warning" | "destructive"> = {
  "at-school": "secondary",
  "on-bus": "success",
  "dropped-off": "warning",
  absent: "destructive",
};

const EMPTY = {
  name: "",
  grade: "",
  parent_name: "",
  parent_phone: "",
  parent_phone2: "",
  parent_email: "",
  home_address: "",
  home_lat: null as number | null,
  home_lng: null as number | null,
  pickup_time: "",
  status: "at-school",
  school_id: "none",
  morning_route: "none",
  afternoon_route: "none",
};

export function StudentsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { data: students = [] } = useStudents();
  const { data: routes = [] } = useRoutes();
  const { data: schools = [] } = useSchools();
  const { data: absences = [] } = useAbsences();
  const [open, setOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [geocoding, setGeocoding] = useState(false);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [schoolFilter, setSchoolFilter] = useState("all");

  const morningRoutes = useMemo(() => routes.filter((r: any) => r.type === "morning"), [routes]);
  const afternoonRoutes = useMemo(() => routes.filter((r: any) => r.type === "afternoon"), [routes]);
  const routeName = (id: string) => (routes as any[]).find((r) => r.id === id)?.name;
  const schoolName = (id: string | null) =>
    (schools as any[]).find((s) => s.id === id)?.name ?? "—";

  const absenceByStudent = useMemo(() => {
    const m = new Map<string, string>();
    (absences as any[]).forEach((a) => m.set(a.student_id, a.id));
    return m;
  }, [absences]);

  const schoolFilters = useMemo(
    () => [
      { value: "all", label: "All schools" },
      ...(schools as any[]).map((s) => ({ value: s.id, label: s.name })),
    ],
    [schools],
  );

  const filtered = useMemo(
    () =>
      (students as any[]).filter((s) => {
        const matchesStatus = statusFilter === "all" || s.status === statusFilter;
        const matchesSchool = schoolFilter === "all" || s.school_id === schoolFilter;
        const q = search.toLowerCase();
        const matchesSearch =
          !q ||
          [s.name, s.parent_name, s.parent_phone].some((v: string) =>
            (v ?? "").toLowerCase().includes(q),
          );
        return matchesStatus && matchesSchool && matchesSearch;
      }),
    [students, search, statusFilter, schoolFilter],
  );

  const startCreate = () => { setEditId(null); setForm({ ...EMPTY }); setOpen(true); };
  const startEdit = (s: any) => {
    setEditId(s.id);
    const ids: string[] = s.route_ids ?? [];
    setForm({
      name: s.name, grade: s.grade ?? "", parent_name: s.parent_name ?? "",
      parent_phone: s.parent_phone ?? "", parent_phone2: s.parent_phone2 ?? "",
      parent_email: s.parent_email ?? "", home_address: s.home_address ?? "",
      home_lat: s.home_lat, home_lng: s.home_lng, pickup_time: s.pickup_time ?? "",
      status: s.status ?? "at-school", school_id: s.school_id ?? "none",
      morning_route: morningRoutes.find((r: any) => ids.includes(r.id))?.id ?? "none",
      afternoon_route: afternoonRoutes.find((r: any) => ids.includes(r.id))?.id ?? "none",
    });
    setOpen(true);
  };

  const parentPhoneErr = phoneError(form.parent_phone);
  const parentPhone2Err = phoneError(form.parent_phone2);
  const parentEmailErr = emailError(form.parent_email);

  const geocodeAddress = async () => {
    if (!form.home_address.trim()) return;
    setGeocoding(true);
    try {
      const res = await api.post("/api/fleet/geocode", { address: form.home_address });
      if (res.found) {
        setForm((f) => ({ ...f, home_lat: res.lat, home_lng: res.lng }));
        toast({ title: "Address located", description: res.label ?? "Pin placed on the map." });
      } else {
        toast({
          title: "Address not found",
          description: "Try a more specific address, or set the pin manually on the map.",
          variant: "destructive",
        });
      }
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setGeocoding(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      // Both morning and afternoon routes are sent in one payload so editing
      // one never drops the other (#5).
      const route_ids = [form.morning_route, form.afternoon_route].filter((r) => r !== "none");
      const payload = {
        name: form.name, grade: form.grade || null, parent_name: form.parent_name || null,
        parent_phone: form.parent_phone || null, parent_phone2: form.parent_phone2 || null,
        parent_email: form.parent_email || null, home_address: form.home_address || null,
        home_lat: form.home_lat, home_lng: form.home_lng, pickup_time: form.pickup_time || null,
        status: form.status, school_id: form.school_id === "none" ? null : form.school_id, route_ids,
      };
      if (editId) await api.put(`/api/students/${editId}`, payload);
      else await api.post("/api/students", payload);
      await qc.invalidateQueries({ queryKey: ["students"] });
      await qc.invalidateQueries({ queryKey: ["routes"] });
      setOpen(false);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (s: any) => {
    if (!(await confirm({
      title: `Delete ${s.name}?`,
      description: "This removes the student and cancels their stop on every route.",
      confirmLabel: "Delete student",
    }))) return;
    await api.del(`/api/students/${s.id}`);
    await qc.invalidateQueries({ queryKey: ["students"] });
    await qc.invalidateQueries({ queryKey: ["routes"] });
  };

  const toggleAbsence = async (s: any) => {
    const existing = absenceByStudent.get(s.id);
    if (existing) {
      await api.del(`/api/students/absences/${existing}`);
    } else {
      if (!(await confirm({
        title: `Mark ${s.name} absent today?`,
        description: "The bus won't stop at this student's stop today.",
        confirmLabel: "Mark absent",
        cancelLabel: "Cancel",
        destructive: false,
      }))) return;
      await api.post("/api/students/absences", { student_id: s.id });
    }
    await qc.invalidateQueries({ queryKey: ["absences"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Students" subtitle="Manage student profiles and assignments" />

      <ListToolbar
        search={search}
        onSearch={setSearch}
        placeholder="Search students, parents…"
        filters={[
          { value: statusFilter, onChange: setStatusFilter, options: STUDENT_STATUS_FILTERS },
          { value: schoolFilter, onChange: setSchoolFilter, options: schoolFilters },
        ]}
        actions={
          <>
            <Button variant="outline" onClick={() => setBulkOpen(true)}><Upload className="h-4 w-4" /> Bulk Upload</Button>
            <Button onClick={startCreate}><Plus className="h-4 w-4" /> Add Student</Button>
          </>
        }
      />

      <p className="text-sm text-muted-foreground">
        {filtered.length} of {students.length} students
      </p>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Student</TableHead>
              <TableHead>Grade</TableHead>
              <TableHead>School</TableHead>
              <TableHead>Routes</TableHead>
              <TableHead>Home Address</TableHead>
              <TableHead>Pickup</TableHead>
              <TableHead>Parent</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={9} className="text-center text-muted-foreground">No students match your filters.</TableCell></TableRow>
            ) : (
              filtered.map((s: any) => {
                const names = (s.route_ids ?? []).map(routeName).filter(Boolean);
                const absentToday = absenceByStudent.has(s.id);
                return (
                  <TableRow key={s.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground">
                          {initials(s.name)}
                        </span>
                        <span className="font-medium">{s.name}</span>
                        {absentToday && <Badge variant="destructive" className="ml-1">Absent today</Badge>}
                      </div>
                    </TableCell>
                    <TableCell>{s.grade ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{schoolName(s.school_id)}</TableCell>
                    <TableCell className="text-muted-foreground">{names.length ? names.join(", ") : "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{s.home_address ?? "—"}</TableCell>
                    <TableCell>{s.pickup_time ?? "—"}</TableCell>
                    <TableCell>
                      <div className="leading-tight">
                        <p className="font-medium">{s.parent_name ?? "—"}</p>
                        {s.parent_phone && <p className="text-xs text-muted-foreground">{s.parent_phone}</p>}
                      </div>
                    </TableCell>
                    <TableCell><Badge variant={STATUS_VARIANT[s.status] ?? "secondary"}>{s.status}</Badge></TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        title={absentToday ? "Mark present today" : "Mark absent today"}
                        onClick={() => toggleAbsence(s)}
                      >
                        {absentToday ? <UserCheck className="h-4 w-4 text-success" /> : <UserX className="h-4 w-4" />}
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => startEdit(s)}><Pencil className="h-4 w-4" /></Button>
                      <Button variant="ghost" size="icon" onClick={() => remove(s)}><Trash2 className="h-4 w-4" /></Button>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{editId ? "Edit Student" : "Add Student"}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
              <div className="space-y-2"><Label>Grade</Label><Input value={form.grade} onChange={(e) => setForm({ ...form, grade: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><Label>Parent name</Label><Input value={form.parent_name} onChange={(e) => setForm({ ...form, parent_name: e.target.value })} /></div>
              <div className="space-y-2">
                <Label>Parent phone</Label>
                <Input value={form.parent_phone} onChange={(e) => setForm({ ...form, parent_phone: e.target.value })} />
                {parentPhoneErr && <p className="text-xs text-destructive">{parentPhoneErr}</p>}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Second phone</Label>
                <Input value={form.parent_phone2} onChange={(e) => setForm({ ...form, parent_phone2: e.target.value })} />
                {parentPhone2Err && <p className="text-xs text-destructive">{parentPhone2Err}</p>}
              </div>
              <div className="space-y-2">
                <Label>Parent email</Label>
                <Input value={form.parent_email} onChange={(e) => setForm({ ...form, parent_email: e.target.value })} />
                {parentEmailErr && <p className="text-xs text-destructive">{parentEmailErr}</p>}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Home address</Label>
                <div className="flex gap-2">
                  <Input value={form.home_address} onChange={(e) => setForm({ ...form, home_address: e.target.value })} />
                  <Button type="button" variant="outline" onClick={geocodeAddress} disabled={geocoding || !form.home_address.trim()}>
                    {geocoding ? "Locating…" : "Locate"}
                  </Button>
                </div>
              </div>
              <div className="space-y-2"><Label>Pickup time</Label><Input placeholder="06:45" value={form.pickup_time} onChange={(e) => setForm({ ...form, pickup_time: e.target.value })} /></div>
            </div>
            <div className="space-y-2">
              <Label>Home location {form.home_lat != null && <span className="text-xs text-muted-foreground">({form.home_lat}, {form.home_lng})</span>}</Label>
              <MapPicker lat={form.home_lat} lng={form.home_lng} onPick={(lat, lng) => setForm({ ...form, home_lat: lat, home_lng: lng })} />
              <p className="text-xs text-muted-foreground">Type an address and click Locate, or click the map to set the pickup point.</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>School</Label>
                <Select value={form.school_id} onValueChange={(v) => setForm({ ...form, school_id: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— None —</SelectItem>
                    {schools.map((s: any) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Status</Label>
                <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="at-school">At school</SelectItem>
                    <SelectItem value="on-bus">On bus</SelectItem>
                    <SelectItem value="dropped-off">Dropped off</SelectItem>
                    <SelectItem value="absent">Absent</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Morning route</Label>
                <Select value={form.morning_route} onValueChange={(v) => setForm({ ...form, morning_route: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— None —</SelectItem>
                    {morningRoutes.map((r: any) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>Afternoon route</Label>
                <Select value={form.afternoon_route} onValueChange={(v) => setForm({ ...form, afternoon_route: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— None —</SelectItem>
                    {afternoonRoutes.map((r: any) => <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Students are assigned to routes; the bus is whatever bus runs the route.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              onClick={save}
              disabled={saving || !form.name || !!parentPhoneErr || !!parentPhone2Err || !!parentEmailErr}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BulkUploadDialog open={bulkOpen} onOpenChange={setBulkOpen} />
    </div>
  );
}
