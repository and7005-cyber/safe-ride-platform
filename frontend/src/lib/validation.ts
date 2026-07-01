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

// Two-parent contact invariant (R9–R10) -------------------------------------
// Mirrors backend `_clean_student`: every student needs a Parent 1 name, at
// least one phone across the two parent slots, and at least one email across
// the two slots. `parent_phone2` is Parent 2's phone (reused column).

const present = (v: string | null | undefined) => !!v && !!String(v).trim();

export interface ParentContactFields {
  parent_name?: string | null;
  parent_phone?: string | null;
  parent_phone2?: string | null;
  parent_email?: string | null;
  parent2_email?: string | null;
}

export interface ParentContactErrors {
  parentName: string | null;
  phone: string | null;
  email: string | null;
}

/** Field-level messages for the parent-contact invariant, all null when it holds. */
export function parentContactErrors(f: ParentContactFields): ParentContactErrors {
  return {
    parentName: present(f.parent_name) ? null : "Parent 1 name is required",
    phone:
      present(f.parent_phone) || present(f.parent_phone2)
        ? null
        : "At least one parent phone number is required",
    email:
      present(f.parent_email) || present(f.parent2_email)
        ? null
        : "At least one parent email is required",
  };
}

export interface BulkStudentRow extends ParentContactFields {
  name?: string | null;
  grade?: string | null;
  parent2_name?: string | null;
}

/**
 * Per-row bulk-upload validation mirroring the backend's `/api/students/bulk`
 * row checks (same messages, same "first failure wins" behaviour, plus the
 * phone/email format checks `_clean_student` would reject server-side).
 * Returns a "label: message" string, or null when the row is uploadable.
 */
export function bulkStudentRowError(row: BulkStudentRow, index: number): string | null {
  const label = present(row.name) ? String(row.name) : `row ${index + 1}`;
  if (!present(row.name) || !present(row.grade) || !present(row.parent_name)) {
    return `${label}: missing required field (name, grade, parent name)`;
  }
  if (!present(row.parent_phone) && !present(row.parent_phone2)) {
    return `${label}: at least one parent phone is required`;
  }
  if (!present(row.parent_email) && !present(row.parent2_email)) {
    return `${label}: at least one parent email is required`;
  }
  const formatChecks: Array<[string | null, string]> = [
    [phoneError(row.parent_phone), "parent 1 phone"],
    [phoneError(row.parent_phone2), "parent 2 phone"],
    [emailError(row.parent_email), "parent 1 email"],
    [emailError(row.parent2_email), "parent 2 email"],
  ];
  for (const [error, field] of formatChecks) {
    if (error) return `${label}: invalid ${field}`;
  }
  return null;
}
