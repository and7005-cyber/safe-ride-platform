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

type Query = Record<string, string | number | boolean | null | undefined>;

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

async function request(method: string, path: string, opts: { query?: Query; body?: unknown } = {}) {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });

  if (response.status === 401 && !CREDENTIAL_PATHS.has(path)) {
    setToken(null);
    unauthorizedHandler?.();
    throw new Error("Your session has expired. Please sign in again.");
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message =
      data && typeof data.detail === "string"
        ? data.detail
        : `Request failed with status ${response.status}`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  get: (path: string, query?: Query) => request("GET", path, { query }),
  post: (path: string, body?: unknown) => request("POST", path, { body: body ?? {} }),
  put: (path: string, body?: unknown) => request("PUT", path, { body: body ?? {} }),
  del: (path: string) => request("DELETE", path),
};
