/* RxScan service worker.
 * - App shell (page, scripts, icons) is cached so the app opens offline.
 * - /api/* is NEVER cached: receipts and parsed data only live on your device and in your sheet.
 * - Handles the Android share sheet (POST /share-target).
 * __VERSION__ is replaced by the server with a hash of the static files, so each deploy busts the cache.
 */
const VERSION = "__VERSION__";
const CACHE = "rxscan-shell-" + VERSION;
const SHELL = [
  "/",
  "/manifest.webmanifest",
  "/static/idb.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-192.png",
  "/static/icons/maskable-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/favicon-32.png",
  "/static/icons/shortcut-camera.png",
];

importScripts("/static/idb.js");

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) =>
      // credentials so the cached "/" works behind APP_PASSWORD basic auth
      Promise.all(SHELL.map((u) => fetch(u, { credentials: "include", cache: "no-cache" })
        .then((r) => { if (r.ok && !r.redirected) return c.put(u, r); }) // never cache the sign-in page as the app
        .catch(() => {})))
    )
  );
  // Don't skipWaiting automatically: the page shows "Update available" and the user chooses.
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("rxscan-shell-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Android share sheet → stash files in IndexedDB, then open the app.
  if (req.method === "POST" && url.pathname === "/share-target") {
    e.respondWith((async () => {
      try {
        const form = await req.formData();
        const files = form.getAll("images").filter((f) => f && f.size);
        await RxDB.inboxAdd(files.map((f) => ({ name: f.name || "shared.jpg", type: f.type, blob: f })));
      } catch (err) { /* fall through to the app anyway */ }
      return Response.redirect("/?share=1", 303);
    })());
    return;
  }

  if (req.method !== "GET") return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return; // network only, never cached
  if (url.pathname === "/login" || url.pathname === "/logout") return;        // always live

  // Page navigations: network first (fresh code), cached shell when offline.
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try {
        const r = await fetch(req);
        // Only the real app page becomes the offline shell (not /login, not a redirect to it).
        if (r.ok && !r.redirected && url.pathname === "/") (await caches.open(CACHE)).put("/", r.clone());
        return r;
      } catch {
        return (await caches.match("/")) || new Response("Offline", { status: 503 });
      }
    })());
    return;
  }

  // Static assets: cache first, refresh in the background.
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const hit = await cache.match(req, { ignoreSearch: true });
      const net = fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => null);
      return hit || (await net) || new Response("", { status: 504 });
    })());
  }
});
