import { describe, expect, it } from "vitest";
import {
  ADMIN_INCIDENT_LABEL,
  PARENT_STUDENT_STATUS_LABEL,
  PARENT_STUDENT_STATUS_NOTE,
  noteFor,
  ADMIN_INCIDENT_VARIANT,
  BUS_AVAILABILITY_OPTIONS,
  BUS_STATUS_FILTERS,
  BUS_STATUS_LABEL,
  BUS_STATUS_VARIANT,
  NOTIFICATION_LABEL,
  NOTIFICATION_VARIANT,
  PARENT_INCIDENT_LABEL,
  PARENT_INCIDENT_VARIANT,
  RUN_STATUS_FILTERS,
  RUN_STATUS_LABEL,
  RUN_STATUS_VARIANT,
  STUDENT_STATUS_FILTERS,
  STUDENT_STATUS_LABEL,
  STUDENT_STATUS_VARIANT,
  labelFor,
  variantFor,
} from "@/lib/statusVocabulary";

/**
 * One label source per status domain (U17/R26-R28).
 *
 * These assertions are the mechanism that keeps the four domains from drifting
 * again: adding a value without labelling it fails here rather than shipping a
 * raw slug to a driver's phone.
 */

const STUDENT_VALUES = [
  "at-school", "on-bus", "dropped-off", "absent", "at-home", "unassigned",
  // The two values the rebuilt derivation introduced (U3, U6). Before U12 both
  // rendered as raw slugs on every surface that could receive them.
  "expected-on-bus", "unaccounted",
] as const;
const BUS_VALUES = ["active", "idle", "delayed", "out-of-service"] as const;
const RUN_VALUES = ["in-progress", "completed", "delayed"] as const;
const NOTIFICATION_VALUES = [
  "run-started", "student-boarded", "bus-approaching", "reached-school",
  "on-way-home", "dropped-off", "student-absent", "incident", "admin-notice",
  "ride-cancelled", "custom",
] as const;
const ADMIN_INCIDENT_VALUES = [
  "breakdown", "accident", "student", "traffic", "arrival", "other",
  "cancellation", "run-started", "run-completed",
  // The closure events (U11): every one of them changes what a completed run
  // means, so each needs office wording rather than a raw slug.
  "closure-refused", "force-closed", "handover-recorded", "action-reversed",
] as const;
const PARENT_INCIDENT_VALUES = [
  "breakdown", "accident", "student", "traffic", "other",
] as const;

describe("exhaustiveness", () => {
  const domains: [string, readonly string[], Record<string, string>, Record<string, string>][] = [
    ["student", STUDENT_VALUES, STUDENT_STATUS_LABEL, STUDENT_STATUS_VARIANT],
    ["parent student", STUDENT_VALUES, PARENT_STUDENT_STATUS_LABEL, STUDENT_STATUS_VARIANT],
    ["bus", BUS_VALUES, BUS_STATUS_LABEL, BUS_STATUS_VARIANT],
    ["run", RUN_VALUES, RUN_STATUS_LABEL, RUN_STATUS_VARIANT],
    ["notification", NOTIFICATION_VALUES, NOTIFICATION_LABEL, NOTIFICATION_VARIANT],
    ["admin incident", ADMIN_INCIDENT_VALUES, ADMIN_INCIDENT_LABEL, ADMIN_INCIDENT_VARIANT],
    ["parent incident", PARENT_INCIDENT_VALUES, PARENT_INCIDENT_LABEL, PARENT_INCIDENT_VARIANT],
  ];

  it.each(domains)("%s: every value has a label and a variant", (_name, values, labels, variants) => {
    for (const value of values) {
      expect(labels[value], `missing label for "${value}"`).toBeTruthy();
      expect(variants[value], `missing variant for "${value}"`).toBeTruthy();
    }
    expect(Object.keys(labels).sort()).toEqual([...values].sort());
    expect(Object.keys(variants).sort()).toEqual([...values].sort());
  });
});

describe("casing convention", () => {
  // R28: sentence case throughout. The parent alerts page used to render
  // "Dropped Off" while the parent home page rendered "Dropped off" — the same
  // word, two ways, on adjacent screens.
  const everyLabel = [
    ...Object.values(STUDENT_STATUS_LABEL),
    ...Object.values(PARENT_STUDENT_STATUS_LABEL),
    ...Object.values(BUS_STATUS_LABEL),
    ...Object.values(RUN_STATUS_LABEL),
    ...Object.values(NOTIFICATION_LABEL),
    ...Object.values(ADMIN_INCIDENT_LABEL),
    ...Object.values(PARENT_INCIDENT_LABEL),
  ];

  it("capitalises only the first word", () => {
    for (const label of everyLabel) {
      const rest = label.split(" ").slice(1);
      const titleCased = rest.filter((w) => /^[A-Z]/.test(w));
      expect(titleCased, `"${label}" is Title Case, not sentence case`).toEqual([]);
    }
  });

  it("starts every label with a capital", () => {
    for (const label of everyLabel) {
      expect(label[0], `"${label}" does not start with a capital`).toBe(label[0].toUpperCase());
    }
  });
});

describe("no raw slugs reach a user", () => {
  it("never renders a hyphenated identifier as a label", () => {
    const everyLabel = [
      ...Object.values(STUDENT_STATUS_LABEL),
      ...Object.values(PARENT_STUDENT_STATUS_LABEL),
      ...Object.values(BUS_STATUS_LABEL),
      ...Object.values(RUN_STATUS_LABEL),
      ...Object.values(NOTIFICATION_LABEL),
      ...Object.values(ADMIN_INCIDENT_LABEL),
    ];
    for (const label of everyLabel) {
      // "Out of service" is fine; "out-of-service" is a slug.
      expect(label, `"${label}" looks like a raw slug`).not.toMatch(/^[a-z]+(-[a-z]+)+$/);
    }
  });

  it("falls back rather than echoing an unknown value", () => {
    expect(labelFor(STUDENT_STATUS_LABEL, "brand-new-state")).toBe("Unknown");
    expect(labelFor(STUDENT_STATUS_LABEL, null)).toBe("Unknown");
    expect(variantFor(STUDENT_STATUS_VARIANT, "brand-new-state")).toBe("secondary");
  });

  it("allows an explicit echo where the caller wants one", () => {
    // The parent alerts feed merges two type namespaces and prefers showing the
    // raw type over "Unknown" for a value it has never seen.
    expect(labelFor(NOTIFICATION_LABEL, "future-type", "future-type")).toBe("future-type");
  });
});

describe("filter options derive from the labels", () => {
  it.each([
    ["student", STUDENT_STATUS_FILTERS, STUDENT_STATUS_LABEL],
    ["bus", BUS_STATUS_FILTERS, BUS_STATUS_LABEL],
    ["run", RUN_STATUS_FILTERS, RUN_STATUS_LABEL],
  ])("%s filters carry one option per label plus All", (_name, filters, labels) => {
    expect(filters[0]).toEqual({ value: "all", label: "All statuses" });
    expect(filters.length).toBe(Object.keys(labels).length + 1);
    for (const option of filters.slice(1)) {
      expect(labels[option.value]).toBe(option.label);
    }
  });

  it("offers both availability options, matching the bus out-of-service value", () => {
    expect(BUS_AVAILABILITY_OPTIONS.map((o) => o.value)).toEqual(["in-service", "out-of-service"]);
    expect(BUS_STATUS_LABEL["out-of-service"]).toBe("Out of service");
  });
});

describe("cross-role agreement", () => {
  it("gives admin and parent the same wording for shared incident types", () => {
    for (const type of PARENT_INCIDENT_VALUES) {
      if (type === "traffic") continue; // admin says "Heavy traffic / delay", deliberately operational
      expect(ADMIN_INCIDENT_LABEL[type]).toBe(PARENT_INCIDENT_LABEL[type]);
    }
  });

  it("hides office-only incident types from the parent vocabulary", () => {
    // The closure events matter most here: they name other people's children,
    // so a leak into parent wording would be a disclosure, not a duplicate.
    for (const officeOnly of [
      "arrival", "run-started", "run-completed", "cancellation",
      "closure-refused", "force-closed", "handover-recorded", "action-reversed",
    ]) {
      expect(Object.keys(PARENT_INCIDENT_LABEL)).not.toContain(officeOnly);
    }
  });
});


describe("parent wording for the derived values (U12/R27)", () => {
  it("never shows a parent the bare operational term for unaccounted", () => {
    // The force-close raises a phone-call obligation precisely so a person
    // delivers this news. The app must not get there first, in a word chosen
    // for a dispatcher.
    expect(PARENT_STUDENT_STATUS_LABEL.unaccounted).not.toBe(
      STUDENT_STATUS_LABEL.unaccounted,
    );
    expect(PARENT_STUDENT_STATUS_LABEL.unaccounted.toLowerCase()).not.toContain("unaccounted");
  });

  it("tells the parent what happens next", () => {
    const note = noteFor(PARENT_STUDENT_STATUS_NOTE, "unaccounted");
    expect(note).toBeTruthy();
    expect(note!.toLowerCase()).toContain("call");
  });

  it("keeps admin and driver on the operational term", () => {
    expect(STUDENT_STATUS_LABEL.unaccounted).toBe("Unaccounted");
  });

  it("distinguishes a presumed rider from a confirmed one on every surface", () => {
    for (const map of [STUDENT_STATUS_LABEL, PARENT_STUDENT_STATUS_LABEL]) {
      expect(map["expected-on-bus"]).not.toBe(map["on-bus"]);
    }
    // Not styled as a confirmed success: the presumption is not yet evidence.
    expect(STUDENT_STATUS_VARIANT["expected-on-bus"]).not.toBe(
      STUDENT_STATUS_VARIANT["on-bus"],
    );
  });

  it("shares every other value with the operational vocabulary", () => {
    for (const [value, label] of Object.entries(STUDENT_STATUS_LABEL)) {
      if (value === "unaccounted") continue;
      expect(PARENT_STUDENT_STATUS_LABEL[value as keyof typeof STUDENT_STATUS_LABEL]).toBe(label);
    }
  });

  it("renders no raw slug for either new value on any surface", () => {
    for (const map of [STUDENT_STATUS_LABEL, PARENT_STUDENT_STATUS_LABEL]) {
      for (const value of ["expected-on-bus", "unaccounted"] as const) {
        expect(labelFor(map, value)).not.toBe("Unknown");
        expect(labelFor(map, value)).not.toMatch(/^[a-z]+(-[a-z]+)+$/);
      }
    }
  });
});
