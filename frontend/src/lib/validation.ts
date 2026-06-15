// Client-side Kenyan phone + email validation (#13). The backend is the source
// of truth and re-validates/normalises; these helpers surface friendly inline
// errors and block obviously-bad submits.

const PHONE_CLEAN = /[\s\-()./]+/g;
const KE_MOBILE = /^(?:7|1)\d{8}$/;
const KE_LANDLINE = /^[2-9]\d{6,8}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;

export function normalizeKenyanPhone(
  raw: string | null | undefined,
  opts: { allowLandline?: boolean } = {},
): string | null {
  if (raw == null) return null;
  const cleaned = String(raw).trim().replace(PHONE_CLEAN, "");
  if (!cleaned) return null;
  let national: string;
  if (cleaned.startsWith("+254")) national = cleaned.slice(4);
  else if (cleaned.startsWith("254")) national = cleaned.slice(3);
  else if (cleaned.startsWith("0")) national = cleaned.slice(1);
  else national = cleaned;
  if (!/^\d+$/.test(national)) return null;
  if (KE_MOBILE.test(national)) return `+254${national}`;
  if (opts.allowLandline && KE_LANDLINE.test(national)) return `+254${national}`;
  return null;
}

export function isValidEmail(raw: string | null | undefined): boolean {
  return !!raw && EMAIL.test(String(raw).trim());
}

/** Returns an error message for a phone field, or null when valid/empty. */
export function phoneError(
  raw: string | null | undefined,
  opts: { allowLandline?: boolean; required?: boolean } = {},
): string | null {
  if (!raw || !String(raw).trim()) return opts.required ? "Phone number is required" : null;
  return normalizeKenyanPhone(raw, opts)
    ? null
    : "Enter a valid Kenyan mobile number (e.g. 0712 345 678 or +254712345678)";
}

/** Returns an error message for an email field, or null when valid/empty. */
export function emailError(raw: string | null | undefined, required = false): string | null {
  if (!raw || !String(raw).trim()) return required ? "Email is required" : null;
  return isValidEmail(raw) ? null : "Enter a valid email address";
}
