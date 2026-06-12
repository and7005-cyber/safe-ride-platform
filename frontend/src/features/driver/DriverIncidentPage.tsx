import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { DRIVER_NAV } from "@/features/driver/DriverHomePage";
import { api } from "@/lib/apiClient";

const TYPES = [
  { value: "breakdown", label: "Vehicle Breakdown" },
  { value: "accident", label: "Road Accident" },
  { value: "student", label: "Student Issue" },
  { value: "traffic", label: "Heavy Traffic / Delay" },
  { value: "other", label: "Other" },
];

export function DriverIncidentPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [type, setType] = useState("traffic");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api.post("/api/incidents/driver", { type, description });
      toast({ title: "Incident reported", description: "Your administrator has been notified." });
      navigate("/driver");
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title="Report Incident">
      <div className="space-y-4">
        <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
          <TriangleAlert className="h-4 w-4 shrink-0" />
          Use this form to report any safety concern or delay during the run.
        </div>
        <Card>
          <CardContent className="space-y-4 p-5">
            <p className="font-semibold">New Incident Report</p>
            <div className="space-y-2">
              <Label>Incident Type</Label>
              <Select value={type} onValueChange={setType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Description</Label>
              <Textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Describe what happened…" />
            </div>
            <Button className="w-full" onClick={submit} disabled={busy || !description.trim()}>
              {busy ? "Sending…" : "Submit Report"}
            </Button>
          </CardContent>
        </Card>
      </div>
    </RoleMobileLayout>
  );
}
