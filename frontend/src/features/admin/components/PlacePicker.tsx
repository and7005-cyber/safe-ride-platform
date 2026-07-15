import { AddressAutocomplete } from "@/features/admin/components/AddressAutocomplete";
import { MapPicker } from "@/features/admin/components/MapPicker";
import { api } from "@/lib/apiClient";

/**
 * How a coordinate/address value came to be. A background re-geocode may refine
 * a `typed`/`imported`/`legacy` value, but must never overwrite a `picked` one
 * — the operator's deliberate pin wins (R8, R11).
 */
export type Provenance = "typed" | "picked" | "imported" | "legacy";

/** The three interchangeable ways a PlacePicker value can change. */
export type ChangeSource = "address-select" | "address-edit" | "pin";

/** A location resolved by any of the three input methods. */
export interface ResolvedPlace {
  address: string;
  lat: number | null;
  lng: number | null;
  provenance: Provenance;
}

/**
 * Pure provenance transition. Total and deterministic — the unit-testable core
 * of the coherence guard:
 *  - a dropped/dragged pin is always `picked`;
 *  - selecting an autocomplete suggestion is `typed`;
 *  - a free-text address edit downgrades to `typed`, EXCEPT over a `picked`
 *    value, which is preserved so a later text edit (or a background
 *    re-geocode) never clobbers the operator's deliberate pin (R11).
 */
export function nextProvenance(prev: Provenance, source: ChangeSource): Provenance {
  switch (source) {
    case "pin":
      return "picked";
    case "address-select":
      return "typed";
    case "address-edit":
      return prev === "picked" ? "picked" : "typed";
  }
}

/**
 * One controlled, resolved-place control that yields `{address, lat, lng,
 * provenance}` via three interchangeable input methods: address autocomplete,
 * map-pin drop/drag, and reverse-geocode. Composes the existing
 * `AddressAutocomplete` + `MapPicker` (no duplicated Google API logic) so it can
 * drop into any form field. Consumed by Add Stop (U10), the student home (U11),
 * CSV repair (U12), and the depot (U13).
 */
export function PlacePicker({
  value,
  onChange,
  placeholder,
  testId,
}: {
  value: ResolvedPlace;
  onChange: (next: ResolvedPlace) => void;
  placeholder?: string;
  testId?: string;
}) {
  // A pin resolves to an editable address (best-effort reverse-geocode). The
  // coordinates come from the callback args — never from the `value` closure —
  // so this async write can't clobber the freshly dropped pin with stale
  // coordinates. `nextProvenance(_, "pin")` is invariant to `value.provenance`
  // (always `picked`), so a stale closure is harmless here too.
  const reverseGeocode = async (lat: number, lng: number) => {
    try {
      const res = await api.post("/api/fleet/reverse-geocode", { lat, lng });
      if (res.found && res.label) {
        onChange({ address: res.label, lat, lng, provenance: nextProvenance(value.provenance, "pin") });
      }
    } catch {
      /* reverse geocoding is a convenience — never block the pin */
    }
  };

  return (
    <div className="space-y-2">
      <AddressAutocomplete
        value={value.address}
        placeholder={placeholder}
        testId={testId}
        // Free-text edit: keep the coordinates and let provenance settle via the
        // guard — a `picked` value stays `picked`, anything else becomes `typed`
        // (eligible for a future background re-geocode refinement).
        onChange={(address) =>
          onChange({ ...value, address, provenance: nextProvenance(value.provenance, "address-edit") })
        }
        // A suggestion was picked — exact coordinates, provenance `typed`.
        onResolve={(address, lat, lng) =>
          onChange({ address, lat, lng, provenance: nextProvenance(value.provenance, "address-select") })
        }
      />
      <MapPicker
        lat={value.lat}
        lng={value.lng}
        // Pin drop/drag (and the map's own search box) → provenance `picked`.
        onPick={(lat, lng) =>
          onChange({ ...value, lat, lng, provenance: nextProvenance(value.provenance, "pin") })
        }
        onMapPick={reverseGeocode}
      />
    </div>
  );
}
