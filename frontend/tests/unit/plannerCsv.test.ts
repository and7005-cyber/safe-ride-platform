import { describe, expect, it } from "vitest";
import {
  PLANNER_STOPS_CAP,
  importPlannerRows,
  type PlannerCsvRow,
} from "@/features/admin/components/PlannerCsvDialog";

const emptyStarter: PlannerCsvRow[] = [{ address: "", pickup_time: "" }];

describe("importPlannerRows", () => {
  it("maps headers case-insensitively (trims and normalises spacing)", () => {
    const result = importPlannerRows(
      [
        { Address: "Yaya Centre, Nairobi", " PICKUP_TIME ": "06:45", LAT: "", Lng: "" },
        { Address: "Sarit Centre, Westlands", " PICKUP_TIME ": "", LAT: "-1.2606", Lng: "36.8028" },
      ],
      emptyStarter,
    );
    expect(result.errors).toEqual([]);
    expect(result.added).toBe(2);
    expect(result.rows).toEqual([
      { address: "Yaya Centre, Nairobi", pickup_time: "06:45", lat: null, lng: null },
      { address: "Sarit Centre, Westlands", pickup_time: "", lat: -1.2606, lng: 36.8028 },
    ]);
  });

  it("rejects a bad pickup_time without blocking valid rows", () => {
    const result = importPlannerRows(
      [
        { address: "Stop A", pickup_time: "7:45" }, // needs HH:MM
        { address: "Stop B", pickup_time: "07:45" },
      ],
      emptyStarter,
    );
    expect(result.added).toBe(1);
    expect(result.rows).toEqual([{ address: "Stop B", pickup_time: "07:45", lat: null, lng: null }]);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toMatch(/Row 2: pickup_time must be HH:MM/);
  });

  it("rejects lat without lng (and vice versa)", () => {
    const result = importPlannerRows(
      [
        { address: "Stop A", lat: "-1.28", lng: "" },
        { address: "Stop B", lat: "", lng: "36.82" },
        { address: "Stop C", lat: "not-a-number", lng: "36.82" },
      ],
      emptyStarter,
    );
    expect(result.added).toBe(0);
    expect(result.errors).toEqual([
      'Row 2: lat and lng must be provided together',
      'Row 3: lat and lng must be provided together',
      'Row 4: lat/lng must be numbers',
    ]);
    // Nothing valid: the single empty starter row is kept.
    expect(result.rows).toEqual(emptyStarter);
  });

  it("requires an address", () => {
    const result = importPlannerRows([{ address: "   ", pickup_time: "06:00" }], emptyStarter);
    expect(result.added).toBe(0);
    expect(result.errors).toEqual(["Row 2: address is required"]);
  });

  it("enforces the total planner cap with a count message", () => {
    const existing: PlannerCsvRow[] = Array.from({ length: PLANNER_STOPS_CAP - 1 }, (_, i) => ({
      address: `Existing ${i + 1}`,
      pickup_time: "",
    }));
    const incoming = [
      { address: "Fits" },
      { address: "Over 1" },
      { address: "Over 2" },
    ];
    const result = importPlannerRows(incoming, existing);
    expect(result.added).toBe(1);
    expect(result.rows).toHaveLength(PLANNER_STOPS_CAP);
    expect(result.rows[PLANNER_STOPS_CAP - 1]).toEqual({
      address: "Fits", pickup_time: "", lat: null, lng: null,
    });
    expect(result.errors).toEqual([
      `2 row(s) skipped: the planner is capped at ${PLANNER_STOPS_CAP} stops`,
    ]);
  });

  it("appends to existing non-empty rows and drops empty starter rows", () => {
    const existing: PlannerCsvRow[] = [
      { address: "Kept stop", pickup_time: "06:30", lat: -1.29, lng: 36.79 },
      { address: "", pickup_time: "" }, // empty row is dropped, not kept
    ];
    const result = importPlannerRows([{ address: "New stop" }], existing);
    expect(result.added).toBe(1);
    expect(result.rows).toEqual([
      { address: "Kept stop", pickup_time: "06:30", lat: -1.29, lng: 36.79 },
      { address: "New stop", pickup_time: "", lat: null, lng: null },
    ]);
  });

  it("replaces the single empty starter row instead of keeping it", () => {
    const result = importPlannerRows([{ address: "Only stop" }], emptyStarter);
    expect(result.rows).toEqual([{ address: "Only stop", pickup_time: "", lat: null, lng: null }]);
  });
});
