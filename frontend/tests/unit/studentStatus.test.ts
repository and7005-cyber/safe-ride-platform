// U10 — StudentsPage pure status pieces. Covers AE1 (R1–R4): the label and
// variant maps span every derived display_status value (including 'at-home'
// and 'unassigned'), the status filter operates on the derived values — never
// the raw status — and partial-scope absence badges name their half of the day.
import { describe, expect, it } from "vitest";
import {
  STUDENT_STATUS_FILTERS,
  STUDENT_STATUS_LABEL,
  STUDENT_STATUS_VARIANT,
  absenceBadgeLabel,
  parentCancellationDescription,
  studentMatchesFilter,
  type StudentDisplayStatus,
} from "@/features/admin/StudentsPage";

const DERIVED_VALUES: StudentDisplayStatus[] = [
  "at-school",
  "on-bus",
  "dropped-off",
  "absent",
  "at-home",
  "unassigned",
];

describe("status label/variant maps", () => {
  it("covers every derived display_status value", () => {
    expect(Object.keys(STUDENT_STATUS_LABEL).sort()).toEqual([...DERIVED_VALUES].sort());
    expect(Object.keys(STUDENT_STATUS_VARIANT).sort()).toEqual([...DERIVED_VALUES].sort());
  });

  it("renders the R1 names, including Unassigned and At home", () => {
    expect(STUDENT_STATUS_LABEL).toEqual({
      "at-school": "At school",
      "on-bus": "On bus",
      "dropped-off": "Dropped off",
      absent: "Absent today",
      "at-home": "At home",
      unassigned: "Unassigned",
    });
  });

  it("styles at-home as secondary and unassigned as outline", () => {
    expect(STUDENT_STATUS_VARIANT).toEqual({
      "at-school": "secondary",
      "on-bus": "success",
      "dropped-off": "warning",
      absent: "destructive",
      "at-home": "secondary",
      unassigned: "outline",
    });
  });
});

describe("STUDENT_STATUS_FILTERS", () => {
  it("offers All plus one option per derived value, labelled from the label map", () => {
    expect(STUDENT_STATUS_FILTERS[0]).toEqual({ value: "all", label: "All statuses" });
    expect(STUDENT_STATUS_FILTERS.slice(1)).toEqual(
      Object.entries(STUDENT_STATUS_LABEL).map(([value, label]) => ({ value, label })),
    );
  });
});

describe("studentMatchesFilter", () => {
  it("matches every derived value under 'all'", () => {
    for (const value of DERIVED_VALUES) {
      expect(studentMatchesFilter({ display_status: value }, "all")).toBe(true);
    }
  });

  it("matches each derived value only against its own filter", () => {
    for (const filter of DERIVED_VALUES) {
      for (const value of DERIVED_VALUES) {
        expect(studentMatchesFilter({ display_status: value }, filter)).toBe(value === filter);
      }
    }
  });

  it("filters on display_status, never the raw status (stale absent shows at home)", () => {
    const staleAbsent = { status: "absent", display_status: "at-home" };
    expect(studentMatchesFilter(staleAbsent, "absent")).toBe(false);
    expect(studentMatchesFilter(staleAbsent, "at-home")).toBe(true);
  });

  it("matches a row with no display_status only under 'all'", () => {
    expect(studentMatchesFilter({}, "all")).toBe(true);
    expect(studentMatchesFilter({}, "at-school")).toBe(false);
  });
});

describe("absenceBadgeLabel", () => {
  it("keeps the whole-day wording", () => {
    expect(absenceBadgeLabel("day")).toBe("Absent today");
  });

  it("names the half-day for partial parent cancellations", () => {
    expect(absenceBadgeLabel("morning")).toBe("Absent (AM)");
    expect(absenceBadgeLabel("afternoon")).toBe("Absent (PM)");
  });

  it("treats a missing scope as whole-day", () => {
    expect(absenceBadgeLabel(undefined)).toBe("Absent today");
    expect(absenceBadgeLabel(null)).toBe("Absent today");
  });
});

describe("parentCancellationDescription", () => {
  it("names the student and the cancelled scope", () => {
    expect(parentCancellationDescription("Faith Achieng", "morning")).toContain(
      "the morning ride for Faith Achieng",
    );
    expect(parentCancellationDescription("Faith Achieng", "afternoon")).toContain(
      "the afternoon ride for Faith Achieng",
    );
    expect(parentCancellationDescription("Faith Achieng", "day")).toContain(
      "today's rides for Faith Achieng",
    );
  });

  it("explains both resolutions (escalate or remove)", () => {
    const text = parentCancellationDescription("Faith Achieng", "afternoon");
    expect(text).toMatch(/full-day absence/i);
    expect(text).toMatch(/remove the cancellation/i);
  });
});
