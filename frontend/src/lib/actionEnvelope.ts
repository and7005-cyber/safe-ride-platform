// The idempotent driver action envelope (GPS plan U6: R3, R33 client half).
//
// One tap, one key. At the first attempt of an action the envelope is minted:
// a UUID, the fix captured right then, the action's fields and the device id.
// It is kept in localStorage until the server has answered for it, and a
// retry of the same action re-sends it unchanged — same key, same fix, same
// capture time — so the position evidence stays bound to when the driver
// acted, not to when the network delivered it (R3), and the server can replay
// instead of repeating (R33). A different action, or the same one after the
// server has acknowledged, is a new tap and mints a new key.
//
// Nothing queues: a tap that cannot reach the server fails in front of the
// driver, and the driver's next tap of that action is the retry.
//
// The server's two conflicts: `idempotency-in-flight` (the first attempt is
// still executing) is retried after a short delay, bounded, with the same
// envelope; `idempotency-mismatch` (a different payload under this key) drops
// the envelope and surfaces the error. Neither ever re-mints.

import { api, ApiError } from "@/lib/apiClient";
import { fixCapture, type FixPayload } from "@/lib/geo/fixCapture";

export const ENVELOPE_STORAGE_KEY = "saferide-action-envelopes";
export const DEVICE_ID_KEY = "saferide-device-id";
export const IDEMPOTENCY_HEADER = "Idempotency-Key";
export const IN_FLIGHT_CODE = "idempotency-in-flight";
export const MISMATCH_CODE = "idempotency-mismatch";
export const IN_FLIGHT_RETRY_DELAY_MS = 750;
export const IN_FLIGHT_MAX_RETRIES = 3;
/** An envelope nobody has retried for this long is forgotten. */
export const ENVELOPE_TTL_MS = 6 * 60 * 60 * 1000;

/** The seven action routes that carry the envelope (not the prompt routes,
 * not reverse, not reads). */
export const ENVELOPE_PATHS: ReadonlySet<string> = new Set([
  "/api/runs/driver/start",
  "/api/runs/driver/arrive",
  "/api/runs/driver/boarding",
  "/api/runs/driver/dropoff",
  "/api/runs/driver/absent",
  "/api/runs/driver/handover",
  "/api/runs/driver/end",
]);

export interface ActionEnvelope {
  key: string;
  path: string;
  /** The body exactly as sent: the action's fields, `fix`, `device_id`. */
  body: Record<string, unknown>;
  /** The run this tap belongs to; null for Start Run. */
  run_id: string | null;
  /** Client clock at minting, for the TTL. */
  created_at: number;
}

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isUuidV4(value: unknown): value is string {
  return typeof value === "string" && UUID_V4.test(value);
}

/** A v4 UUID. `crypto.randomUUID` needs a secure context, which a phone
 * browsing a LAN dev server does not have; `getRandomValues` does not. */
export function randomUuid(cryptoObj: Crypto | undefined = globalThis.crypto): string {
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    try {
      return cryptoObj.randomUUID();
    } catch {
      // Insecure context: fall through.
    }
  }
  const bytes = new Uint8Array(16);
  if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
    cryptoObj.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

let memoryDeviceId: string | null = null;

/** A stable, client-minted device id (diagnostic only, R28). Kept in
 * localStorage; in memory for the session where storage is unavailable. */
export function getDeviceId(storage: Storage | null = safeStorage()): string {
  try {
    const stored = storage?.getItem(DEVICE_ID_KEY);
    if (isUuidV4(stored)) return stored;
  } catch {
    // Storage refused the read.
  }
  if (!memoryDeviceId) memoryDeviceId = randomUuid();
  try {
    storage?.setItem(DEVICE_ID_KEY, memoryDeviceId);
  } catch {
    // Storage refused the write; the session id still serves.
  }
  return memoryDeviceId;
}

/** Stable JSON: keys sorted, so the same action fingerprints the same way
 * whatever the caller's property order. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** What makes two taps "the same action": the route, the run and the
 * action's own fields — never the fix or the key. */
export function actionFingerprint(
  path: string,
  runId: string | null,
  action: Record<string, unknown>,
): string {
  return canonical({ path, run_id: runId, action });
}

export function isInFlightConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === IN_FLIGHT_CODE;
}

export function isMismatchConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === MISMATCH_CODE;
}

/** Whether the server may not have seen (or may still act on) the envelope,
 * so a retry must carry it unchanged: the network failed, the server fell
 * over, or the first attempt is still in flight. Anything the server
 * answered for — a business refusal, a mismatch, a 401 — is acknowledged. */
export function isRetryableFailure(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500 || isInFlightConflict(error);
  // fetch rejects a network failure with a TypeError; an aborted request is
  // a DOMException. Anything else (the session-expiry Error, a parse error)
  // is a definite answer.
  return error instanceof TypeError || (typeof DOMException !== "undefined" && error instanceof DOMException);
}

export interface ActionEnvelopesDeps {
  storage: Storage | null;
  post: (path: string, body: unknown, headers: Record<string, string>) => Promise<any>;
  uuid: () => string;
  now: () => number;
  sleep: (ms: number) => Promise<void>;
  deviceId: () => string;
}

export interface MintOptions {
  runId: string | null;
  /** Called only when a new envelope is minted — a reused envelope keeps the
   * fix frozen at its first attempt (R3). */
  fix: () => Promise<FixPayload> | FixPayload;
}

export class ActionEnvelopes {
  private pendingByFingerprint = new Map<string, ActionEnvelope>();
  private loaded = false;
  private sentListeners = new Set<(path: string) => void>();

  constructor(private readonly deps: ActionEnvelopesDeps) {}

  /** Called after the server accepted an envelope, with its path. The
   * ping stream listens (GPS plan U14): a tapped action re-binds the run to
   * this session on the server, so pings paused on `session-mismatch`
   * resume on the next one. Returns the unsubscribe. */
  onSent(listener: (path: string) => void): () => void {
    this.sentListeners.add(listener);
    return () => {
      this.sentListeners.delete(listener);
    };
  }

  /** The pending envelope for this exact action, or a fresh one. */
  async mint(path: string, action: Record<string, unknown>, opts: MintOptions): Promise<ActionEnvelope> {
    this.load();
    const fingerprint = actionFingerprint(path, opts.runId, action);
    const pending = this.pendingByFingerprint.get(fingerprint);
    if (pending && this.deps.now() - pending.created_at < ENVELOPE_TTL_MS) return pending;
    const fix = await opts.fix();
    const envelope: ActionEnvelope = {
      key: this.deps.uuid(),
      path,
      body: { ...action, fix, device_id: this.deps.deviceId() },
      run_id: opts.runId,
      created_at: this.deps.now(),
    };
    this.pendingByFingerprint.set(fingerprint, envelope);
    this.persist();
    return envelope;
  }

  /** POST the envelope with its key. Resolves with the server's result and
   * forgets the envelope; on a failure the server may still act on, the
   * envelope stays pending for the next tap of the same action. */
  async send(envelope: ActionEnvelope): Promise<any> {
    for (let attempt = 0; ; attempt++) {
      try {
        const result = await this.deps.post(envelope.path, envelope.body, {
          [IDEMPOTENCY_HEADER]: envelope.key,
        });
        this.forget(envelope.key);
        for (const listener of this.sentListeners) listener(envelope.path);
        return result;
      } catch (error) {
        if (isInFlightConflict(error) && attempt < IN_FLIGHT_MAX_RETRIES) {
          await this.deps.sleep(IN_FLIGHT_RETRY_DELAY_MS);
          continue;
        }
        if (!isRetryableFailure(error)) this.forget(envelope.key);
        throw error;
      }
    }
  }

  /** Drop what no longer belongs: envelopes of another run (a Start Run
   * envelope has none, so it goes once a run is active), and stale ones. */
  prune(activeRunId: string | null): void {
    this.load();
    const now = this.deps.now();
    let changed = false;
    for (const [fingerprint, envelope] of this.pendingByFingerprint) {
      const stale = now - envelope.created_at >= ENVELOPE_TTL_MS;
      if (envelope.run_id !== activeRunId || stale) {
        this.pendingByFingerprint.delete(fingerprint);
        changed = true;
      }
    }
    if (changed) this.persist();
  }

  forget(key: string): void {
    this.load();
    for (const [fingerprint, envelope] of this.pendingByFingerprint) {
      if (envelope.key === key) {
        this.pendingByFingerprint.delete(fingerprint);
        this.persist();
        return;
      }
    }
  }

  pending(): ActionEnvelope[] {
    this.load();
    return [...this.pendingByFingerprint.values()];
  }

  private load(): void {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const raw = this.deps.storage?.getItem(ENVELOPE_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return;
      for (const [fingerprint, value] of Object.entries(parsed as Record<string, unknown>)) {
        if (isEnvelope(value)) this.pendingByFingerprint.set(fingerprint, value);
      }
    } catch {
      // Unreadable: start empty. Nothing is lost that a retry could not
      // re-mint, and a fresh key is always safe on the server.
    }
  }

  private persist(): void {
    try {
      if (this.pendingByFingerprint.size === 0) {
        this.deps.storage?.removeItem(ENVELOPE_STORAGE_KEY);
        return;
      }
      this.deps.storage?.setItem(
        ENVELOPE_STORAGE_KEY,
        JSON.stringify(Object.fromEntries(this.pendingByFingerprint)),
      );
    } catch {
      // Quota or private mode: the in-memory copy still covers this session.
    }
  }
}

function isEnvelope(value: unknown): value is ActionEnvelope {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    isUuidV4(v.key)
    && typeof v.path === "string"
    && v.body != null && typeof v.body === "object"
    && (v.run_id === null || typeof v.run_id === "string")
    && typeof v.created_at === "number"
  );
}

/** The app's one envelope store. */
export const actionEnvelopes = new ActionEnvelopes({
  storage: safeStorage(),
  post: (path, body, headers) => api.post(path, body, { headers }),
  uuid: randomUuid,
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  deviceId: () => getDeviceId(),
});

/**
 * Every driver action POST goes through here (R1, R3, R33): the fix is
 * captured — or the pending envelope of the same action reused, fix and all
 * — the key rides the header, the device id the body. Arrive callers put
 * `expected_stop_order` in `action`.
 */
export function postDriverAction(
  path: string,
  action: Record<string, unknown>,
  opts: { runId: string | null },
): Promise<any> {
  return actionEnvelopes
    .mint(path, action, { runId: opts.runId, fix: () => fixCapture.capture() })
    .then((envelope) => actionEnvelopes.send(envelope));
}
