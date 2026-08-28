import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/apiClient";
import { POLL_ADMIN } from "@/lib/queries";

// Provider console data hooks (U13). The console lives OUTSIDE any school:
// keys are ["provider", ...] — never school-prefixed — and apiClient exempts
// /api/provider from the X-School-Id header, so a live step-in in the same
// tab cannot leak its school onto console calls.

/** One row of GET /api/provider/schools — counts and setup state only,
 * never a roster (R24): no student field of any kind exists here. */
export interface ProviderSchool {
  schoolId: string;
  name: string;
  code: string;
  setupState: "ready" | "setup" | string;
  students: number;
  buses: number;
  drivers: number;
  runsToday: number;
  lastStaffWrite: string | null;
}

export interface ProviderAccount {
  userId: string;
  email: string;
  fullName: string | null;
  totpEnrolled: boolean;
  createdAt: string;
}

export interface ProviderAuditRow {
  id: string;
  action: string;
  actorId: string | null;
  actorName: string | null;
  actorEmail: string | null;
  actorKind: string | null;
  schoolId: string | null;
  supportSessionId: string | null;
  resourceType: string | null;
  resourceId: string | null;
  detail: unknown;
  createdAt: string;
}

export function useProviderSchools() {
  return useQuery<ProviderSchool[]>({
    queryKey: ["provider", "schools"],
    queryFn: ({ signal }) => api.get("/api/provider/schools", undefined, { signal }),
    // The health board mirrors the admin cadence so runsToday stays honest.
    refetchInterval: POLL_ADMIN,
  });
}

export function useProviderAccounts() {
  return useQuery<ProviderAccount[]>({
    queryKey: ["provider", "accounts"],
    queryFn: ({ signal }) => api.get("/api/provider/accounts", undefined, { signal }),
  });
}

export function useProviderAudit(filters: {
  schoolId?: string | null;
  supportSessionId?: string | null;
}) {
  const schoolId = filters.schoolId || null;
  const supportSessionId = filters.supportSessionId || null;
  return useQuery<ProviderAuditRow[]>({
    queryKey: ["provider", "audit", schoolId, supportSessionId],
    queryFn: ({ signal }) =>
      api.get(
        "/api/provider/audit",
        { school_id: schoolId, support_session_id: supportSessionId },
        { signal },
      ),
  });
}

// --- step-up (fresh-code) client mirror --------------------------------------

/** Server rule (U10): step-in, account create/remove and TOTP reset need a
 * code verified within the last fifteen minutes on the CALLING session. */
export const STEP_UP_FRESH_MINUTES = 15;

/**
 * Whether a step-up action should ask for a code UP FRONT: the session has
 * never verified one, the timestamp is unreadable, or it is older than the
 * fifteen-minute window (U13). Purely a UX pre-judgement — the server's
 * `totp-step-up-required` answer stays the authority and every caller
 * retries with a code when it lands (clock skew, races).
 */
export function stepUpCodeNeeded(
  totpVerifiedAt: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (!totpVerifiedAt) return true;
  const verified = new Date(totpVerifiedAt).getTime();
  if (Number.isNaN(verified)) return true;
  return now.getTime() - verified > STEP_UP_FRESH_MINUTES * 60_000;
}

export type StepUpOutcome<T> = { cancelled: true } | { cancelled: false; result: T };

/**
 * Run a step-up-gated call (U13): asks for a code up front when the session's
 * last one is stale, and ALWAYS retries once with a fresh code when the
 * server answers `totp-step-up-required`. `promptCode` resolves null when the
 * person backs out — the action is then not performed.
 */
export async function runWithStepUp<T>(
  action: (code?: string) => Promise<T>,
  opts: {
    totpVerifiedAt: string | null | undefined;
    promptCode: (message?: string) => Promise<string | null>;
  },
): Promise<StepUpOutcome<T>> {
  let code: string | undefined;
  if (stepUpCodeNeeded(opts.totpVerifiedAt)) {
    const entered = await opts.promptCode();
    if (entered === null) return { cancelled: true };
    code = entered;
  }
  try {
    return { cancelled: false, result: await action(code) };
  } catch (err) {
    if (err instanceof ApiError && err.code === "totp-step-up-required") {
      const entered = await opts.promptCode(err.message);
      if (entered === null) return { cancelled: true };
      return { cancelled: false, result: await action(entered) };
    }
    throw err;
  }
}
