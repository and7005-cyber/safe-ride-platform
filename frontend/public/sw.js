// SafeRide push service worker. Shows arrival notifications and deep-links
// to the parent alerts page.
self.addEventListener("push", (event) => {
  let payload = { title: "SafeRide", body: "", url: "/parent/alerts" };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (_e) {
    if (event.data) payload.body = event.data.text();
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      data: { url: payload.url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url ?? "/parent/alerts";
  event.waitUntil(self.clients.openWindow(url));
});
