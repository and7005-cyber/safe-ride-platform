// GPS plan U4 — the staff exceptions panel's pure pieces (R21, R22).
// Every kind migration 016 allows has office wording; the fix, distance,
// children and ledger lines read in plain words; nothing here is parent-facing.
import { describe, expect, it } from "vitest";
import {
  EXCEPTION_KINDS,
  EXCEPTION_KIND_LABEL,
  EXCEPTION_KIND_NOTE,
  childrenLine,
  distanceLine,
  eventLine,
  fixLine,
  formatDistance,
  kindLabel,
  reasonLabel,
  responseLabel,
  seenAtStopLine,
  statusBadge,
  unreviewedCount,
  type RunException,
  type RunExceptionEvent,
} from "@/features/admin/components/RunExceptionsPanel";
import { ADMIN_INCIDENT_LABEL, PARENT_INCIDENT_LABEL } from "@/lib/statusVocabulary";

// The six kinds migration 016's CHECK constraint allows, verbatim.
const MIGRATION_016_KINDS = [
  "custody-away", "stop-bypassed", "absent-remote", "absent-attested",
  "unverified", "implausible-movement",
] as const;

function event(over: Partial<RunExceptionEvent> = {}): RunExceptionEvent {
  return {
    id: "e1", student_id: null, distance_m: null, prompt_state: null,
    delivered_at: null, shown_at: null, response: null,
    call_now_due_at: null, call_now_sent_at: null,
    created_at: "2026-06-17T10:51:50+00:00",
    ...over,
  };
}

function exception(over: Partial<RunException> = {}): RunException {
  return {
    id: "x1", kind: "stop-bypassed", reason: null, stop_order: 1, stop_name: "Kilimani",
    student_name: null, students: [], fix_lat: null, fix_lng: null, fix_accuracy_m: null,
    fix_captured_at: null, distance_m: null, seen_at_stop: null, status: null,
    created_at: "2026-06-17T10:51:55+00:00", reviewed_at: null, reviewed_by_display: null,
    events: [],
    ...over,
  };
}

describe("every exception kind is labelled", () => {
  it("covers exactly the kinds migration 016 allows", () => {
    expect([...EXCEPTION_KINDS].sort()).toEqual([...MIGRATION_016_KINDS].sort());
    expect(Object.keys(EXCEPTION_KIND_LABEL).sort()).toEqual([...MIGRATION_016_KINDS].sort());
    expect(Object.keys(EXCEPTION_KIND_NOTE).sort()).toEqual([...MIGRATION_016_KINDS].sort());
  });

  it("uses sentence case and never a raw slug", () => {
    for (const label of Object.values(EXCEPTION_KIND_LABEL)) {
      expect(label[0]).toBe(label[0].toUpperCase());
      expect(label.split(" ").slice(1).filter((w) => /^[A-Z]/.test(w))).toEqual([]);
      expect(label).not.toMatch(/^[a-z]+(-[a-z]+)+$/);
    }
  });

  it("names the two alert-raising kinds the way the Alerts page does", () => {
    // One thing, one name: the office reads the alert, then opens the run.
    expect(EXCEPTION_KIND_LABEL["stop-bypassed"]).toBe(ADMIN_INCIDENT_LABEL["stop-bypassed"]);
    expect(EXCEPTION_KIND_LABEL["absent-remote"]).toBe(ADMIN_INCIDENT_LABEL["absent-remote"]);
  });

  it("has no parent wording for any kind (R22)", () => {
    for (const kind of MIGRATION_016_KINDS) {
      expect(PARENT_INCIDENT_LABEL).not.toHaveProperty(kind);
    }
  });

  it("falls back to a generic label rather than echoing an unknown kind", () => {
    expect(kindLabel("brand-new-kind")).toBe("Stop exception");
  });
});

describe("reasons and responses read as words", () => {
  it("labels the known reasons", () => {
    expect(reasonLabel("too-coarse")).toBe("fix too coarse");
    expect(reasonLabel("no-fix")).toBe("no fix for this action");
    expect(reasonLabel("stop-unverified")).toBe("stop position unverified");
    expect(reasonLabel("jump")).toBe("implausible jump between fixes");
    expect(reasonLabel(null)).toBeNull();
  });

  it("opens out an unknown reason instead of hiding it", () => {
    expect(reasonLabel("some-new-reason")).toBe("some new reason");
  });

  it("labels every answer the prompts record", () => {
    for (const response of ["confirmed", "dismissed", "resolution", "told-me", "not-at-stop", "undo"]) {
      const label = responseLabel(response)!;
      expect(label).toBeTruthy();
      expect(label).not.toMatch(/-/);
    }
  });
});

describe("the fix corroborates and does not prove", () => {
  it("says nothing when no fix was stored", () => {
    expect(fixLine(exception())).toBeNull();
    // A purged trail leaves the coordinates null while accuracy may linger.
    expect(fixLine(exception({ fix_accuracy_m: 20 }))).toBeNull();
  });

  it("states the phone's claimed accuracy beside the capture time", () => {
    const line = fixLine(exception({
      fix_lat: -1.29, fix_lng: 36.78, fix_accuracy_m: 20, fix_captured_at: "2026-06-17T10:51:50+00:00",
    }))!;
    expect(line).toMatch(/^Phone reported within 20 m at \d\d:\d\d$/);
  });

  it("formats distances in metres below a kilometre and kilometres above", () => {
    expect(formatDistance(180.4)).toBe("180 m");
    expect(formatDistance(2280)).toBe("2.3 km");
    expect(formatDistance(null)).toBeNull();
    expect(distanceLine(exception({ kind: "custody-away", distance_m: 2280 }))).toBe("2.3 km from the stop");
    expect(distanceLine(exception({ kind: "implausible-movement", distance_m: 5200 }))).toBe(
      "Moved 5.2 km between fixes",
    );
    expect(distanceLine(exception({ kind: "stop-bypassed" }))).toBeNull();
  });

  it("renders seen-at-stop only when the server decided it", () => {
    expect(seenAtStopLine(exception())).toBeNull();
    expect(seenAtStopLine(exception({ seen_at_stop: true }))).toBe("Bus seen at stop: yes");
    expect(seenAtStopLine(exception({ seen_at_stop: false }))).toBe("Bus seen at stop: no");
  });
});

describe("the children a row is about", () => {
  it("lists the still-unaccounted children of an open bypassed stop", () => {
    expect(childrenLine(exception({
      status: "open", students: [{ id: "a", name: "Brian" }, { id: "b", name: "Amina" }],
    }))).toBe("Still no outcome: Brian, Amina");
  });

  it("says so when a bypassed stop has resolved", () => {
    expect(childrenLine(exception({ status: "resolved" }))).toBe(
      "Every child at this stop now has an outcome",
    );
  });

  it("names the child for student-keyed kinds", () => {
    expect(childrenLine(exception({ kind: "absent-remote", student_name: "Kevin Mwangi" }))).toBe(
      "Kevin Mwangi",
    );
  });

  it("has nothing to say for a run-level kind", () => {
    expect(childrenLine(exception({ kind: "implausible-movement", stop_name: null }))).toBeNull();
  });
});

describe("the ledger in plain words", () => {
  it("reads an answered prompt with its answer", () => {
    expect(eventLine(event({ prompt_state: "answered", response: "confirmed", distance_m: 2280 })))
      .toMatch(/^\d\d:\d\d — prompt answered: confirmed by the driver \(2\.3 km away\)$/);
  });

  it("distinguishes pending, delivered and shown", () => {
    expect(eventLine(event({ prompt_state: "pending" }))).toContain("waiting to reach the driver");
    expect(eventLine(event({ prompt_state: "pending", delivered_at: "2026-06-17T10:51:51+00:00" })))
      .toContain("not yet shown");
    expect(eventLine(event({
      prompt_state: "pending", delivered_at: "2026-06-17T10:51:51+00:00",
      shown_at: "2026-06-17T10:51:52+00:00",
    }))).toMatch(/prompt shown to the driver at \d\d:\d\d, not yet answered/);
  });

  it("reads an auto-resolved prompt as closed unanswered", () => {
    expect(eventLine(event({ prompt_state: "unanswered" }))).toContain("prompt closed unanswered");
  });

  it("reads a resolution tap and a promptless record", () => {
    expect(eventLine(event({ response: "resolution", student_id: "s1" }))).toContain(
      "a child at this stop was recorded",
    );
    expect(eventLine(event())).toContain("tap recorded, no prompt shown");
  });

  it("states the call-now notice's fate", () => {
    expect(eventLine(event({
      prompt_state: "answered", response: "not-at-stop",
      call_now_due_at: "2026-06-17T10:52:00+00:00", call_now_sent_at: "2026-06-17T10:52:02+00:00",
    }))).toMatch(/the driver was not at the stop · call-now notice sent to the family at \d\d:\d\d/);
    expect(eventLine(event({ prompt_state: "answered", response: "dismissed", call_now_due_at: "2026-06-17T10:52:00+00:00" })))
      .toContain("call-now notice due, not yet sent");
  });
});

describe("the count the badge shows", () => {
  it("is the stored review stamp, not open/resolved", () => {
    expect(unreviewedCount([
      exception({ status: "open", reviewed_at: "2026-06-17T12:00:00+00:00" }),
      exception({ status: "resolved" }),
      exception({ status: null }),
    ])).toBe(2);
    expect(unreviewedCount([])).toBe(0);
  });
});

// --- the custody tap's derived status and children (GPS plan U9) ----------------

describe("a custody tap's row", () => {
  it("lists the tapped children plainly, not as 'still no outcome'", () => {
    expect(childrenLine(exception({
      kind: "custody-away", status: "open",
      students: [{ id: "a", name: "Wanjiru" }, { id: "b", name: "Brian" }],
    }))).toBe("Wanjiru, Brian");
    expect(childrenLine(exception({
      kind: "unverified", students: [{ id: "a", name: "Wanjiru" }],
    }))).toBe("Wanjiru");
  });

  it("badges every derived status the server can send, and nothing for none", () => {
    expect(statusBadge("open")).toEqual({ label: "Open", variant: "warning" });
    expect(statusBadge("resolved")).toEqual({ label: "Resolved", variant: "success" });
    expect(statusBadge("confirmed")).toEqual({ label: "Confirmed by driver", variant: "success" });
    expect(statusBadge("retracted")).toEqual({ label: "Retracted", variant: "secondary" });
    // The remote absent's settled classes (GPS plan U10/R17).
    expect(statusBadge("attested")).toEqual({ label: "Attested by driver", variant: "secondary" });
    expect(statusBadge("uncorroborated")).toEqual({ label: "Uncorroborated", variant: "warning" });
    expect(statusBadge(null)).toBeNull();
  });

  it("wordings are sentence case with no raw slug", () => {
    for (const status of ["open", "resolved", "confirmed", "retracted", "attested", "uncorroborated"] as const) {
      const label = statusBadge(status)!.label;
      expect(label[0]).toBe(label[0]!.toUpperCase());
      expect(label).not.toMatch(/[a-z]-[a-z]/);
    }
  });
});

describe("the custody undo on the ledger (GPS plan U9)", () => {
  it("reads a retraction as undone by the driver, answered or appended", () => {
    expect(responseLabel("retracted")).toBe("undone by the driver");
    expect(eventLine(event({ prompt_state: "answered", response: "retracted" }))).toContain(
      "prompt answered: undone by the driver",
    );
    const appended = eventLine(event({ prompt_state: null, response: "retracted", student_id: "a" }));
    expect(appended).toContain("undone by the driver");
    expect(appended).not.toContain("prompt");
  });
});
