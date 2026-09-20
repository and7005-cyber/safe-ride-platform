import { useState } from "react";
import { format } from "date-fns";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { PageHeader } from "@/features/admin/components/PageHeader";
import {
  useProviderAudit,
  useProviderSchools,
  type ProviderAuditRow,
} from "@/features/provider/providerHooks";

// The provider-side audit reader (U13/R25): REAL identities, unmasked — this
// is the one surface where provider actors show with their own names (the
// school-side trail shows "SafeRide"). Filter by school, and by support
// session by clicking a row's session chip — every action taken during one
// step-in shares that chip.

const ALL_SCHOOLS = "all";

function timeText(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : format(date, "d MMM yyyy HH:mm:ss");
}

function detailText(detail: unknown): string {
  if (detail === null || detail === undefined) return "—";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return "—";
  }
}

export function ProviderAuditPage() {
  const [schoolId, setSchoolId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const { data: schools = [] } = useProviderSchools();
  const { data: rows = [], isLoading } = useProviderAudit({
    schoolId,
    supportSessionId: sessionId,
  });

  const schoolName = (id: string | null) =>
    schools.find((s) => s.schoolId === id)?.name ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit"
        subtitle="Every provider action, with real names — filter by school or one support session"
      />

      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={schoolId ?? ALL_SCHOOLS}
          onValueChange={(v) => setSchoolId(v === ALL_SCHOOLS ? null : v)}
        >
          <SelectTrigger className="w-56" data-testid="audit-school-filter">
            <SelectValue placeholder="All schools" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_SCHOOLS}>All schools</SelectItem>
            {schools.map((school) => (
              <SelectItem key={school.schoolId} value={school.schoolId}>
                {school.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {sessionId && (
          <Badge variant="secondary" className="gap-1.5" data-testid="audit-session-filter">
            Session {sessionId.slice(0, 8)}…
            <button
              type="button"
              aria-label="Clear session filter"
              onClick={() => setSessionId(null)}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        )}
      </div>

      <Card>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>School</TableHead>
              <TableHead>Resource</TableHead>
              <TableHead>Session</TableHead>
              <TableHead>Detail</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center text-muted-foreground">
                  No audit rows match.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row: ProviderAuditRow) => (
                <TableRow key={row.id} data-testid={`audit-row-${row.id}`}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {timeText(row.createdAt)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{row.action}</Badge>
                  </TableCell>
                  <TableCell>
                    <p className="font-medium" data-testid="audit-actor-name">
                      {row.actorName ?? "—"}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {row.actorEmail ?? ""}
                      {row.actorKind ? ` · ${row.actorKind}` : ""}
                    </p>
                  </TableCell>
                  <TableCell>{schoolName(row.schoolId) ?? (row.schoolId ? `${row.schoolId.slice(0, 8)}…` : "—")}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.resourceType ?? "—"}
                  </TableCell>
                  <TableCell>
                    {row.supportSessionId ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 font-mono text-xs"
                        title="Show only this support session"
                        onClick={() => setSessionId(row.supportSessionId)}
                      >
                        {row.supportSessionId.slice(0, 8)}…
                      </Button>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="max-w-64 truncate text-xs text-muted-foreground">
                    {detailText(row.detail)}
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
