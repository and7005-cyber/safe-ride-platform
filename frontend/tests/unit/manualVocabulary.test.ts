import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  ADMIN_INCIDENT_LABEL,
  BUS_STATUS_LABEL,
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
