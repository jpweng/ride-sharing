"use strict";

// The text pages (About, the privacy notice) say how a ride is paid for:
// between passenger and driver, or through the site. Which one is true
// depends on whether payments are switched on (GET /api/payments/config,
// see docs/payments.md), so both are written in the page, marked
// data-payments="off" and data-payments="on" (hidden), and this script shows
// the right ones. The last answer is remembered, for reading offline.
(function () {
  const KEY = "payments";
  function show(on) {
    document.querySelectorAll("[data-payments]").forEach((element) => {
      element.hidden = (element.dataset.payments === "on") !== on;
    });
  }
  try {
    if (localStorage.getItem(KEY) === "on") show(true);
  } catch {
    // no storage (private mode): the page's own texts until the answer comes
  }
  fetch("/api/payments/config")
    .then((resp) => resp.json())
    .then((config) => {
      show(Boolean(config.enabled));
      try {
        localStorage.setItem(KEY, config.enabled ? "on" : "off");
      } catch {
        // as above
      }
    })
    .catch(() => {});
})();
