import { describe, expect, it } from "vitest";
import {
  TRACKING_FIELDS,
  trackingFieldError,
  trackingFormFromSchool,
  trackingFormValid,
  trackingPayload,
} from "@/features/admin/trackingFields";

// School Settings "Tracking" card (GPS plan U11/R38): the five per-school
// knobs, their bounds (the server's 422 and the column CHECK share them), and
// the omitted/null/number semantics the PUT body carries.

const spec = (key: string) => TRACKING_FIELDS.find((f) => f.key === key)!;

describe("tracking fields", () => {
  it("names the five per-school knobs in the server's order with the server's bounds", () => {
    expect(TRACKING_FIELDS.map((f) => f.key)).toEqual([
      "custody_threshold_m",
      "vicinity_radius_m",
      "fix_accuracy_cap_m",
      "position_retention_days",
      "ping_interval_s",
    ]);
    expect(TRACKING_FIELDS.map((f) => [f.min, f.max])).toEqual([
      [25, 2000],
      [25, 2000],
      [25, 2000],
      [7, 365],
      [5, 60],
    ]);
  });

  it("seeds the form from the school row: a stored value as text, null or missing as empty", () => {
    expect(
      trackingFormFromSchool({ custody_threshold_m: 300, vicinity_radius_m: null }),
    ).toEqual({
      custody_threshold_m: "300",
      vicinity_radius_m: "",
      fix_accuracy_cap_m: "",
      position_retention_days: "",
      ping_interval_s: "",
    });
    expect(Object.values(trackingFormFromSchool(null))).toEqual(["", "", "", "", ""]);
  });

  it("accepts empty (default) and whole numbers inside the bounds, and names the bounds otherwise", () => {
    const custody = spec("custody_threshold_m");
    expect(trackingFieldError(custody, "")).toBeNull();
    expect(trackingFieldError(custody, "  ")).toBeNull();
    expect(trackingFieldError(custody, "25")).toBeNull();
    expect(trackingFieldError(custody, "2000")).toBeNull();
    expect(trackingFieldError(custody, "24")).toBe("Between 25 and 2000 m");
    expect(trackingFieldError(custody, "2001")).toBe("Between 25 and 2000 m");
    expect(trackingFieldError(custody, "150.5")).toBe("Whole number of metres");
    expect(trackingFieldError(custody, "abc")).toBe("Whole number of metres");
    expect(trackingFieldError(spec("position_retention_days"), "6")).toBe("Between 7 and 365 days");
    expect(trackingFieldError(spec("ping_interval_s"), "61")).toBe("Between 5 and 60 s");
  });

  it("sends a number for a filled knob and an explicit null for an empty one — every knob present", () => {
    const form = trackingFormFromSchool({ custody_threshold_m: 300 });
    form.ping_interval_s = " 20 ";
    expect(trackingPayload(form)).toEqual({
      custody_threshold_m: 300,
      vicinity_radius_m: null,
      fix_accuracy_cap_m: null,
      position_retention_days: null,
      ping_interval_s: 20,
    });
    expect(Object.keys(trackingPayload(trackingFormFromSchool(null)))).toHaveLength(5);
  });

  it("marks the whole form valid only when every field is empty or in range", () => {
    const form = trackingFormFromSchool(null);
    expect(trackingFormValid(form)).toBe(true);
    form.vicinity_radius_m = "24";
    expect(trackingFormValid(form)).toBe(false);
    form.vicinity_radius_m = "100";
    expect(trackingFormValid(form)).toBe(true);
  });
});
