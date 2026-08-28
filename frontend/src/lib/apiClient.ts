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

// Registered by the AuthProvider (U12): a 404 on a call that carried
// X-School-Id MAY mean the caller's membership at that school was revoked
// mid-session. The handler re-checks /me exactly once at a time and, when the
// school is really gone, clears the store so ProtectedRoute re-routes —
// centralised here so no page ever turns membership loss into a toast storm.
let schoolScoped404Handler: ((schoolId: string) => void) | null = null;
export function onSchoolScoped404(handler: ((schoolId: string) => void) | null) {
  schoolScoped404Handler = handler;
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestExtras {
  /** TanStack Query passes its own signal so an unmounted/cancelled query
   * aborts the fetch — required for the school switch to cut off the old
   * school's in-flight responses. */
  signal?: AbortSignal;
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

async function request(
  method: string,
  path: string,
  opts: { query?: Query; body?: unknown; signal?: AbortSignal } = {},
) {
  const headers: Record<string, string> = {};
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

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(extractDetailMessage(data, response.status));
  }
  return data;
}

export const api = {
  get: (path: string, query?: Query, extras?: RequestExtras) =>
    request("GET", path, { query, signal: extras?.signal }),
  post: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("POST", path, { body: body ?? {}, signal: extras?.signal }),
  put: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("PUT", path, { body: body ?? {}, signal: extras?.signal }),
  // DELETE takes an optional JSON body (Cancel-a-Ride withdrawal sends
  // {student_id, scope}); unlike post/put it is NOT defaulted to {} so
  // existing body-less deletes keep sending no body and no Content-Type.
  del: (path: string, body?: unknown, extras?: RequestExtras) =>
    request("DELETE", path, { body, signal: extras?.signal }),
};
