import { getActiveSchoolId } from "@/lib/school";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:9001";

const TOKEN_KEY = "saferide-token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// Registered by the AuthProvider so a global 401 can clear state + redirect.
let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler;
}

/**
 * A failed request, with the server's machine `code` when it sent one (U13).
 *
 * Second-factor refusals carry `detail: {code, message}` — `preauth-voided`,
 * `totp-step-up-required`, `totp-enrolment-required` — and the client must
 * branch on the code, never on message text. `message` stays the human
 * sentence, so every existing `(err as Error).message` call keeps working.
 */
export class ApiError extends Error {
  status: number;
  code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// Registered by the AuthProvider (U12): a 404 on a call that carried
// X-School-Id MAY mean the caller's membership at that school was revoked
// mid-session. The handler re-checks /me exactly once at a time and, when the
// school is really gone, clears the store so ProtectedRoute re-routes —
// centralised here so no page ever turns membership loss into a toast storm.
let schoolScoped404Handler: ((schoolId: string) => void) | null = null;
export function onSchoolScoped404(handler: ((schoolId: string) => void) | null) {
  schoolScoped404Handler = handler;
}

// Registered by the AuthProvider (U13): a 403 on a call that carried
// X-School-Id is, for a stepped-in provider, the signature of the support
// session ending under the tab (step-out elsewhere, the four-hour limit, a
// supersede) — the handler re-checks /me and routes back to the provider
// console. Staff 403s just get a fresh /me.
let schoolScoped403Handler: ((schoolId: string) => void) | null = null;
export function onSchoolScoped403(handler: ((schoolId: string) => void) | null) {
  schoolScoped403Handler = handler;
}

// Registered by the AuthProvider (U13): any 409 whose detail.code is
// `totp-enrolment-required` (a peer reset the provider's factor mid-session)
// re-checks /me so ProtectedRoute can swap in the enrolment screen.
let totpEnrolmentRequiredHandler: (() => void) | null = null;
export function onTotpEnrolmentRequired(handler: (() => void) | null) {
  totpEnrolmentRequiredHandler = handler;
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestExtras {
  /** TanStack Query passes its own signal so an unmounted/cancelled query
   * aborts the fetch — required for the school switch to cut off the old
   * school's in-flight responses. */
  signal?: AbortSignal;
  /** Per-call request headers (GPS plan U6): the driver action envelope
   * sends its `Idempotency-Key` here. Merged after the standard headers, so a
   * caller can never override Authorization or the school scope by accident
   * — those two are set last. */
  headers?: Record<string, string>;
}

function buildUrl(path: string, query?: Query) {
  const url = new URL(path, API_BASE_URL);
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return url.toString();
}

// Credential endpoints: a 401 here means "wrong credentials", not an expired
// session — surface the server's message instead of the sign-out flow.
const CREDENTIAL_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/signup",
  "/api/auth/pin-login",
  "/api/auth/forgot-password",
  "/api/auth/reset-password",
  // The provider code step (U13): a 401 here is a wrong/voided code or a dead
  // pre-auth token — there is no session to expire yet.
  "/api/auth/totp",
]);

/**
 * Whether a path belongs to the school-scoped staff surface (U12).
 *
 * The auth surface never takes a school (offers are explicitly "the calling
 * account's", change-password/me/logout are account-level), driver routes
 * REJECT the header with 403 by design (so a staff+driver hybrid account can
 * still drive), the parent portal resolves its own school set server-side,
 * and push registration is per user/device. Everything else on the admin
 * console works inside the active school and sends the header.
 */
export function sendsSchoolHeader(path: string): boolean {
  if (path.startsWith("/api/auth/")) return false;
  if (path.startsWith("/api/runs/driver")) return false;
  if (path === "/api/incidents/driver") return false;
  if (path.startsWith("/api/parent-portal")) return false;
  if (path.startsWith("/api/push")) return false;
  // The provider console lives OUTSIDE any school (U13): its routes ignore
  // the header by design, and a stepped-in provider's tab store must never
  // leak the support school onto console calls (step-out included).
  if (path.startsWith("/api/provider")) return false;
  return true;
}

function extractDetailMessage(data: any, status: number): string {
  if (data && typeof data.detail === "string") return data.detail;
  // Structured 409s (e.g. {detail: {code: "password-change-required",
  // message: ...}}) carry their human text in detail.message.
  if (data && data.detail && typeof data.detail.message === "string") {
    return data.detail.message;
  }
  return `Request failed with status ${status}`;
}

function extractDetailCode(data: any): string | null {
  const code = data?.detail?.code;
  return typeof code === "string" ? code : null;
}

async function request(
  method: string,
  path: string,
  opts: { query?: Query; body?: unknown; signal?: AbortSignal; headers?: Record<string, string> } = {},
) {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  // Active-school scope (U12): attached from the per-tab store, which is only
  // ever populated on the staff surface — see lib/school.ts.
  const schoolId = getActiveSchoolId();
  const scoped = Boolean(schoolId) && sendsSchoolHeader(path);
  if (schoolId && scoped) headers["X-School-Id"] = schoolId;

  const response = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });

  if (response.status === 401 && !CREDENTIAL_PATHS.has(path)) {
    setToken(null);
    unauthorizedHandler?.();
    throw new Error("Your session has expired. Please sign in again.");
  }

  if (response.status === 404 && scoped && schoolId) {
    // Might be a plain missing row — or the whole school vanishing for this
    // caller (membership revoked). The registered handler re-checks /me and
    // decides; this request still fails normally either way.
    schoolScoped404Handler?.(schoolId);
  }

  if (response.status === 403 && scoped && schoolId) {
    // For a stepped-in provider this is "the support session ended" (U13);
    // the handler re-checks /me and decides. The request still fails.
    schoolScoped403Handler?.(schoolId);
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const code = extractDetailCode(data);
    if (response.status === 409 && code === "totp-enrolment-required") {
      // A peer reset this provider's second factor mid-session (U13): every
      // non-exempt route answers this until re-enrolment — refresh /me so
      // the enrolment interstitial takes over.
      totpEnrolmentRequiredHandler?.();
    }
    throw new ApiError(extractDetailMessage(data, response.status), response.status, code);
  }
  return data;
}

export const api = {
  get: (path: string, query?: Query, extras?: RequestExtras) =>
    request("GET", path, { query, signal: extras?.signal, headers: extras?.headers }),
  post: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("POST", path, { body: body ?? {}, signal: extras?.signal, headers: extras?.headers }),
  put: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("PUT", path, { body: body ?? {}, signal: extras?.signal, headers: extras?.headers }),
  // DELETE takes an optional JSON body (Cancel-a-Ride withdrawal sends
  // {student_id, scope}); unlike post/put it is NOT defaulted to {} so
  // existing body-less deletes keep sending no body and no Content-Type.
  del: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("DELETE", path, { body, signal: extras?.signal, headers: extras?.headers }),
};
