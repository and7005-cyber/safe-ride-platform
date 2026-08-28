import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { MapPicker } from "@/features/admin/components/MapPicker";
import { PageHeader } from "@/features/admin/components/PageHeader";
import { api } from "@/lib/apiClient";
import { phoneError } from "@/lib/validation";
import { useSchoolKey, useSchoolSettings } from "@/lib/queries";

// U12 — School Settings replaces the Schools page (R23): the console works
// inside ONE active school, whose name, contact, bell times and gate location
// are edited here. There is no create and no delete anywhere — schools are
// created by the provider and never deleted from the app.

const EMPTY = {
  name: "",
  address: "",
  phone: "",
  lat: null as number | null,
  lng: null as number | null,
  morning_bell: "",
  afternoon_bell: "",
};

export function SchoolSettingsPage() {
  const qc = useQueryClient();
  const schoolKey = useSchoolKey();
  const { toast } = useToast();
  const { data: school } = useSchoolSettings();

  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [seededId, setSeededId] = useState<string | null>(null);

  // Seed the form once per school row (refetches must not clobber edits).
  useEffect(() => {
    if (!school || seededId === school.id) return;
    setSeededId(school.id);
    setForm({
      name: school.name ?? "",
      address: school.address ?? "",
      phone: school.phone ?? "",
      lat: school.lat ?? null,
      lng: school.lng ?? null,
      morning_bell: school.morning_bell ?? "",
      afternoon_bell: school.afternoon_bell ?? "",
    });
  }, [school, seededId]);

  const phoneErr = phoneError(form.phone, { allowLandline: true });

  const save = async () => {
    if (!school) return;
    if (!form.address || form.lat == null || form.lng == null) {
      toast({ title: "Address and a map location are required", variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      await api.put(`/api/fleet/schools/${school.id}`, {
        name: form.name,
        address: form.address,
        phone: form.phone || null,
        lat: form.lat,
        lng: form.lng,
        morning_bell: form.morning_bell || null,
        afternoon_bell: form.afternoon_bell || null,
      });
      await qc.invalidateQueries({ queryKey: schoolKey("school-settings") });
      // Bells feed the route schedules; routes re-solve against them.
      await qc.invalidateQueries({ queryKey: schoolKey("routes") });
      toast({ title: "School settings saved" });
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="School Settings"
        subtitle="This school's name, contact details, bell times and gate location"
        action={
          school?.code ? (
            <Badge variant="outline" data-testid="school-code">
              Code: {school.code}
            </Badge>
          ) : undefined
        }
      />

      <Card className="max-w-2xl">
        <CardContent className="space-y-4 p-5">
          <div className="space-y-2">
            <Label>Name</Label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>Address</Label>
            <Input
              value={form.address}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label>Phone</Label>
            <Input
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
            />
            {phoneErr && <p className="text-xs text-destructive">{phoneErr}</p>}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label>Morning bell</Label>
              <Input
                type="time"
                value={form.morning_bell}
                data-testid="morning-bell"
                onChange={(e) => setForm({ ...form, morning_bell: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Morning routes aim to arrive at the gate by this time.
              </p>
            </div>
            <div className="space-y-2">
              <Label>Afternoon bell</Label>
              <Input
                type="time"
                value={form.afternoon_bell}
                data-testid="afternoon-bell"
                onChange={(e) => setForm({ ...form, afternoon_bell: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Afternoon routes leave the gate at this time.
              </p>
            </div>
          </div>
          <div className="space-y-2">
            <Label>
              Location{" "}
              {form.lat != null && (
                <span className="text-xs text-muted-foreground">
                  ({form.lat}, {form.lng})
                </span>
              )}
            </Label>
            <MapPicker
              lat={form.lat}
              lng={form.lng}
              onPick={(lat, lng) => setForm({ ...form, lat, lng })}
            />
            <p className="text-xs text-muted-foreground">
              Click the map to set the school gate location.
            </p>
          </div>
          <div className="flex justify-end">
            <Button onClick={save} disabled={saving || !form.name || !!phoneErr || !school}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
