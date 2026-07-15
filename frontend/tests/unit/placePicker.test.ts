// U9 — PlacePicker provenance core. Covers R8/R11: the coherence guard that
// makes removing the student "Home Location" field safe. The pure
// `nextProvenance` helper is the unit-testable heart of the primitive — the full
// AddressAutocomplete + MapPicker wiring is exercised by the consuming e2e (U14).
import { describe, expect, it } from "vitest";
import {
  nextProvenance,
  type ChangeSource,
  type Provenance,
} from "@/features/admin/components/PlacePicker";

const PROVENANCES: Provenance[] = ["typed", "picked", "imported", "legacy"];

describe("nextProvenance", () => {
  it("keeps a 'picked' value picked when the address text is edited (the guard)", () => {
    // The operator's deliberate pin wins over a later text edit or a background
    // re-geocode — this is the invariant that makes the field removal safe.
    expect(nextProvenance("picked", "address-edit")).toBe("picked");
  });

  it("sets 'typed' when an autocomplete suggestion is selected", () => {
    expect(nextProvenance("typed", "address-select")).toBe("typed");
    expect(nextProvenance("legacy", "address-select")).toBe("typed");
    expect(nextProvenance("imported", "address-select")).toBe("typed");
    expect(nextProvenance("picked", "address-select")).toBe("typed");
  });

  it("sets 'picked' when a map pin is dropped or dragged", () => {
    for (const prev of PROVENANCES) {
      expect(nextProvenance(prev, "pin")).toBe("picked");
    }
  });

  it("upgrades a loaded 'legacy' value to 'typed' on select and 'picked' on a pin", () => {
    expect(nextProvenance("legacy", "address-select")).toBe("typed");
    expect(nextProvenance("legacy", "pin")).toBe("picked");
  });

  it("downgrades every non-picked source to 'typed' on a free-text address edit", () => {
    expect(nextProvenance("typed", "address-edit")).toBe("typed");
    expect(nextProvenance("imported", "address-edit")).toBe("typed");
    expect(nextProvenance("legacy", "address-edit")).toBe("typed");
  });

  it("is total and deterministic across every (provenance, source) pair", () => {
    const sources: ChangeSource[] = ["address-select", "address-edit", "pin"];
    for (const prev of PROVENANCES) {
      for (const source of sources) {
        const result = nextProvenance(prev, source);
        expect(PROVENANCES).toContain(result);
        // Deterministic: the same inputs always yield the same output.
        expect(nextProvenance(prev, source)).toBe(result);
      }
    }
  });
});
