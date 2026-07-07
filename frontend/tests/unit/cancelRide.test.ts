// U13 — ParentHomePage Cancel-a-Ride pure pieces. Covers AE4 (R14, R18): the
// pending chip names the cancelled scope, the cancel dialog offers the three
// same-day scopes with the today-only / rest-of-day-after-morning note, and
// withdrawChoices mirrors the server's withdraw semantics — nothing while not
// withdrawable, the row's own half for a partial (single choice, no dialog),
// and per-half or atomic whole-day single-call choices for a merged 'day' row
// (never two sequential half-deletes, which could strand the parent halfway).
import { describe, expect, it } from "vitest";
import {
  CANCELLATION_CHIP_LABEL,
  CANCEL_DIALOG_NOTE,
  CANCEL_SCOPE_OPTIONS,
  withdrawChoices,
} from "@/features/parent/ParentHomePage";
import type { CancelScope, ChildCancellation } from "@/features/parent/parentHooks";

const ALL_SCOPES: CancelScope[] = ["morning", "afternoon", "day"];

describe("CANCELLATION_CHIP_LABEL", () => {
  it("covers every cancellation scope and nothing else", () => {
    expect(Object.keys(CANCELLATION_CHIP_LABEL).sort()).toEqual([...ALL_SCOPES].sort());
  });

  it("maps each scope to its pending-chip copy", () => {
    expect(CANCELLATION_CHIP_LABEL).toEqual({
      morning: "AM ride cancelled",
      afternoon: "PM ride cancelled",
      day: "Rides cancelled today",
    });
  });
});

describe("cancel dialog copy", () => {
  it("offers Morning / Afternoon / Rest of day, in that order", () => {
    expect(CANCEL_SCOPE_OPTIONS).toEqual([
      { scope: "morning", label: "Morning" },
      { scope: "afternoon", label: "Afternoon" },
      { scope: "day", label: "Rest of day" },
    ]);
  });

  it("states the today-only rule and the rest-of-day-after-morning semantics", () => {
    expect(CANCEL_DIALOG_NOTE).toBe(
      "Cancellations apply to today only. 'Rest of day' after this morning's run cancels the afternoon ride.",
    );
    expect(CANCEL_DIALOG_NOTE).toMatch(/today only/i);
    expect(CANCEL_DIALOG_NOTE).toMatch(/cancels the afternoon ride/i);
  });
});

describe("withdrawChoices", () => {
  it("offers nothing when there is no cancellation", () => {
    expect(withdrawChoices(null)).toEqual([]);
    expect(withdrawChoices(undefined)).toEqual([]);
  });

  it("offers nothing once the server says the row is no longer withdrawable", () => {
    for (const scope of ALL_SCOPES) {
      expect(withdrawChoices({ scope, withdrawable: false })).toEqual([]);
    }
  });

  it("offers exactly the row's own half for a partial cancellation", () => {
    expect(withdrawChoices({ scope: "morning", withdrawable: true })).toEqual([
      { scope: "morning", label: "Morning ride" },
    ]);
    expect(withdrawChoices({ scope: "afternoon", withdrawable: true })).toEqual([
      { scope: "afternoon", label: "Afternoon ride" },
    ]);
  });

  it("offers each half plus the atomic whole-day delete for a merged 'day' row", () => {
    const day: ChildCancellation = { scope: "day", withdrawable: true };
    expect(withdrawChoices(day)).toEqual([
      { scope: "morning", label: "Morning ride" },
      { scope: "afternoon", label: "Afternoon ride" },
      { scope: "day", label: "Both rides" },
    ]);
  });

  it("maps every choice to a single DELETE scope the server accepts", () => {
    for (const scope of ALL_SCOPES) {
      for (const choice of withdrawChoices({ scope, withdrawable: true })) {
        expect(ALL_SCOPES).toContain(choice.scope);
      }
    }
  });
});
