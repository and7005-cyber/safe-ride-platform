import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  EXCEPTION_KIND_LABEL,
  PLAUSIBILITY_FLAGS,
  reasonLabel,
  statusBadge,
  type RunException,
} from "@/features/admin/components/RunExceptionsPanel";
import { TRACKING_FIELDS } from "@/features/admin/trackingFields";
import { LOCATION_EXPLAINER } from "@/features/driver/DriverRunPage";
import { POSITION_SOURCE_LABEL, formatAge, freshnessLabel } from "@/lib/positionFreshness";
import {
  ADMIN_INCIDENT_LABEL,
  BUS_STATUS_LABEL,
  NOTIFICATION_LABEL,
  PARENT_STUDENT_STATUS_LABEL,
  STUDENT_STATUS_LABEL,
} from "@/lib/statusVocabulary";

/**
 * The published guides are part of the product (U15/R28).
 *
 * The manual told drivers to end a run "knowing some are unconfirmed" long after
 * the app started refusing to, and gave parents a status table in the wrong
 * capitalisation — because nothing tied the words in the guides to the words in
 * the code. A driver reading the old instruction on a live afternoon route has
 * one obvious escape, and it is marking children absent.
 *
 * These assertions are that tie. They will not judge the prose; they only fail
 * when a value the app can render has no wording in the guide that has to
 * explain it.
 */

const MANUAL = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/user-manual");
const read = (name: string) => readFileSync(resolve(MANUAL, name), "utf8");

describe("the user manual keeps up with the shipped vocabulary", () => {
  it("documents every student status the office can be shown", () => {
    const admin = read("admin-guide.md");
    for (const label of Object.values(STUDENT_STATUS_LABEL)) {
      expect(admin, `admin guide never mentions "${label}"`).toContain(label);
    }
  });

  it("documents every student status a parent can be shown, in the parent's words", () => {
    const parent = read("parent-guide.md");
    for (const label of Object.values(PARENT_STUDENT_STATUS_LABEL)) {
      // 'Unassigned' is an admin-only wrapper for a student on no route; it
      // never reaches a parent, so the parent guide has no reason to carry it.
      if (label === "Unassigned") continue;
      expect(parent, `parent guide never mentions "${label}"`).toContain(label);
    }
  });

  it("never shows a parent the operational word for an unaccounted child", () => {
    // The office phones these families; the guide must not pre-empt that call
    // with a dispatcher's term, and neither must the app.
    expect(read("parent-guide.md")).not.toContain(STUDENT_STATUS_LABEL.unaccounted);
    expect(read("parent-guide.md")).toContain(PARENT_STUDENT_STATUS_LABEL.unaccounted);
  });

  it("documents every bus status, including the availability the office sets", () => {
    const admin = read("admin-guide.md");
    for (const label of Object.values(BUS_STATUS_LABEL)) {
      expect(admin, `admin guide never mentions bus status "${label}"`).toContain(label);
    }
  });

  it("documents the office-only alerts the closure lifecycle raises", () => {
    const admin = read("admin-guide.md");
    for (const type of [
      "closure-refused",
      "force-closed",
      "handover-recorded",
      "action-reversed",
      // The two stop exceptions that reach the alerts feed (GPS plan U4).
      "stop-bypassed",
      "absent-remote",
    ] as const) {
      const label = ADMIN_INCIDENT_LABEL[type];
      expect(admin, `admin guide never mentions the "${label}" alert`).toContain(label);
    }
  });

  it("no longer tells drivers to end a run over unconfirmed children", () => {
    // The exact instruction the closure gate invalidated. Left in place it is
    // worse than missing documentation: it describes an escape hatch that no
    // longer exists and points at the one that misreports children.
    const driver = read("driver-guide.md");
    expect(driver).not.toMatch(/accept the warning knowingly/i);
    expect(driver).not.toMatch(/Not yet confirmed dropped off/i);
    // And it explains what to do instead.
    expect(driver).toMatch(/Still to account for/);
    expect(driver).toMatch(/Off-route/);
  });

  it("tells drivers they can undo their own entry rather than ring the office", () => {
    const driver = read("driver-guide.md");
    expect(driver).not.toMatch(/You cannot undo an absence yourself/i);
    expect(driver).toMatch(/\bUndo\b/);
  });
});

/**
 * GPS plan U13 (R6). Release 3 breaks four written promises — "the app does
 * not use your phone's GPS", "never asks for location permission", "there is
 * still no un-board button", and the admin guide's "drivers can't undo these"
 * — and adds a vocabulary the office, the driver and the parent each meet on
 * their own screen. The same tie as above: a label the app renders must have
 * wording in the guide that explains it.
 */
describe("the guides describe the GPS release in the shipped words", () => {
  const EXCEPTION_STATUSES: NonNullable<RunException["status"]>[] = [
    "open",
    "resolved",
    "confirmed",
    "retracted",
    "attested",
    "uncorroborated",
  ];

  it("no longer promises drivers the app never uses location", () => {
    const driver = read("driver-guide.md");
    expect(driver).not.toMatch(/does not use your phone's GPS/i);
    expect(driver).not.toMatch(/never uses your phone's GPS/i);
    expect(driver).not.toMatch(/never asks for location permission/i);
    expect(driver).not.toMatch(/no un-board button/i);
    // The replacement promise: runs only, nothing outside them.
    expect(driver).toMatch(/Start Run/);
    expect(driver).toMatch(/End Run/);
    expect(driver).toMatch(/Nothing outside a run/i);
  });

  it("carries the explainer's headings and the two location banners for drivers", () => {
    const driver = read("driver-guide.md");
    expect(driver).toContain(LOCATION_EXPLAINER.title);
    for (const point of LOCATION_EXPLAINER.points) {
      expect(driver, `driver guide never mentions the explainer heading "${point.label}"`).toContain(
        `**${point.label}**`,
      );
    }
    expect(driver).toContain(LOCATION_EXPLAINER.continueLabel);
    // LocationStatusBanner's two titles and its steps toggle.
    expect(driver).toContain("Location off");
    expect(driver).toContain("Turn on precise location");
    expect(driver).toContain("How to turn it on");
  });

  it("describes the three prompts with the buttons as the cards word them", () => {
    const driver = read("driver-guide.md");
    // NudgeQueue's answer labels, verbatim — a driver reading the guide on the
    // road must find the same words on the card.
    for (const label of ["Yes — they told me", "No — I wasn't at the stop", "Confirm", "Undo", "Boarded", "Dropped off", "Absent"]) {
      expect(driver, `driver guide never mentions the prompt button "${label}"`).toContain(`**${label}**`);
    }
    expect(driver).toContain("Did a parent or the office tell you");
    expect(driver).toContain("Confirm, or undo?");
    expect(driver).toContain("Mark boarded or absent?");
  });

  it("documents every stop-exception kind and every status the panel can show", () => {
    const admin = read("admin-guide.md");
    for (const label of Object.values(EXCEPTION_KIND_LABEL)) {
      expect(admin, `admin guide never mentions the exception kind "${label}"`).toContain(label);
    }
    for (const status of EXCEPTION_STATUSES) {
      const badge = statusBadge(status);
      expect(badge, `statusBadge(${status}) must define a label`).not.toBeNull();
      expect(admin, `admin guide never mentions the exception status "${badge!.label}"`).toContain(
        `**${badge!.label}**`,
      );
    }
  });

  it("explains every plausibility flag in the panel's plain words, and why identical accuracy is not one", () => {
    const admin = read("admin-guide.md");
    for (const flag of PLAUSIBILITY_FLAGS) {
      const words = reasonLabel(flag)!;
      expect(admin, `admin guide never explains the flag "${flag}" ("${words}")`).toContain(words);
    }
    // The dropped arm (position_rules.py): iPhones quantise accuracy.
    expect(admin).toMatch(/iPhones? report accuracy in fixed steps/i);
  });

  it("explains the phone line and the seen-at-stop line without letting a fix prove anything", () => {
    const admin = read("admin-guide.md");
    expect(admin).toContain("Phone reported within");
    expect(admin).toContain("Bus seen at stop");
    expect(admin).toMatch(/corroborates; it does not prove/);
  });

  it("documents the Tracking card's five fields, their defaults, and the Use default control", () => {
    const admin = read("admin-guide.md");
    for (const spec of TRACKING_FIELDS) {
      expect(admin, `admin guide never mentions the Tracking field "${spec.label}"`).toContain(`**${spec.label}**`);
      expect(admin, `admin guide never states the bounds of "${spec.label}"`).toContain(`${spec.min}–${spec.max}`);
    }
    expect(admin).toContain("**Use default**");
    // Staleness and the fix-wait budget have no knob; the guide must say so
    // rather than let the office look for one.
    expect(admin).toMatch(/system-wide, not per school/);
  });

  it("uses the maps' freshness and source wording for the office and for parents", () => {
    const admin = read("admin-guide.md");
    const parent = read("parent-guide.md");
    // freshnessLabel: "updated <age>" fresh, "last seen <age>" stale. The
    // guides carry one worked example of each, so derive both from the module
    // rather than spell them out: a rewording of the label fails here.
    const at = new Date(0).toISOString();
    const fresh = freshnessLabel({ position_at: at, stale: false }, 12_000)!;
    const stale = freshnessLabel({ position_at: at, stale: true }, 240_000)!;
    expect(fresh).toBe(`updated ${formatAge(12)}`);
    expect(stale).toBe(`last seen ${formatAge(240)}`);
    for (const guide of [admin, parent]) {
      expect(guide, `guide never shows the fresh example "${fresh}"`).toContain(fresh);
      expect(guide, `guide never shows the stale example "${stale}"`).toContain(stale);
    }
    for (const label of Object.values(POSITION_SOURCE_LABEL)) {
      expect(admin, `admin guide never mentions the position source "${label}"`).toContain(label);
    }
    // FleetMapPage's two GPS-state lines.
    expect(admin).toContain("No GPS for this run");
    expect(admin).toContain("Phone GPS off since the last tap");
  });

  it("documents the call-now notice and the three neutral corrections in the parent's words", () => {
    const parent = read("parent-guide.md");
    for (const type of ["absent-call-now", "boarding-corrected", "absence-corrected", "dropoff-corrected"] as const) {
      const label = NOTIFICATION_LABEL[type];
      expect(parent, `parent guide never mentions the "${label}" notification`).toContain(`**${label}**`);
    }
    // Parents never see exceptions (R22): the guide names nothing the panel does.
    for (const label of Object.values(EXCEPTION_KIND_LABEL)) {
      expect(parent, `parent guide leaks the exception kind "${label}"`).not.toContain(label);
    }
  });

  it("ships the driver briefing with the explainer's five notice headings and the sign-off", () => {
    const briefing = read("driver-gps-briefing.md");
    expect(briefing).toContain(LOCATION_EXPLAINER.title);
    for (const point of LOCATION_EXPLAINER.points) {
      expect(briefing, `briefing never carries the notice heading "${point.label}"`).toContain(`**${point.label}**`);
    }
    for (const line of ["Delivered by Kuumbai", "Confirmed by Kuumbai", "School informed"]) {
      expect(briefing).toContain(line);
    }
    expect(briefing).toMatch(/before the Release 3 deploy/);
    // The notice's substance, not just its headings.
    expect(briefing).toMatch(/90 days/);
    expect(briefing).toMatch(/transport coordinator/);
  });

  it("no longer tells the office that drivers cannot undo a boarding", () => {
    const admin = read("admin-guide.md");
    expect(admin).not.toMatch(/Drivers can't undo these/i);
    expect(admin).not.toMatch(/only advances as the driver taps/i);
  });
});
