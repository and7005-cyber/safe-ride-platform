import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, Upload, UserCheck, UserX } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { AddressAutocomplete } from "@/features/admin/components/AddressAutocomplete";
import { BulkUploadDialog } from "@/features/admin/components/BulkUploadDialog";
import { ListToolbar } from "@/features/admin/components/ListToolbar";
import { MapPicker } from "@/features/admin/components/MapPicker";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { emailError, parentContactErrors, phoneError } from "@/lib/validation";
import { useAbsences, useRoutes, useSchools, useStudents } from "@/lib/queries";

// --- Pure status pieces (unit-tested in tests/unit/studentStatus.test.ts) ---

// The derived, day-scoped status computed by the backend (U3/U4). The raw
// `status` column still travels in the payload but never renders here (R1–R4).
export type StudentDisplayStatus =
  | "at-school"
  | "on-bus"
  | "dropped-off"
  | "absent"
  | "at-home"
  | "unassigned";

export const STUDENT_STATUS_LABEL: Record<StudentDisplayStatus, string> = {
  "at-school": "At school",
  "on-bus": "On bus",
  "dropped-off": "Dropped off",
  absent: "Absent today",
  "at-home": "At home",
  unassigned: "Unassigned",
};

export const STUDENT_STATUS_VARIANT: Record<
  StudentDisplayStatus,
  "secondary" | "success" | "warning" | "destructive" | "outline"
> = {
  "at-school": "secondary",
  "on-bus": "success",
  "dropped-off": "warning",
  absent: "destructive",
  "at-home": "secondary",
  unassigned: "outline",
};

// Filter options are the derived value set (R4) — one option per label entry.
export const STUDENT_STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  ...Object.entries(STUDENT_STATUS_LABEL).map(([value, label]) => ({ value, label })),
];

// The status filter matches the derived display_status, never the raw status (R4).
export function studentMatchesFilter(
  student: { display_status?: string | null },
  filter: string,
): boolean {
  return filter === "all" || student.display_status === filter;
}

// Absence badge text: whole-day keeps the historical wording; partial-scope
// parent cancellations (U4) name their half of the day.
export function absenceBadgeLabel(scope?: string | null): string {
  if (scope === "morning") return "Absent (AM)";
  if (scope === "afternoon") return "Absent (PM)";
  return "Absent today";
}

// Copy for the scope-aware toggle's dialog: a parent cancellation is named
// with its scope before the office escalates or removes it (U10).
export function parentCancellationDescription(
  studentName: string,
  scope?: string | null,
): string {
  const what =
    scope === "morning"
      ? "the morning ride"
      : scope === "afternoon"
        ? "the afternoon ride"
        : "today's rides";
  return (
    `A parent cancelled ${what} for ${studentName}. Escalate it to a full-day ` +
    "absence recorded by the office, or remove the cancellation so the student " +
    "is expected on the bus again."
  );
}

// --- Component ---------------------------------------------------------------

function initials(name: string): string {
  return name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
}

const EMPTY = {
  name: "",
  grade: "",
  parent_name: "",
  parent_phone: "",
  parent_email: "",
  parent2_name: "",
  parent_phone2: "", // Parent 2's phone (reused column, see plan KTDs)
  parent2_email: "",
  home_address: "",
  home_lat: null as number | null,
  home_lng: null as number | null,
  pickup_time: "",
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
  // The badge and the escalate/remove dialog act on TODAY's absence row, so
  // the query is scoped to the Nairobi day the server keys absences on —
  // an unscoped list would let a past/future-dated row wear today's badge.
  // Kenya is UTC+3 year-round (no DST), so a fixed offset is exact; this
  // still trusts the client clock to be roughly right.
  const nairobiToday = new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
  const { data: absences = [] } = useAbsences(nairobiToday);
  const [open, setOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  // A parent-sourced absence row awaiting the escalate/remove decision (U10).
  const [parentAbsence, setParentAbsence] = useState<{ student: any; absence: any } | null>(null);
  const [absenceBusy, setAbsenceBusy] = useState(false);
  // Invariant messages (parent 1 name / ≥1 phone / ≥1 email) only show after a
  // blocked save attempt — a fresh dialog shouldn't open covered in red.
  const [showContactErrors, setShowContactErrors] = useState(false);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [schoolFilter, setSchoolFilter] = useState("all");

  const morningRoutes = useMemo(() => routes.filter((r: any) => r.type === "morning"), [routes]);
  const afternoonRoutes = useMemo(() => routes.filter((r: any) => r.type === "afternoon"), [routes]);
  const routeName = (id: string) => (routes as any[]).find((r) => r.id === id)?.name;
  const schoolName = (id: string | null) =>
    (schools as any[]).find((s) => s.id === id)?.name ?? "—";

  // The whole absence row per student — scope drives the badge text, source
  // gates the toggle (parent-sourced rows get the escalate/remove dialog).
  const absenceByStudent = useMemo(() => {
    const m = new Map<string, any>();
    (absences as any[]).forEach((a) => m.set(a.student_id, a));
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
        const matchesStatus = studentMatchesFilter(s, statusFilter);
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

  const startCreate = () => {
    setEditId(null); setForm({ ...EMPTY }); setShowContactErrors(false); setOpen(true);
  };
  const startEdit = (s: any) => {
    setEditId(s.id);
    const ids: string[] = s.route_ids ?? [];
    setForm({
      name: s.name, grade: s.grade ?? "", parent_name: s.parent_name ?? "",
      parent_phone: s.parent_phone ?? "", parent_email: s.parent_email ?? "",
      parent2_name: s.parent2_name ?? "", parent_phone2: s.parent_phone2 ?? "",
      parent2_email: s.parent2_email ?? "", home_address: s.home_address ?? "",
      home_lat: s.home_lat, home_lng: s.home_lng, pickup_time: s.pickup_time ?? "",
      school_id: s.school_id ?? "none",
      morning_route: morningRoutes.find((r: any) => ids.includes(r.id))?.id ?? "none",
      afternoon_route: afternoonRoutes.find((r: any) => ids.includes(r.id))?.id ?? "none",
    });
    setShowContactErrors(false);
    setOpen(true);
  };

  // Format errors show as you type; the cross-slot invariant (contactErrs)
  // mirrors the backend contract and blocks save with inline messages.
  const parentPhoneErr = phoneError(form.parent_phone);
  const parentPhone2Err = phoneError(form.parent_phone2);
  const parentEmailErr = emailError(form.parent_email);
  const parent2EmailErr = emailError(form.parent2_email);
  const contactErrs = parentContactErrors(form);
  const contactInvalid = !!(contactErrs.parentName || contactErrs.phone || contactErrs.email);

  // A pin dropped/dragged on the map resolves to an editable address (R8).
  // Best-effort: when the lookup finds nothing the field is left as-is.
  const reverseGeocode = async (lat: number, lng: number) => {
    try {
      const res = await api.post("/api/fleet/reverse-geocode", { lat, lng });
      if (res.found && res.label) setForm((f) => ({ ...f, home_address: res.label }));
    } catch {
      /* reverse geocoding is a convenience — never block the pin */
    }
  };

  const save = async () => {
    if (contactInvalid) {
      setShowContactErrors(true);
      return;
    }
    setSaving(true);
    try {
      // Both morning and afternoon routes are sent in one payload so editing
      // one never drops the other (#5).
      const route_ids = [form.morning_route, form.afternoon_route].filter((r) => r !== "none");
      const payload = {
        name: form.name, grade: form.grade || null, parent_name: form.parent_name || null,
        parent_phone: form.parent_phone || null, parent_email: form.parent_email || null,
        parent2_name: form.parent2_name || null, parent_phone2: form.parent_phone2 || null,
        parent2_email: form.parent2_email || null, home_address: form.home_address || null,
        home_lat: form.home_lat, home_lng: form.home_lng, pickup_time: form.pickup_time || null,
        school_id: form.school_id === "none" ? null : form.school_id, route_ids,
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

  // display_status derives from the absence rows (day scope → absent), so the
  // Status column refreshes together with the badge after any absence change.
  const refreshAfterAbsenceChange = async () => {
    await qc.invalidateQueries({ queryKey: ["absences"] });
    await qc.invalidateQueries({ queryKey: ["students"] });
  };

  const toggleAbsence = async (s: any) => {
    const existing = absenceByStudent.get(s.id);
    if (existing) {
      if (existing.source === "parent") {
        // A parent cancellation (partial or whole-day) is never silently
        // deleted — the office explicitly escalates or removes it (U10).
        setParentAbsence({ student: s, absence: existing });
        return;
      }
      await api.del(`/api/students/absences/${existing.id}`);
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
    await refreshAfterAbsenceChange();
  };

  const resolveParentAbsence = async (action: "escalate" | "remove") => {
    if (!parentAbsence) return;
    setAbsenceBusy(true);
    try {
      if (action === "escalate") {
        // The existing admin mark endpoint escalates the row to scope='day',
        // source='admin' (U4's staff transition rule).
        await api.post("/api/students/absences", { student_id: parentAbsence.student.id });
      } else {
        await api.del(`/api/students/absences/${parentAbsence.absence.id}`);
      }
      await refreshAfterAbsenceChange();
      setParentAbsence(null);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setAbsenceBusy(false);
    }
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
                const absence = absenceByStudent.get(s.id);
                return (
                  <TableRow key={s.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground">
                          {initials(s.name)}
                        </span>
                        <span className="font-medium">{s.name}</span>
                        {absence && (
                          <Badge variant="destructive" className="ml-1">
                            {absenceBadgeLabel(absence.scope)}
                          </Badge>
                        )}
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
                    <TableCell>
                      <Badge
                        variant={STUDENT_STATUS_VARIANT[s.display_status as StudentDisplayStatus] ?? "secondary"}
                        className={s.display_status === "unassigned" ? "text-muted-foreground" : undefined}
                      >
                        {STUDENT_STATUS_LABEL[s.display_status as StudentDisplayStatus] ?? s.display_status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {/* Action order [absence toggle, edit, delete] is load-bearing:
                          admin-crud.spec.ts selects these buttons positionally. */}
                      <Button
                        variant="ghost"
                        size="icon"
                        title={absence ? "Mark present today" : "Mark absent today"}
                        onClick={() => toggleAbsence(s)}
                      >
                        {absence ? <UserCheck className="h-4 w-4 text-success" /> : <UserX className="h-4 w-4" />}
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
              <div className="space-y-3 rounded-md border p-3" data-testid="parent1-group">
                <p className="text-sm font-semibold">Parent 1</p>
                <div className="space-y-2">
                  <Label>Name</Label>
                  <Input value={form.parent_name} onChange={(e) => setForm({ ...form, parent_name: e.target.value })} />
                  {showContactErrors && contactErrs.parentName && (
                    <p className="text-xs text-destructive">{contactErrs.parentName}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label>Phone</Label>
                  <Input value={form.parent_phone} onChange={(e) => setForm({ ...form, parent_phone: e.target.value })} />
                  {parentPhoneErr && <p className="text-xs text-destructive">{parentPhoneErr}</p>}
                  {!parentPhoneErr && showContactErrors && contactErrs.phone && (
                    <p className="text-xs text-destructive">{contactErrs.phone}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label>Email</Label>
                  <Input value={form.parent_email} onChange={(e) => setForm({ ...form, parent_email: e.target.value })} />
                  {parentEmailErr && <p className="text-xs text-destructive">{parentEmailErr}</p>}
                  {!parentEmailErr && showContactErrors && contactErrs.email && (
                    <p className="text-xs text-destructive">{contactErrs.email}</p>
                  )}
                </div>
              </div>
              <div className="space-y-3 rounded-md border p-3" data-testid="parent2-group">
                <p className="text-sm font-semibold">
                  Parent 2 <span className="font-normal text-muted-foreground">(optional)</span>
                </p>
                <div className="space-y-2">
                  <Label>Name</Label>
                  <Input value={form.parent2_name} onChange={(e) => setForm({ ...form, parent2_name: e.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>Phone</Label>
                  <Input value={form.parent_phone2} onChange={(e) => setForm({ ...form, parent_phone2: e.target.value })} />
                  {parentPhone2Err && <p className="text-xs text-destructive">{parentPhone2Err}</p>}
                </div>
                <div className="space-y-2">
                  <Label>Email</Label>
                  <Input value={form.parent2_email} onChange={(e) => setForm({ ...form, parent2_email: e.target.value })} />
                  {parent2EmailErr && <p className="text-xs text-destructive">{parent2EmailErr}</p>}
                </div>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              At least one phone and one email are needed across the two parents; emails link parent accounts.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Home address</Label>
                <AddressAutocomplete
                  value={form.home_address}
                  placeholder="Type to search addresses…"
                  testId="student-address"
                  onChange={(address) => setForm((f) => ({ ...f, home_address: address }))}
                  onResolve={(address, lat, lng) =>
                    setForm((f) => ({ ...f, home_address: address, home_lat: lat, home_lng: lng }))
                  }
                />
              </div>
              <div className="space-y-2"><Label>Pickup time</Label><Input placeholder="06:45" value={form.pickup_time} onChange={(e) => setForm({ ...form, pickup_time: e.target.value })} /></div>
            </div>
            <div className="space-y-2">
              <Label>Home location {form.home_lat != null && <span className="text-xs text-muted-foreground">({form.home_lat}, {form.home_lng})</span>}</Label>
              <MapPicker
                lat={form.home_lat}
                lng={form.home_lng}
                onPick={(lat, lng) => setForm((f) => ({ ...f, home_lat: lat, home_lng: lng }))}
                onMapPick={reverseGeocode}
              />
              <p className="text-xs text-muted-foreground">Pick an address suggestion to place the pin, or click the map — the address fills in and stays editable.</p>
            </div>
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
              disabled={
                saving || !form.name ||
                !!parentPhoneErr || !!parentPhone2Err || !!parentEmailErr || !!parent2EmailErr ||
                (showContactErrors && contactInvalid)
              }
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Scope-aware "mark present" on a parent-sourced absence (U10): the
          office chooses — never a silent delete of the parent's cancellation. */}
      <Dialog open={!!parentAbsence} onOpenChange={(next) => (next ? null : setParentAbsence(null))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Parent ride cancellation</DialogTitle>
            <DialogDescription>
              {parentAbsence &&
                parentCancellationDescription(parentAbsence.student.name, parentAbsence.absence.scope)}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={absenceBusy}
              onClick={() => resolveParentAbsence("remove")}
            >
              Remove the cancellation
            </Button>
            <Button disabled={absenceBusy} onClick={() => resolveParentAbsence("escalate")}>
              Escalate to full-day absence
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BulkUploadDialog open={bulkOpen} onOpenChange={setBulkOpen} />
    </div>
  );
}
