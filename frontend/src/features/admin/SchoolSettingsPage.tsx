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
import {
  TRACKING_FIELDS,
  trackingFieldError,
  trackingFormFromSchool,
  trackingFormValid,
  trackingPayload,
  type TrackingDefaults,
  type TrackingForm,
} from "@/features/admin/trackingFields";
import { api } from "@/lib/apiClient";
import { phoneError } from "@/lib/validation";
import { useSchoolKey, useSchoolSettings } from "@/lib/queries";

// U12 — School Settings replaces the Schools page (R23): the console works
// inside ONE active school, whose name, contact, bell times and gate location
// are edited here. There is no create and no delete anywhere — schools are
// created by the provider and never deleted from the app.
//
// GPS plan U11 (R26, R31, R38): a second card, "Tracking", holds the five
// per-school knobs. Each shows its system default (served by the API as
// `tracking_defaults`) as the placeholder and in the help text; an empty
// field means "use the default", and "Use default" empties it. One Save
// writes both cards; every changed value is audited server-side.

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
  const [tracking, setTracking] = useState<TrackingForm>(() => trackingFormFromSchool(null));
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
    setTracking(trackingFormFromSchool(school));
  }, [school, seededId]);

  const phoneErr = phoneError(form.phone, { allowLandline: true });
  const defaults: TrackingDefaults = school?.tracking_defaults ?? {};
  const trackingOk = trackingFormValid(tracking);

  const save = async () => {
    if (!school) return;
    if (!form.address || form.lat == null || form.lng == null) {
      toast({ title: "Address and a map location are required", variant: "destructive" });
      return;
    }
    if (!trackingOk) {
      toast({ title: "Check the tracking values", variant: "destructive" });
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
        // Every knob, as a number or an explicit null (clear to default).
        ...trackingPayload(tracking),
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
        subtitle="This school's name, contact details, bell times, gate location and tracking thresholds"
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
        </CardContent>
      </Card>

      <Card className="max-w-2xl" data-testid="tracking-card">
        <CardContent className="space-y-4 p-5">
          <div>
            <h2 className="text-base font-semibold">Tracking</h2>
            <p className="text-xs text-muted-foreground">
              How the driver app's GPS fixes are judged for this school. Leave a field empty
              to use the system default shown; every change is recorded in the audit log.
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {TRACKING_FIELDS.map((spec) => {
              const value = tracking[spec.key];
              const error = trackingFieldError(spec, value);
              const fallback = defaults[spec.key];
              const inputId = `tracking-${spec.key}`;
              return (
                <div key={spec.key} className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={inputId}>
                      {spec.label}{" "}
                      <span className="text-xs font-normal text-muted-foreground">({spec.unit})</span>
                    </Label>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      disabled={value === ""}
                      data-testid={`tracking-default-${spec.key}`}
                      onClick={() => setTracking({ ...tracking, [spec.key]: "" })}
                    >
                      Use default
                    </Button>
                  </div>
                  <Input
                    id={inputId}
                    type="number"
                    inputMode="numeric"
                    min={spec.min}
                    max={spec.max}
                    step={1}
                    value={value}
                    placeholder={fallback == null ? "" : String(fallback)}
                    aria-invalid={error ? true : undefined}
                    data-testid={inputId}
                    onChange={(e) => setTracking({ ...tracking, [spec.key]: e.target.value })}
                  />
                  {error ? (
                    <p className="text-xs text-destructive" data-testid={`${inputId}-error`}>
                      {error}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {fallback == null ? "" : `Default ${fallback} ${spec.unit}. `}
                      {spec.help}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="flex max-w-2xl justify-end">
        <Button
          onClick={save}
          disabled={saving || !form.name || !!phoneErr || !school || !trackingOk}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
