import { useSyncExternalStore } from "react";
import { api } from "@/lib/apiClient";

// The phone's school binding (hardening U10). A driver's PIN signs in only at
// the school this phone was set up for: the driver types the school's driver
// code once (or opens the office's link or QR, `/auth?school=<code>`), the
// app looks the code up and asks "Is this your school?", and stores the
// answer — the school's id and name, never the code itself. Every PIN sign-in
// sends that id.
//
// The binding lives in localStorage, so it survives sign-out and a closed
// tab; only "Change school" clears it. A regenerated driver code does not
// unbind a phone (the id is unchanged). iOS keeps separate storage for
// Safari and the installed app and clears both after a week unused, which is
// why the driver guide leads with "open the installed app". Where storage
// refuses the write (privacy modes, blocked site data, quota), the binding
// is kept in memory and lasts as long as the tab.

export interface DriverSchool {
  schoolId: string;
  name: string;
}

const KEY = "saferide-driver-school";
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

let cachedRaw: string | null = null;
let cachedValue: DriverSchool | null = null;

// The tab-only binding: set when storage refused the last confirmed school,
// cleared by a write storage accepted and by "Change school". While set it is
// this tab's newest answer, so it wins over anything storage still holds. One
// object per confirmation, so useSyncExternalStore sees a stable snapshot.
let memoryBinding: DriverSchool | null = null;

/** The phone's binding, or null (none, or a malformed stored value — which
 * then reads as "not set up"). Falls back to the tab-only binding when
 * storage refused the write. */
export function readDriverSchool(): DriverSchool | null {
  if (memoryBinding) return memoryBinding;
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(KEY);
  } catch {
    return null;
  }
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  cachedValue = null;
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.schoolId === "string" && parsed.schoolId) {
        cachedValue = {
          schoolId: parsed.schoolId,
          name: typeof parsed.name === "string" ? parsed.name : "",
        };
      }
    } catch {
      cachedValue = null;
    }
  }
  return cachedValue;
}

export function storeDriverSchool(binding: DriverSchool): void {
  const value = { schoolId: binding.schoolId, name: binding.name ?? "" };
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
    memoryBinding = null;
  } catch {
    // Storage refused (privacy modes, quota): the binding lasts as long as
    // the tab.
    memoryBinding = value;
  }
  notify();
}

/** "Change school": the only way a binding is cleared. */
export function clearDriverSchool(): void {
  memoryBinding = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing stored
  }
  notify();
}

export function subscribeDriverSchool(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** React subscription to this phone's binding. */
export function useDriverSchool(): DriverSchool | null {
  return useSyncExternalStore(subscribeDriverSchool, readDriverSchool, () => null);
}

// --- the driver code (D2) ----------------------------------------------------

/** Eight symbols of Crockford base32: no I, L, O or U. */
export const DRIVER_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
export const DRIVER_CODE_LENGTH = 8;

/** A typed driver code in its stored form: any case, hyphens and spaces
 * removed, O read as 0 and I or L as 1 — the server does the same. */
export function normaliseDriverCode(input: string): string {
  return (input ?? "")
    .replace(/[\s-]+/g, "")
    .toUpperCase()
    .replace(/O/g, "0")
    .replace(/[IL]/g, "1");
}

/** Whether a NORMALISED value can be a driver code at all. */
export function isDriverCode(code: string): boolean {
  return (
    code.length === DRIVER_CODE_LENGTH &&
    Array.from(code).every((ch) => DRIVER_CODE_ALPHABET.includes(ch))
  );
}

/** XXXX-XXXX, the way the office reads it out. */
export function formatDriverCode(code: string): string {
  const normalised = normaliseDriverCode(code);
  return normalised.length > 4 ? `${normalised.slice(0, 4)}-${normalised.slice(4)}` : normalised;
}

/** The public AAA-NNN school code (Settings, the sidebar) — a different code,
 * which a driver may type by mistake (DL5). */
export function looksLikeSchoolCode(input: string): boolean {
  return /^[A-Za-z]{3}\s*-?\s*[0-9]{3}$/.test((input ?? "").trim());
}

/** The `?school=` code of a sign-in link, normalised, or null. */
export function driverCodeFromSearch(search: string | URLSearchParams): string | null {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const raw = params.get("school");
  if (!raw || !raw.trim()) return null;
  return normaliseDriverCode(raw);
}

/** The link the Drivers page shares (and encodes in its QR). */
export function driverSignInLink(origin: string, code: string): string {
  return `${origin.replace(/\/$/, "")}/auth?school=${normaliseDriverCode(code)}`;
}

/** The public lookup: {schoolId, name} for a driver code, or an ApiError
 * (404 unknown or replaced code, 429 too many tries). */
export async function lookupDriverSchool(code: string): Promise<DriverSchool> {
  const normalised = normaliseDriverCode(code);
  const res = await api.get(`/api/auth/schools/${encodeURIComponent(normalised)}`);
  return { schoolId: String(res.schoolId), name: String(res.name ?? "") };
}

// --- a signed-in driver adopts the session's school (AE10) -------------------

interface SessionLike {
  activeSchoolId: string | null;
  signedInWithPin: boolean;
  memberships: { schoolId: string; schoolName: string | null; role: string }[];
}

/**
 * A driver already signed in on a phone with no binding (signed in before
 * Release 1b, or storage cleared) adopts the school of their session, so the
 * next PIN sign-in asks for nothing. Only for a driver session: a PIN
 * session, or an account whose only memberships are driver ones — a staff
 * member who also drives never has the sign-in page switched to PIN.
 * Returns the binding written, or null.
 */
export function adoptSessionSchool(user: SessionLike): DriverSchool | null {
  if (readDriverSchool()) return null;
  const drivers = user.memberships.filter((m) => m.role === "driver");
  if (drivers.length === 0) return null;
  const onlyDriver = user.memberships.every((m) => m.role === "driver");
  if (!user.signedInWithPin && !onlyDriver) return null;
  const pick =
    drivers.find((m) => m.schoolId === user.activeSchoolId) ??
    (drivers.length === 1 ? drivers[0] : null);
  if (!pick) return null;
  const binding = { schoolId: pick.schoolId, name: pick.schoolName ?? "" };
  storeDriverSchool(binding);
  return binding;
}
