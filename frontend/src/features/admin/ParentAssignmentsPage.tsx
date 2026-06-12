import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { StatCard } from "@/features/admin/components/StatCard";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { useParents, useParentStudents, useStudents } from "@/lib/queries";
import { Users } from "lucide-react";

export function ParentAssignmentsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: links = [] } = useParentStudents();
  const { data: parents = [] } = useParents();
  const { data: students = [] } = useStudents();
  const [parentId, setParentId] = useState("");
  const [studentId, setStudentId] = useState("");

  const registered = parents.filter((p: any) => p.status === "registered");

  const link = async () => {
    if (!parentId || !studentId) {
      toast({ title: "Select a parent and a student", variant: "destructive" });
      return;
    }
    try {
      await api.post("/api/accounts/parent-students", { parent_id: parentId, student_id: studentId });
      await qc.invalidateQueries({ queryKey: ["parent-students"] });
      setStudentId("");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    }
  };

  const unlink = async (id: string) => {
    await api.del(`/api/accounts/parent-students/${id}`);
    await qc.invalidateQueries({ queryKey: ["parent-students"] });
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Parent Assignments" subtitle="Link parents to their children" />

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Assignments" value={links.length} icon={Link2} />
        <StatCard label="Registered Parents" value={registered.length} icon={Users} variant="success" />
        <StatCard label="Students" value={students.length} icon={Users} />
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-5">
          <div className="min-w-48 flex-1 space-y-1">
            <label className="text-sm font-medium">Parent</label>
            <Select value={parentId} onValueChange={setParentId}>
              <SelectTrigger><SelectValue placeholder="Select parent" /></SelectTrigger>
              <SelectContent>
                {registered.map((p: any) => <SelectItem key={p.id} value={p.id}>{p.full_name} ({p.email})</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-48 flex-1 space-y-1">
            <label className="text-sm font-medium">Student</label>
            <Select value={studentId} onValueChange={setStudentId}>
              <SelectTrigger><SelectValue placeholder="Select student" /></SelectTrigger>
              <SelectContent>
                {students.map((s: any) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <Button onClick={link}><Link2 className="h-4 w-4" /> Assign</Button>
        </CardContent>
      </Card>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Parent</TableHead>
              <TableHead>Student</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {links.length === 0 ? (
              <TableRow><TableCell colSpan={3} className="text-center text-muted-foreground">No assignments yet.</TableCell></TableRow>
            ) : (
              links.map((l: any) => (
                <TableRow key={l.id}>
                  <TableCell className="font-medium">{l.parent_name}</TableCell>
                  <TableCell>{l.student_name}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" onClick={() => unlink(l.id)}><Trash2 className="h-4 w-4" /></Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
