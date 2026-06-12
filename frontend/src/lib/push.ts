// Client push plumbing: Firebase Cloud Messaging first, raw Web Push (VAPID)
// fallback. The server tells us what is configured via /api/push/config; with
// neither configured push stays off and the in-app alerts feed carries the
// same notifications.
import { api } from "@/lib/apiClient";

export interface PushConfig {
  firebase: Record<string, string> | null;
  firebaseVapidKey: string | null;
  vapidPublicKey: string | null;
}

export type PushMode = "fcm" | "webpush";

const PUSH_MODE_KEY = "saferide-push-mode";

let configPromise: Promise<PushConfig> | null = null;

export function fetchPushConfig(): Promise<PushConfig> {
  if (!configPromise) {
    configPromise = api.get("/api/push/config").catch(() => {
      configPromise = null;
      return { firebase: null, firebaseVapidKey: null, vapidPublicKey: null };
    });
  }
  return configPromise;
}

export function getStoredPushMode(): PushMode | null {
  const value = localStorage.getItem(PUSH_MODE_KEY);
  return value === "fcm" || value === "webpush" ? value : null;
}

function storePushMode(mode: PushMode | null) {
  if (mode) localStorage.setItem(PUSH_MODE_KEY, mode);
  else localStorage.removeItem(PUSH_MODE_KEY);
}

export function registerServiceWorker(): Promise<ServiceWorkerRegistration> | null {
  if (!("serviceWorker" in navigator)) return null;
  return navigator.serviceWorker.register("/sw.js");
}

async function firebaseMessaging(config: PushConfig) {
  const { initializeApp, getApps } = await import("firebase/app");
  const { getMessaging } = await import("firebase/messaging");
  const app = getApps()[0] ?? initializeApp(config.firebase!);
  return { app, messaging: getMessaging(app) };
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(normalized);
  return Uint8Array.from([...raw].map((char) => char.charCodeAt(0)));
}

/** Enable push for this device. Returns the mode used, or null if the server
 * has no push channel configured. Throws when the browser refuses. */
export async function enablePush(): Promise<PushMode | null> {
  const config = await fetchPushConfig();
  const registration = await navigator.serviceWorker.ready;

  if (config.firebase && config.firebaseVapidKey) {
    const { messaging } = await firebaseMessaging(config);
    const { getToken } = await import("firebase/messaging");
    const token = await getToken(messaging, {
      vapidKey: config.firebaseVapidKey,
      serviceWorkerRegistration: registration,
    });
    await api.post("/api/push/fcm-token", { token, user_agent: navigator.userAgent });
    storePushMode("fcm");
    return "fcm";
  }

  if (config.vapidPublicKey) {
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(config.vapidPublicKey) as BufferSource,
    });
    const json = subscription.toJSON();
    await api.post("/api/push/subscribe", {
      endpoint: subscription.endpoint,
      p256dh: json.keys?.p256dh ?? null,
      auth: json.keys?.auth ?? null,
      user_agent: navigator.userAgent,
    });
    storePushMode("webpush");
    return "webpush";
  }

  return null;
}

export async function disablePush(): Promise<void> {
  const mode = getStoredPushMode();
  const config = await fetchPushConfig();

  if (mode === "fcm" && config.firebase) {
    try {
      const { messaging } = await firebaseMessaging(config);
      const { getToken, deleteToken } = await import("firebase/messaging");
      const registration = await navigator.serviceWorker.ready;
      const token = await getToken(messaging, {
        vapidKey: config.firebaseVapidKey ?? undefined,
        serviceWorkerRegistration: registration,
      });
      if (token) {
        await api.post("/api/push/fcm-token/unregister", { token }).catch(() => {});
      }
      await deleteToken(messaging);
    } catch {
      // Token may already be gone; clearing local state is enough.
    }
  }

  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      await api.post("/api/push/unsubscribe", { endpoint: subscription.endpoint }).catch(() => {});
      await subscription.unsubscribe();
    }
  } catch {
    // Best effort.
  }
  storePushMode(null);
}

/** True when this device currently has an active push setup. */
export async function isPushActive(): Promise<boolean> {
  if (!("serviceWorker" in navigator) || Notification.permission !== "granted") return false;
  const mode = getStoredPushMode();
  if (mode === "fcm") return true;
  try {
    const registration = await navigator.serviceWorker.ready;
    return Boolean(await registration.pushManager.getSubscription());
  } catch {
    return false;
  }
}

/** Foreground push messages (page focused): the service worker relays every
 * push it receives via postMessage (works for both FCM and raw web push). */
export async function listenForegroundMessages(
  onNotification: (payload: { title: string; body: string }) => void,
): Promise<() => void> {
  if (!("serviceWorker" in navigator)) return () => {};
  const handler = (event: MessageEvent) => {
    if (event.data?.type !== "SAFERIDE_PUSH") return;
    const payload = event.data.payload ?? {};
    onNotification({
      title: payload.title ?? "SafeRide",
      body: payload.body ?? "",
    });
  };
  navigator.serviceWorker.addEventListener("message", handler);
  return () => navigator.serviceWorker.removeEventListener("message", handler);
}
