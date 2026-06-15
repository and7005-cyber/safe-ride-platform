import { describe, expect, it } from "vitest";
import {
  emailError,
  isValidEmail,
  normalizeKenyanPhone,
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
