/* CIVICSYNC service worker — offline app shell.
   Cache version bumped; node APIs and the live feed are never cached. */
const CACHE = "civicsync-v1.0.7";
const SHELL = [
  "/", "/app", "/manifest.webmanifest",
  "/css/style.css", "/css/app.css",
  "/js/dashboard.js", "/js/app.js", "/js/proof.js",
  "/icons/icon-192.png", "/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  /* never cache live/API streams */
  if (url.pathname.startsWith("/api/") || url.pathname === "/video" ||
      url.pathname === "/snapshot" || e.request.method !== "GET") return;
  /* shell documents/styles/scripts: network-first, cache fallback */
  if (e.request.mode === "navigate" ||
      url.pathname.endsWith(".css") || url.pathname.endsWith(".js") ||
      url.pathname.endsWith(".html") || url.pathname.endsWith(".webmanifest")) {
    e.respondWith(fetch(e.request).then((r) => {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return r;
    }).catch(() => caches.match(e.request).then(h => h || caches.match(url.pathname))));
    return;
  }
  /* icons etc: cache first */
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((r) => {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return r;
    }))
  );
});
