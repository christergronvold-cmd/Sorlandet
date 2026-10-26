// The service worker exists for exactly one reason: a push can arrive while the page is
// closed, and only a worker can be woken up to show it. There is deliberately no fetch
// handler and no cache here - a tracking page that serves yesterday's position out of a
// cache would be worse than one that does not load at all.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", event => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch (err) {
    d = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(self.registration.showNotification(d.title || "Sørlandet", {
    body: d.body || "",
    icon: "icon.svg",
    badge: "icon.svg",
    // One tag, so a second notice replaces the first rather than stacking up a column
    // of them on the lock screen. renotify makes the replacement still buzz.
    tag: "sorlandet-move",
    renotify: true,
    data: { url: d.url || "./" },
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "./";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(open => {
      for (const w of open) if ("focus" in w) return w.focus();
      return self.clients.openWindow(url);
    }));
});
