// The Drivers page's PIN rules (hardening U10, DL8): a new or re-issued PIN is
// six digits and not an easy guess, and Generate never offers one the server
// refuses. The server's denylist is read from its source so the two lists
// cannot drift apart.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  PIN_DENYLIST,
  PIN_LENGTH_MESSAGE,
  PIN_TOO_EASY_MESSAGE,
  generatePin,
  newPinProblem,
  pinIsTooEasy,
} from "@/lib/driverPin";

const SECURITY_PY = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../backend/app/core/security.py",
);

describe("the new-PIN rules", () => {
  it.each(["48291", "0322", "4829150", "48291a", " 48291"])("%j must be six digits", (pin) => {
    expect(newPinProblem(pin)).toBe(PIN_LENGTH_MESSAGE);
  });

  it.each([
    "111111", "000000", "123456", "234567", "567890", "890123",
    "654321", "987654", "109876", "121212", "696969", "123123", "520520",
    "112233", "123321", "159753", "147258",
  ])("%j is too easy to guess", (pin) => {
    expect(pinIsTooEasy(pin)).toBe(true);
    expect(newPinProblem(pin)).toBe(PIN_TOO_EASY_MESSAGE);
  });

  it.each(["482915", "730184", "102938", "120000", "135790"])("%j is accepted", (pin) => {
    expect(newPinProblem(pin)).toBeNull();
  });

  it("treats an empty field as no PIN (create) or keep the current one (edit)", () => {
    expect(newPinProblem("")).toBeNull();
  });

  it("refuses the same denylist as the server", () => {
    const source = readFileSync(SECURITY_PY, "utf8");
    const block = source.match(/PIN_DENYLIST = \(([\s\S]*?)\)/);
    expect(block, "PIN_DENYLIST not found in security.py").toBeTruthy();
    const serverList = [...block![1]!.matchAll(/"(\d{6})"/g)].map((m) => m[1]);
    expect([...PIN_DENYLIST].sort()).toEqual([...serverList].sort());
  });
});

describe("Generate", () => {
  it("draws six digits until the rules accept them", () => {
    // 123456 (a run), then 111111 (repeated), then a value past the unbiased
    // limit, then an ordinary PIN.
    const draws = [1_000_000 * 7 + 123456, 111111, 4_294_967_295, 482915];
    const fill = (array: Uint32Array) => {
      array[0] = draws.shift()!;
      return array;
    };
    expect(generatePin(fill)).toBe("482915");
    expect(draws).toEqual([]);
  });

  it("keeps leading zeros", () => {
    const fill = (array: Uint32Array) => {
      array[0] = 4821;
      return array;
    };
    expect(generatePin(fill)).toBe("004821");
  });

  it("never emits a refused PIN from the real generator", () => {
    for (let i = 0; i < 2000; i++) {
      const pin = generatePin();
      expect(pin).toMatch(/^[0-9]{6}$/);
      expect(newPinProblem(pin)).toBeNull();
    }
  });
});
