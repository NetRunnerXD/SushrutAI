/* SushrutAI service worker: offline reading and dose-reminder notifications.
 *
 * Offline: pages and the person's own data (profile, medicines, doses, records, past chats) are fetched from the
 * network first and the last good copy is kept, so they can still be read without a connection. Data responses
 * are cached per profile (X-Profile-Id), so one family member's cache never answers for another. Writes are
 * never cached. The app clears these caches when data is deleted.
 * Reminders: the page schedules notifications; the "Taken" action records the dose without opening the app.
 */
const PAGES = "sushrut-pages-v1";
const DATA = "sushrut-data-v1";
const DATA_PATHS = [/\/api\/v1\/me\//, /\/api\/v1\/chat\/sessions/, /\/api\/v1\/reports\/labs/, /\/api\/v1\/prescriptions/,
                    /\/api\/v1\/family/, /\/api\/v1\/consents/, /\/api\/v1\/medicines\//];

self.addEventListener("install", () => self.skipWaiting());
// On activate, drop caches that aren't ours: other dev projects served on localhost:3000 (e.g. an old app's
// "...-shell-v1" cache) or older versions of ours could otherwise keep serving stale pages.
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  const keep = new Set([PAGES, DATA]);
  await Promise.all((await caches.keys()).filter((k) => !keep.has(k)).map((k) => caches.delete(k)));
  await self.clients.claim();
})()));

function dataKey(req) {
  const u = new URL(req.url);
  u.searchParams.set("__profile", req.headers.get("X-Profile-Id") || "self");
  return u.toString();
}

async function networkFirst(req, cacheName, key) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(key, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(key);
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/v1/") && DATA_PATHS.some((p) => p.test(url.pathname))
      && !url.pathname.endsWith(".ics") && !url.pathname.includes("/export")) {
    e.respondWith(networkFirst(req, DATA, dataKey(req)));
    return;
  }
  if (url.origin === self.location.origin && (req.mode === "navigate" || url.pathname.startsWith("/_next/static/"))) {
    e.respondWith(networkFirst(req, PAGES, req.url));
  }
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "clear-data") e.waitUntil(Promise.all([caches.delete(DATA), caches.delete(PAGES)]));
});

self.addEventListener("notificationclick", (e) => {
  const d = e.notification.data || {};
  e.notification.close();
  if (e.action === "taken" && d.api && d.token) {
    const headers = { "Content-Type": "application/json", Authorization: "Bearer " + d.token };
    if (d.profile) headers["X-Profile-Id"] = d.profile;
    e.waitUntil(fetch(d.api + "/me/doses", { method: "POST", headers,
      body: JSON.stringify({ medication_id: d.medication_id, slot: d.slot, status: "taken" }) }).catch(() => {}));
    return;
  }
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((list) => {
    const open = list.find((c) => c.url.includes(self.location.origin));
    return open ? open.focus() : self.clients.openWindow("/");
  }));
});
