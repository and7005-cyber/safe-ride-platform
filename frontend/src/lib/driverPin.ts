// Driver PIN rules (hardening U10, DL8). One rule set for the Drivers page's
// field, its Save button and its Generate button, so Generate never offers a
// PIN the server refuses. The server holds the same rules
// (backend/app/core/security.py: validate_pin, pin_is_too_easy,
// PIN_DENYLIST); a unit test compares the two denylists.
//
// A new or re-issued PIN is exactly six digits and not an easy guess. Sign-in
// still accepts four to six digits: drivers with an older, shorter PIN keep
// it until the office re-issues one.

export const PIN_LENGTH = 6;
export const SIGN_IN_PIN_MIN = 4;

/** Refused for a new PIN on top of the pattern rules below (mirrors the
 * server's PIN_DENYLIST). */
export const PIN_DENYLIST: readonly string[] = [
  "112233", "123321", "159753", "159357", "147258", "147852", "258369",
  "789456", "456123", "102030", "010203", "112211", "001122", "000123",
  "998877",
];

export const PIN_LENGTH_MESSAGE = "PIN must be 6 digits";
export const PIN_TOO_EASY_MESSAGE = "That PIN is too easy to guess";
export const PIN_IN_USE_MESSAGE = "That PIN is already in use at this school";

/** Repeated digits, an ascending or descending run (wrapping past 9), a two-
 * or three-digit pattern repeated, or a denylisted value. */
export function pinIsTooEasy(pin: string): boolean {
  const digits = Array.from(pin, (ch) => Number(ch));
  const steps = new Set<number>();
  for (let i = 1; i < digits.length; i++) steps.add((digits[i]! - digits[i - 1]! + 10) % 10);
  if (steps.size === 1) {
    const step = [...steps][0]!;
    if (step === 0 || step === 1 || step === 9) return true;
  }
  for (const period of [2, 3]) {
    if (pin === pin.slice(0, period).repeat(pin.length / period)) return true;
  }
  return PIN_DENYLIST.includes(pin);
}

/** Why the Drivers page may not save `pin` as a new PIN, or null when it may.
 * An empty value is not a problem here: on create it means "no PIN yet", on
 * edit "keep the current PIN". */
export function newPinProblem(pin: string): string | null {
  if (pin === "") return null;
  if (!/^[0-9]{6}$/.test(pin)) return PIN_LENGTH_MESSAGE;
  if (pinIsTooEasy(pin)) return PIN_TOO_EASY_MESSAGE;
  return null;
}

type RandomFill = (array: Uint32Array) => Uint32Array;

const defaultFill: RandomFill = (array) => crypto.getRandomValues(array);

// The largest multiple of 10^6 below 2^32: values at or above it are drawn
// again, so every six-digit string is equally likely (no modulo bias).
const UNBIASED_LIMIT = Math.floor(2 ** 32 / 1_000_000) * 1_000_000;

/** Six random digits from the browser's cryptographic generator, drawn again
 * until the PIN rules accept them. */
export function generatePin(fill: RandomFill = defaultFill): string {
  const buffer = new Uint32Array(1);
  for (;;) {
    fill(buffer);
    const value = buffer[0]!;
    if (value >= UNBIASED_LIMIT) continue;
    const pin = String(value % 1_000_000).padStart(PIN_LENGTH, "0");
    if (!pinIsTooEasy(pin)) return pin;
  }
}
