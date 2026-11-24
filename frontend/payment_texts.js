"use strict";

// The text pages (About, the privacy notice) say how a ride is paid for:
// between passenger and driver, or through the site. Which one is true
// depends on whether payments are switched on (GET /api/payments/config,
// see docs/payments.md), so both are written in the page, marked
// data-payments="off" and data-payments="on" (hidden), and this script shows
// the right ones. A third kind, data-payments="fee" (hidden), is shown when
// payments are on with a service fee. The last answer is remembered, for
// reading offline.
(function () {
  const KEY = "payments";
  // state: "off", "on", or "fee" (on, with a service fee).
  function show(state) {
    document.querySelectorAll("[data-payments]").forEach((element) => {
      const when = element.dataset.payments;
      element.hidden = when === "fee" ? state !== "fee" : (when === "on") !== (state !== "off");
    });
  }
  try {
    const remembered = localStorage.getItem(KEY);
    if (remembered === "on" || remembered === "fee") show(remembered);
  } catch {
    // no storage (private mode): the page's own texts until the answer comes
  }
  fetch("/api/payments/config")
    .then((resp) => resp.json())
    .then((config) => {
      const state = !config.enabled ? "off" : config.fee ? "fee" : "on";
      show(state);
      try {
        localStorage.setItem(KEY, state);
      } catch {
        // as above
      }
    })
    .catch(() => {});
})();
