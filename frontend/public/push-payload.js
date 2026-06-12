// Push payload normalizer, shared by the service worker (importScripts) and
// the vitest unit suite. Accepts both delivery shapes:
//   raw web push: {title, body, url, type}
//   FCM webpush:  {notification: {title, body, icon}, data: {url, type}}
function parsePushPayload(raw) {
  const fallback = { title: "SafeRide", body: "", url: "/parent/alerts", type: "custom" };
  if (!raw || typeof raw !== "object") return fallback;
  const notification = raw.notification ?? {};
  const data = raw.data ?? {};
  return {
    title: notification.title ?? raw.title ?? fallback.title,
    body: notification.body ?? raw.body ?? fallback.body,
    url: data.url ?? raw.url ?? (raw.fcmOptions && raw.fcmOptions.link) ?? fallback.url,
    type: data.type ?? raw.type ?? fallback.type,
  };
}

self.parsePushPayload = parsePushPayload;
