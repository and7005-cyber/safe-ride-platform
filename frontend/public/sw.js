// SafeRide service worker: PWA app shell + push notifications.
// Payload parsing lives in push-payload.js (unit-tested) and handles both
// raw web-push and FCM webpush shapes.

importScripts("/push-payload.js");

const CACHE_NAME = "saferide-shell-v2";
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

  // Navigations: network first, cached shell as the offline fallback. Only a
  // healthy HTML document may overwrite the cached shell — error pages and
  // non-HTML responses must never become the offline app.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const contentType = response.headers.get("content-type") || "";
          if (response.ok && contentType.includes("text/html")) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put("/", copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  // Icons and the manifest: stale-while-revalidate, so app icon / manifest
  // updates reach installed clients on the next load.
  if (url.pathname.startsWith("/icons/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(
      caches.match(request).then((cached) => {
        const refresh = fetch(request)
          .then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, copy)).catch(() => {});
            }
            return response;
          })
          .catch(() => cached);
        return cached || refresh;
      }),
    );
  }
  // Hashed bundles (/assets/) are deliberately not cached here: their names
  // change every deploy and caching them would only accumulate dead entries.
});

function readPushPayload(event) {
  if (!event.data) return self.parsePushPayload(null);
  try {
    return self.parsePushPayload(event.data.json());
  } catch (_e) {
    const payload = self.parsePushPayload(null);
    payload.body = event.data.text();
    return payload;
  }
}

self.addEventListener("push", (event) => {
  const payload = readPushPayload(event);
  event.waitUntil(
    Promise.all([
      // No tag: each notification stands alone, so multi-child updates never
      // overwrite each other in the notification tray.
      self.registration.showNotification(payload.title, {
        body: payload.body,
        icon: "/icons/icon-192.png",
        badge: "/icons/icon-192.png",
        data: { url: payload.url, type: payload.type },
      }),
      // Tell any open app windows so they can toast + refresh feeds.
      self.clients
        .matchAll({ type: "window", includeUncontrolled: true })
        .then((clients) => {
          for (const client of clients) {
            client.postMessage({ type: "SAFERIDE_PUSH", payload });
          }
        })
        .catch(() => {}),
    ]),
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
