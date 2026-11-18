"use strict";

// ---------------------------------------------------------------------
// Installable app: registers the service worker (sw.js, which explains the
// caching and update rules) and shows a notice at the bottom of the page
// when the device is offline, or when a new version has been deployed
// while this page was open. Load after i18n.js.
// ---------------------------------------------------------------------
(() => {
  // A new version is looked for on every page load; an app left open
  // (or in the background on a phone) also looks when it comes back to
  // the foreground, at most this often.
  const UPDATE_CHECK_MS = 30 * 60 * 1000;

  let notice = null; // "offline" | "updated" | null
  let banner = null;

  function render() {
    if (!notice) {
      banner?.remove();
      banner = null;
      return;
    }
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "pwa-banner";
      banner.setAttribute("role", "status");
      document.body.append(banner);
    }
    banner.textContent = I18N.t(notice === "updated" ? "pwa.updated" : "pwa.offline");
    if (notice === "updated") {
      const reload = document.createElement("button");
      reload.type = "button";
      reload.textContent = I18N.t("pwa.reload");
      reload.addEventListener("click", () => location.reload());
      banner.append(" ", reload);
    }
  }

  function show(what) {
    // The update notice wins: reloading is what to do either way.
    if (notice === "updated" && what !== null) return;
    notice = what;
    I18N.ready.then(render);
  }

  window.addEventListener("i18n:change", render);
  window.addEventListener("offline", () => show("offline"));
  window.addEventListener("online", () => notice === "offline" && show(null));
  if (navigator.onLine === false) show("offline");

  if (!("serviceWorker" in navigator)) return;

  // Set when a service worker already controlled this page, so that the
  // very first install (which also takes control) isn't announced as an update.
  const hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (hadController) show("updated");
  });

  window.addEventListener("load", async () => {
    let registration;
    try {
      registration = await navigator.serviceWorker.register("sw.js");
    } catch {
      return; // e.g. plain http on a LAN address: the site works without it
    }
    let lastCheck = Date.now();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible" || Date.now() - lastCheck < UPDATE_CHECK_MS) return;
      lastCheck = Date.now();
      registration.update().catch(() => {}); // offline: try again next time
    });
  });
})();
