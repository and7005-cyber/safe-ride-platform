// SafeRide service worker: PWA app shell + push notifications.
// Push payloads arrive in two shapes and both are handled:
//   raw web push: {title, body, url, type}
//   FCM webpush:  {notification: {title, body, icon}, data: {url, type}}

const CACHE_NAME = "saferide-shell-v1";
const SHELL_URLS = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];
const DEFAULT_URL = "/parent/alerts";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_URLS))
      .catch(() => {})
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // never touch API calls
  if (url.pathname.startsWith("/api/")) return;

  // Navigations: network first, cached shell as the offline fallback.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put("/", copy)).catch(() => {});
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  // Static assets: cache first, then network.
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          if (response.ok && (url.pathname.startsWith("/icons/") || url.pathname.startsWith("/assets/"))) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return response;
        }),
    ),
  );
});

function parsePushPayload(event) {
  const fallback = { title: "SafeRide", body: "", url: DEFAULT_URL, type: "custom" };
  if (!event.data) return fallback;
  let raw;
  try {
    raw = event.data.json();
  } catch (_e) {
    return { ...fallback, body: event.data.text() };
  }
  const notification = raw.notification ?? {};
  const data = raw.data ?? {};
  return {
    title: notification.title ?? raw.title ?? fallback.title,
    body: notification.body ?? raw.body ?? fallback.body,
    url: data.url ?? raw.url ?? raw.fcmOptions?.link ?? fallback.url,
    type: data.type ?? raw.type ?? fallback.type,
  };
}

self.addEventListener("push", (event) => {
  const payload = parsePushPayload(event);
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag: `saferide-${payload.type}`,
      data: { url: payload.url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url ?? DEFAULT_URL;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(url).catch(() => {});
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
