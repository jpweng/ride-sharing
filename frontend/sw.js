"use strict";

// ---------------------------------------------------------------------
// Service worker: makes the site an installable app (PWA, and the Android
// Trusted Web Activity built from it) that opens instantly and says so
// clearly when the device is offline.
//
// - The app shell (SHELL below) is downloaded once per version and served
//   from the cache, so the app opens fast even on a slow mobile network.
// - /api/* always goes to the network and is never cached: a ride list or
//   a match shown from a cache could be out of date without the visitor
//   knowing. Offline, the API answers 503 with the error code "offline",
//   which the app shows as errors.offline like any other API error.
// - Everything else from other sites (OpenStreetMap map tiles, address
//   search, transport logos) is not touched: the browser's normal HTTP
//   caching applies, as without a service worker. Only the map library
//   (Leaflet, a pinned version from unpkg) is part of the shell, since the
//   app can't start without it.
// - A page that isn't cached and can't be loaded shows offline.html.
//
// Updates: the server replaces VERSION with a hash of all the frontend
// files when it serves this file (backend/main.py, GET /sw.js), so any
// deploy that changes a frontend file changes sw.js. The browser checks
// sw.js on every page load (and pwa.js asks again when the app comes back
// to the foreground); a changed one installs the new version's files into
// a new cache, then takes over at once (skipWaiting + clients.claim) and
// deletes the old caches. So the next launch after a deploy always gets
// the new version, even if an old window is still open in the background,
// and pwa.js offers the open window a "Reload" button. Until it's
// reloaded, an open window keeps running its old code, exactly as it
// would without a service worker.
// ---------------------------------------------------------------------

const VERSION = "dev";
const CACHE_PREFIX = "rideandrail-shell-";
const CACHE = CACHE_PREFIX + VERSION;

// Relative to this file, which is served at the site root. Keep in sync
// with the frontend (backend/tests/test_pwa.py checks every file exists and
// that the Leaflet URLs are the ones index.html loads).
const SHELL = [
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  "index.html",
  "privacy.html",
  "offline.html",
  "style.css",
  "app.js",
  "i18n.js",
  "pwa.js",
  "i18n/en.json",
  "i18n/de.json",
  "i18n/fr.json",
  "i18n/it.json",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/maskable-512.png",
  "icons/apple-touch-icon.png",
];
const SHELL_URLS = new Set(SHELL.map((path) => new URL(path, self.location).href));
const OFFLINE_PAGE = new URL("offline.html", self.location).href;

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // cache: "reload" skips the HTTP cache, so a new version never
      // stores an older copy of a file. (unpkg allows CORS, so its files
      // are fetched and cached like ours.)
      await cache.addAll(SHELL.map((path) => new Request(path, { cache: "reload" })));
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE).map((name) => caches.delete(name))
      );
      await self.clients.claim();
    })()
  );
});

// "/" and "/?lang=fr" are index.html.
function shellUrl(url) {
  const clean = new URL(url.pathname, url);
  if (clean.pathname.endsWith("/")) clean.pathname += "index.html";
  return clean.href;
}

function offlineApiResponse() {
  const body = { detail: { code: "offline", message: "No network connection" } };
  return new Response(JSON.stringify(body), {
    status: 503,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  if (sameOrigin && url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request).catch(offlineApiResponse));
    return;
  }
  if (request.method !== "GET") return;

  const key = sameOrigin ? shellUrl(url) : url.href;
  if (SHELL_URLS.has(key)) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(key, { cacheName: CACHE });
        if (cached) return cached;
        try {
          return await fetch(request);
        } catch (err) {
          if (request.mode === "navigate") return (await caches.match(OFFLINE_PAGE)) || Response.error();
          throw err;
        }
      })()
    );
    return;
  }

  if (sameOrigin && request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => (await caches.match(OFFLINE_PAGE)) || Response.error())
    );
  }
  // Anything else (other sites, sw.js itself, /docs, ...) goes to the
  // network as usual.
});
