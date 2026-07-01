import { describe, expect, it } from "vitest";
import {
  bulkStudentRowError,
  emailError,
  isValidEmail,
  normalizeKenyanPhone,
  parentContactErrors,
  phoneError,
} from "@/lib/validation";

describe("normalizeKenyanPhone", () => {
  it("normalises valid mobiles to E.164", () => {
    expect(normalizeKenyanPhone("0712345678")).toBe("+254712345678");
    expect(normalizeKenyanPhone("0112345678")).toBe("+254112345678");
    expect(normalizeKenyanPhone("+254712345678")).toBe("+254712345678");
    expect(normalizeKenyanPhone("254 712 345 678")).toBe("+254712345678");
    expect(normalizeKenyanPhone("0712-345-678")).toBe("+254712345678");
  });

  it("rejects non-mobiles", () => {
    for (const bad of ["", "123", "0812345678", "+1234567890", "abc", "0204440000"]) {
      expect(normalizeKenyanPhone(bad)).toBeNull();
    }
  });

  it("accepts landlines only when allowed", () => {
    expect(normalizeKenyanPhone("0204440000", { allowLandline: true })).toBe("+254204440000");
  });
});

describe("phoneError", () => {
  it("is null for empty optional and valid input", () => {
    expect(phoneError("")).toBeNull();
    expect(phoneError("0712345678")).toBeNull();
  });
  it("flags required-but-empty and invalid", () => {
    expect(phoneError("", { required: true })).toBeTruthy();
    expect(phoneError("nope")).toBeTruthy();
  });
});

describe("email", () => {
  it("validates format", () => {
    expect(isValidEmail("a@b.com")).toBe(true);
    expect(isValidEmail("nope")).toBe(false);
    expect(emailError("a@b.com")).toBeNull();
    expect(emailError("nope")).toBeTruthy();
    expect(emailError("", true)).toBeTruthy();
    expect(emailError("")).toBeNull();
  });
});

// Mirrors the backend two-parent contact invariant (R9–R10): parent 1 name,
// ≥1 phone across (parent_phone, parent_phone2), ≥1 email across
// (parent_email, parent2_email).
describe("parentContactErrors", () => {
  it("passes when parent 1 carries all contacts", () => {
    expect(
      parentContactErrors({
        parent_name: "Jane",
        parent_phone: "0712345678",
        parent_email: "jane@example.com",
      }),
    ).toEqual({ parentName: null, phone: null, email: null });
  });

  it("accepts cross-slot satisfaction (parent 1 phone + parent 2 email)", () => {
    const errs = parentContactErrors({
      parent_name: "Jane",
      parent_phone: "0712345678",
      parent2_email: "peter@example.com",
    });
    expect(errs.phone).toBeNull();
    expect(errs.email).toBeNull();
  });

  it("flags a missing parent 1 name even when parent 2 is filled", () => {
    const errs = parentContactErrors({
      parent_name: "  ",
      parent_phone2: "0712345678",
      parent2_email: "peter@example.com",
    });
    expect(errs.parentName).toBe("Parent 1 name is required");
  });

  it("flags missing phone/email only when both slots are empty", () => {
    const errs = parentContactErrors({ parent_name: "Jane" });
    expect(errs.phone).toBe("At least one parent phone number is required");
    expect(errs.email).toBe("At least one parent email is required");
    const phone2Only = parentContactErrors({ parent_name: "Jane", parent_phone2: "0712345678" });
    expect(phone2Only.phone).toBeNull();
  });
});

describe("bulkStudentRowError", () => {
  const valid = {
    name: "Asha",
    grade: "Grade 3",
    parent_name: "Jane",
    parent_phone: "0712345678",
    parent_email: "jane@example.com",
  };

  it("accepts a valid row and cross-slot contacts", () => {
    expect(bulkStudentRowError(valid, 0)).toBeNull();
    expect(
      bulkStudentRowError(
        { ...valid, parent_phone: null, parent_phone2: "0712345678" },
        0,
      ),
    ).toBeNull();
    expect(
      bulkStudentRowError(
        { ...valid, parent_email: null, parent2_email: "peter@example.com" },
        0,
      ),
    ).toBeNull();
  });

  it("mirrors backend messages, labelled by name or row number", () => {
    expect(bulkStudentRowError({ ...valid, grade: null }, 0)).toBe(
      "Asha: missing required field (name, grade, parent name)",
    );
    expect(bulkStudentRowError({ ...valid, name: "" }, 2)).toBe(
      "row 3: missing required field (name, grade, parent name)",
    );
    expect(
      bulkStudentRowError({ ...valid, parent_phone: null, parent_phone2: "" }, 0),
    ).toBe("Asha: at least one parent phone is required");
    expect(
      bulkStudentRowError({ ...valid, parent_email: null, parent2_email: null }, 0),
    ).toBe("Asha: at least one parent email is required");
  });

  it("flags malformed phones and emails the backend would reject", () => {
    expect(bulkStudentRowError({ ...valid, parent_phone: "12345" }, 0)).toBe(
      "Asha: invalid parent 1 phone",
    );
    expect(
      bulkStudentRowError({ ...valid, parent_phone2: "nope" }, 0),
    ).toBe("Asha: invalid parent 2 phone");
    expect(bulkStudentRowError({ ...valid, parent2_email: "not-an-email" }, 0)).toBe(
      "Asha: invalid parent 2 email",
    );
  });
});
