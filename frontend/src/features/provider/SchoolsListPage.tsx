import { useState } from "react";
import { format } from "date-fns";
import { LogIn, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { CreateSchoolDialog } from "@/features/provider/CreateSchoolDialog";
import { StepInDialog } from "@/features/provider/StepInDialog";
import { useProviderSchools, type ProviderSchool } from "@/features/provider/providerHooks";

// The provider's home (U13/R22, R24): every school with its HEALTH — counts,
// setup state, the latest staff write — and deliberately nothing from any
// roster: no student, parent or staff name ever renders here. Dashes stand
// in for nulls. Per-school "Step in" opens the reasoned entry into that
// school's console; "New school" runs the create/bootstrap flow.

function dateTimeText(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : format(date, "d MMM yyyy HH:mm");
}

export function SchoolsListPage() {
  const { data: schools = [], isLoading } = useProviderSchools();
  const [createOpen, setCreateOpen] = useState(false);
  const [stepInSchool, setStepInSchool] = useState<ProviderSchool | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Schools"
        subtitle={`${schools.length} school${schools.length === 1 ? "" : "s"} on SafeRide`}
        action={
          <Button onClick={() => setCreateOpen(true)} data-testid="provider-create-school">
            <Plus className="h-4 w-4" /> New School
          </Button>
        }
      />

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>School</TableHead>
              <TableHead>Setup</TableHead>
              <TableHead className="text-right">Students</TableHead>
              <TableHead className="text-right">Buses</TableHead>
              <TableHead className="text-right">Drivers</TableHead>
              <TableHead className="text-right">Runs today</TableHead>
              <TableHead>Last staff activity</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : schools.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground">
                  No schools yet. Create the first one.
                </TableCell>
              </TableRow>
            ) : (
              schools.map((school) => (
                <TableRow key={school.schoolId} data-testid={`provider-school-${school.schoolId}`}>
                  <TableCell>
                    <p className="font-medium">{school.name}</p>
                    <p className="font-mono text-xs text-muted-foreground">{school.code}</p>
                  </TableCell>
                  <TableCell>
                    <Badge variant={school.setupState === "ready" ? "success" : "secondary"}>
                      {school.setupState === "ready" ? "Ready" : "Setting up"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">{school.students ?? "—"}</TableCell>
                  <TableCell className="text-right">{school.buses ?? "—"}</TableCell>
                  <TableCell className="text-right">{school.drivers ?? "—"}</TableCell>
                  <TableCell className="text-right">{school.runsToday ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {dateTimeText(school.lastStaffWrite)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setStepInSchool(school)}
                      data-testid={`step-in-${school.schoolId}`}
                    >
                      <LogIn className="h-4 w-4" /> Step in
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>

      <CreateSchoolDialog open={createOpen} onOpenChange={setCreateOpen} />
      <StepInDialog
        school={
          stepInSchool
            ? { schoolId: stepInSchool.schoolId, name: stepInSchool.name, code: stepInSchool.code }
            : null
        }
        onOpenChange={(open) => {
          if (!open) setStepInSchool(null);
        }}
      />
    </div>
  );
}
