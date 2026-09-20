import { useSyncExternalStore } from "react";

// Per-tab active school store (U12/R4). The value lives in sessionStorage so
// each browser tab carries its own school — two tabs on two schools never
// share or overwrite each other's scope — and a duplicated tab starts from the
// original's school (sessionStorage copy semantics), which is the intended
// "carry the school with the tab" behaviour.
//
// The store is only ever populated for a session with school-console access
// (U12, widened by U13): a staff membership — ProtectedRoute auto-selects or
// the person chooses/switches — OR a provider's live step-in, where the store
// mirrors the support session exactly. AuthProvider clears it for every other
// session and on sign-out. That single rule is what keeps the driver surface
// from ever sending X-School-Id (driver routes 403 the header by design) —
// see apiClient.ts, which reads this store on every request.

const SCHOOL_KEY = "saferide-school";

const listeners = new Set<() => void>();

function safeRead(): string | null {
  try {
    return sessionStorage.getItem(SCHOOL_KEY);
  } catch {
    // Storage can be unavailable (privacy modes); behave as "no school".
    return null;
  }
}

export function getActiveSchoolId(): string | null {
  return safeRead();
}

export function setActiveSchoolId(schoolId: string | null): void {
  try {
    if (schoolId) sessionStorage.setItem(SCHOOL_KEY, schoolId);
    else sessionStorage.removeItem(SCHOOL_KEY);
  } catch {
    // Ignore storage failures; subscribers still see the attempt below so the
    // UI stays consistent within this tab's lifetime.
  }
  listeners.forEach((listener) => listener());
}

export function clearActiveSchoolId(): void {
  setActiveSchoolId(null);
}

export function subscribeActiveSchool(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** React subscription to the per-tab active school id (null when unset). */
export function useActiveSchoolId(): string | null {
  // Strings are compared by value in useSyncExternalStore, so reading the
  // storage directly is a stable snapshot.
  return useSyncExternalStore(subscribeActiveSchool, getActiveSchoolId, () => null);
}
