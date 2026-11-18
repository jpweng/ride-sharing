"use strict";

// ---------------------------------------------------------------------
// Service worker: makes the site an installable app (PWA, and the Android
// Trusted Web Activity built from it) that opens instantly and says so
// clearly when the device is offline.
//
// - The app's own files (SHELL below) are network-first: every load asks
//   the server (revalidating, so the browser's HTTP cache never serves an
//   old copy), and a fix deployed during an event reaches every user on
//   their next launch. Each successful answer refreshes the cached copy,
//   which is only used when the network fails, so the installed app still
//   opens offline.
// - The map library (LIBS: Leaflet, a pinned version from unpkg) never
//   changes at a given URL, so it is served from the cache once fetched.
//   It is cached on a best-effort basis, never as part of the shell's
//   download, so unpkg being unreachable can't block an update.
// - /api/* always goes to the network and is never cached: a ride list or
//   a match shown from a cache could be out of date without the visitor
//   knowing. Offline, the API answers 503 with the error code "offline",
//   which the app shows as errors.offline like any other API error.
// - Everything else from other sites (OpenStreetMap map tiles, address
//   search, transport logos) is not touched: the browser's normal HTTP
//   caching applies, as without a service worker.
// - A page that isn't cached and can't be loaded shows offline.html.
//
// Updates: the server replaces VERSION with a hash of all the frontend
// files when it serves this file (backend/routes/pwa.py, GET /sw.js), so any
// deploy that changes a frontend file changes sw.js. The browser checks
// sw.js on every page load (and pwa.js asks again when the app comes back
// to the foreground); a changed one downloads the offline copy of the
// shell into a new cache, takes over at once (skipWaiting + clients.claim)
// and deletes the old caches. Since the shell is network-first, a launch
// with a connection runs the latest files whether or not the new worker
// has installed yet. A window that was open during the deploy keeps its
// old code until reloaded, and pwa.js offers it a "Reload" button.
// ---------------------------------------------------------------------

const VERSION = "dev";
const CACHE_PREFIX = "rideandrail-shell-";
const CACHE = CACHE_PREFIX + VERSION;

// Relative to this file, which is served at the site root. Keep in sync
// with the frontend (backend/tests/test_pwa.py checks every file exists and
// that the Leaflet URLs are the ones index.html loads).
const SHELL = [
  "index.html",
  "privacy.html",
  "conduct.html",
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
// Pinned third-party libraries the app can't start without. Keep in sync
// with index.html (backend/tests/test_pwa.py checks it).
const LIBS = [
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
];
const SHELL_URLS = new Set(SHELL.map((path) => new URL(path, self.location).href));
const LIB_URLS = new Set(LIBS);
const OFFLINE_PAGE = new URL("offline.html", self.location).href;

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // cache: "reload" skips the HTTP cache, so a new version never
      // stores an older copy of a file.
      await cache.addAll(SHELL.map((path) => new Request(path, { cache: "reload" })));
      // Best effort: if unpkg is unreachable, the libraries are cached on
      // their first successful fetch instead, and the update still installs.
      // (unpkg allows CORS, so its files are cached like ours.)
      await Promise.allSettled(LIBS.map((url) => cache.add(url)));
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

  // Pinned libraries: cache-first, cached on first success.
  if (LIB_URLS.has(url.href)) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(url.href);
        if (cached) return cached;
        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(CACHE);
          event.waitUntil(cache.put(url.href, response.clone()));
        }
        return response;
      })()
    );
    return;
  }

  // The app's own files: network-first, revalidating with the server; the
  // cached copy is the offline fallback, refreshed by each success.
  const key = sameOrigin ? shellUrl(url) : url.href;
  if (SHELL_URLS.has(key)) {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request, { cache: "no-cache" });
          if (response.ok && response.type === "basic") {
            const cache = await caches.open(CACHE);
            event.waitUntil(cache.put(key, response.clone()));
          }
          return response;
        } catch (err) {
          const cached = await caches.match(key);
          if (cached) return cached;
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
