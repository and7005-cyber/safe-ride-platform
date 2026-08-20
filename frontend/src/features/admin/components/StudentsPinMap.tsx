import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
// `Map` aliased so the ES `Map` keeps its meaning (RouteMapPreview precedent).
import { AdvancedMarker, Map as GoogleMap } from "@vis.gl/react-google-maps";
import { FitBounds, type LatLng } from "@/components/map/MapPrimitives";
import { MAP_ID, hasMapsKey } from "@/lib/googleMaps";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/apiClient";

// U11 — every home pin for a school on one map for eyeball QA (R19). The data
// comes ONLY from the audited GET /api/students/pin-map: each fetch writes a
// 'pin-map-viewed' live_admin_audit row server-side, so this component must
// never assemble the aggregate from the regular students list (that would
// bypass the audit), and it never polls or background-refetches — every read
// is a deliberate, logged access while someone is actually looking.

export interface PinMapPin {
  id: string;
  name: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  provenance: string | null;
  state: "placed" | "unresolved";
}

// Marker colour by provenance — the QA question this view answers is "which
// pins did a human never look at?", and 'imported' is exactly that tier.
const PROVENANCE_COLOR: Record<string, string> = {
  picked: "#2f6f4f", // deliberate operator pin
  typed: "#1d4ed8", // resolved from a typed address
  imported: "#b45309", // bulk import, unreviewed — the tier this view exists for
  legacy: "#6b7280", // predates provenance tracking
};

function pinColor(provenance: string | null): string {
  return PROVENANCE_COLOR[provenance ?? ""] ?? PROVENANCE_COLOR.legacy;
}

/** The marker face: name label over a provenance-coloured dot. Also used by
 * the key-less fallback rows so the e2e contract (a named, clickable
 * `pin-marker-…`) holds with or without a Google Maps key. */
function PinFace({ pin }: { pin: PinMapPin }) {
  return (
    <div className="flex flex-col items-center">
      <span className="rounded border bg-background/95 px-1 text-[10px] font-medium shadow-sm">
        {pin.name}
      </span>
      <span
        style={{ background: pinColor(pin.provenance) }}
        className="mt-0.5 h-3 w-3 rounded-full border-2 border-white shadow"
      />
    </div>
  );
}

export function StudentsPinMap({
  open,
  onOpenChange,
  schools,
  initialSchoolId,
  onEditStudent,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  schools: Array<{ id: string; name: string }>;
  /** The page's school filter, adopted each time the dialog opens. */
  initialSchoolId: string | null;
  /** Deep-link to the student's normal edit dialog (PlacePicker inside) so a
   * mis-placed pin is fixed in the regular flow, not a parallel one. */
  onEditStudent: (studentId: string) => void;
}) {
  const [schoolId, setSchoolId] = useState<string>("none");
  useEffect(() => {
    if (open) setSchoolId(initialSchoolId ?? "none");
  }, [open, initialSchoolId]);

  const { data, isFetching, error } = useQuery({
    queryKey: ["student-pin-map", schoolId],
    queryFn: () => api.get("/api/students/pin-map", { school_id: schoolId }),
    enabled: open && schoolId !== "none",
    // Access is audit-logged server-side: staleTime 0 makes every open of the
    // dialog a fresh (audited) read, and window-focus refetches are off so no
    // audit row is ever written for a view nobody deliberately requested.
    staleTime: 0,
    refetchOnWindowFocus: false,
  });

  const placed: PinMapPin[] = data?.placed ?? [];
  const unresolved: PinMapPin[] = data?.unresolved ?? [];
  const points: LatLng[] = useMemo(
    () => placed.map((p) => ({ lat: p.lat as number, lng: p.lng as number })),
    [placed],
  );
  const focusKey = `${schoolId}:${points.map((p) => `${p.lat},${p.lng}`).join("|")}`;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>Student pin map</DialogTitle>
          <DialogDescription>
            Every home pin for the school on one map — spot the outliers, then click a
            marker to fix it in the student editor. Opening this view is recorded in the
            audit log.
          </DialogDescription>
        </DialogHeader>

        <div className="max-w-xs space-y-2">
          <Label>School</Label>
          <Select value={schoolId} onValueChange={setSchoolId}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">— Pick a school —</SelectItem>
              {schools.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {schoolId === "none" ? (
          <p className="text-sm text-muted-foreground">
            Pick a school to load its pins.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground" data-testid="pin-map-summary">
              {isFetching
                ? "Loading pins…"
                : `${placed.length} placed · ${unresolved.length} unresolved`}
            </p>
            {error && (
              <p className="text-sm text-destructive">{(error as Error).message}</p>
            )}
            <div className="grid grid-cols-[1fr_15rem] gap-3">
              {hasMapsKey && points.length > 0 ? (
                <div
                  className="h-[26rem] overflow-hidden rounded-md border"
                  data-testid="pin-map"
                >
                  <GoogleMap
                    mapId={MAP_ID}
                    defaultCenter={points[0]}
                    defaultZoom={13}
                    gestureHandling="greedy"
                    disableDefaultUI
                    className="h-full w-full"
                  >
                    <FitBounds points={points} focusKey={focusKey} padding={48} />
                    {placed.map((pin) => (
                      <AdvancedMarker
                        key={pin.id}
                        position={{ lat: pin.lat as number, lng: pin.lng as number }}
                        title={pin.address ?? pin.name}
                        onClick={() => onEditStudent(pin.id)}
                      >
                        <div data-testid={`pin-marker-${pin.id}`} className="cursor-pointer">
                          <PinFace pin={pin} />
                        </div>
                      </AdvancedMarker>
                    ))}
                  </GoogleMap>
                </div>
              ) : (
                // Key-less (or pin-less) fallback, the RouteMapPreview
                // precedent: the same audited content as a named list, each
                // row deep-linking exactly like its marker would.
                <div
                  className="h-[26rem] overflow-y-auto rounded-md border bg-muted/30 p-3"
                  data-testid="pin-map-placeholder"
                >
                  {placed.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No placed pins for this school yet.
                    </p>
                  ) : (
                    <ul className="space-y-1">
                      {placed.map((pin) => (
                        <li key={pin.id}>
                          <button
                            type="button"
                            data-testid={`pin-marker-${pin.id}`}
                            className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent"
                            onClick={() => onEditStudent(pin.id)}
                          >
                            <span
                              style={{ background: pinColor(pin.provenance) }}
                              className="h-2.5 w-2.5 shrink-0 rounded-full"
                            />
                            <span className="font-medium">{pin.name}</span>
                            <span className="truncate text-xs text-muted-foreground">
                              {pin.lat?.toFixed(5)}, {pin.lng?.toFixed(5)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <div
                className="h-[26rem] overflow-y-auto rounded-md border p-3"
                data-testid="pin-map-unresolved"
              >
                <p className="text-sm font-semibold">
                  Unresolved ({unresolved.length})
                </p>
                <p className="mb-2 text-xs text-muted-foreground">
                  No usable coordinates — open each student and place the pin.
                </p>
                {unresolved.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Every student has a pin.
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {unresolved.map((pin) => (
                      <li key={pin.id}>
                        <button
                          type="button"
                          data-testid={`pin-unresolved-${pin.id}`}
                          className="w-full rounded px-2 py-1 text-left text-sm hover:bg-accent"
                          onClick={() => onEditStudent(pin.id)}
                        >
                          <span className="font-medium">{pin.name}</span>
                          {pin.address && (
                            <span className="block truncate text-xs text-muted-foreground">
                              {pin.address}
                            </span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Pin colour is provenance:{" "}
              <span className="font-medium" style={{ color: PROVENANCE_COLOR.imported }}>
                imported
              </span>{" "}
              pins were never reviewed by a person —{" "}
              <span className="font-medium" style={{ color: PROVENANCE_COLOR.picked }}>
                picked
              </span>{" "}
              ones were placed by hand;{" "}
              <span className="font-medium" style={{ color: PROVENANCE_COLOR.typed }}>
                typed
              </span>{" "}
              resolved from an address,{" "}
              <span className="font-medium" style={{ color: PROVENANCE_COLOR.legacy }}>
                legacy
              </span>{" "}
              predates tracking.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
