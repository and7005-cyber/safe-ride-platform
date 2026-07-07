// U12 — ParentAlertsPage pure feed pieces. Covers AE2 (R5–R7, R22): the
// Recent/History windows are disjoint by construction (History's minAgeHours
// equals Recent's windowHours), the new 'admin-notice' and 'ride-cancelled'
// types render first-class (label, variant, type filter — with `custom` still
// excluded), and the period chips exempt only 'admin-notice': run-typed rows
// match their own chip, null-runType rows show under All only.
import { describe, expect, it } from "vitest";
import {
  NOTIFICATION_LABEL,
  NOTIFICATION_VARIANT,
  TYPE_FILTER_OPTIONS,
  TYPE_LABEL,
  matchesPeriod,
  type Period,
} from "@/features/parent/ParentAlertsPage";
import { HISTORY_WINDOW, RECENT_WINDOW } from "@/features/parent/parentHooks";

const PERIOD_VALUES: Period[] = ["all", "morning", "afternoon"];

describe("feed windows", () => {
  it("keeps Recent at the rolling 24h with no minimum age", () => {
    expect(RECENT_WINDOW).toEqual({ windowHours: 24, limit: 50 });
  });

  it("makes History disjoint from Recent: minAgeHours equals Recent's window", () => {
    expect(HISTORY_WINDOW).toEqual({ windowHours: 168, limit: 200, minAgeHours: 24 });
    expect(HISTORY_WINDOW.minAgeHours).toBe(RECENT_WINDOW.windowHours);
  });
});

describe("notification label/variant maps", () => {
  it("renders admin broadcasts as a warning-styled School Notice", () => {
    expect(NOTIFICATION_LABEL["admin-notice"]).toBe("School Notice");
    expect(NOTIFICATION_VARIANT["admin-notice"]).toBe("warning");
  });

  it("renders cancellation confirmations as a secondary-styled Ride Cancelled", () => {
    expect(NOTIFICATION_LABEL["ride-cancelled"]).toBe("Ride Cancelled");
    expect(NOTIFICATION_VARIANT["ride-cancelled"]).toBe("secondary");
  });

  it("styles every labelled notification type (no key drift between the maps)", () => {
    expect(Object.keys(NOTIFICATION_VARIANT).sort()).toEqual(
      Object.keys(NOTIFICATION_LABEL).sort(),
    );
  });
});

describe("TYPE_FILTER_OPTIONS", () => {
  it("picks up the new types automatically, labelled from the map", () => {
    expect(TYPE_FILTER_OPTIONS).toContainEqual({ value: "admin-notice", label: "School Notice" });
    expect(TYPE_FILTER_OPTIONS).toContainEqual({
      value: "ride-cancelled",
      label: "Ride Cancelled",
    });
  });

  it("still excludes the never-produced `custom` type", () => {
    expect(TYPE_FILTER_OPTIONS.some((o) => o.value === "custom")).toBe(false);
  });

  it("offers every other notification type and every incident type", () => {
    const values = TYPE_FILTER_OPTIONS.map((o) => o.value);
    for (const type of Object.keys(NOTIFICATION_LABEL).filter((t) => t !== "custom")) {
      expect(values).toContain(type);
    }
    for (const type of Object.keys(TYPE_LABEL)) {
      expect(values).toContain(type);
    }
  });
});

describe("matchesPeriod", () => {
  const morningRow = { type: "run-started", runType: "morning" };
  const afternoonCancellation = { type: "ride-cancelled", runType: "afternoon" };

  it("matches everything under All", () => {
    const rows = [morningRow, afternoonCancellation, { type: "reached-school", runType: null }];
    for (const item of rows) {
      expect(matchesPeriod(item, "all")).toBe(true);
    }
  });

  it("matches run-typed rows only under their own chip", () => {
    expect(matchesPeriod(morningRow, "morning")).toBe(true);
    expect(matchesPeriod(morningRow, "afternoon")).toBe(false);
    // AE2 companion: an afternoon cancellation surfaces under Afternoon.
    expect(matchesPeriod(afternoonCancellation, "afternoon")).toBe(true);
    expect(matchesPeriod(afternoonCancellation, "morning")).toBe(false);
  });

  it("shows null-runType rows under All only (whole-day cancellations included)", () => {
    const wholeDayCancellation = { type: "ride-cancelled", runType: null };
    expect(matchesPeriod(wholeDayCancellation, "all")).toBe(true);
    expect(matchesPeriod(wholeDayCancellation, "morning")).toBe(false);
    expect(matchesPeriod(wholeDayCancellation, "afternoon")).toBe(false);
  });

  it("exempts admin-notice: visible under every period chip (R22)", () => {
    const broadcast = { type: "admin-notice", runType: null };
    for (const period of PERIOD_VALUES) {
      expect(matchesPeriod(broadcast, period)).toBe(true);
    }
  });

  it("keeps the exemption type-scoped: other null-runType types stay All-only", () => {
    for (const type of Object.keys(NOTIFICATION_LABEL).filter((t) => t !== "admin-notice")) {
      expect(matchesPeriod({ type, runType: null }, "morning")).toBe(false);
      expect(matchesPeriod({ type, runType: null }, "afternoon")).toBe(false);
    }
  });
});
