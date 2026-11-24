"use strict";

// ---------------------------------------------------------------------
// Getting paid as a driver (backend/payouts.py, see docs/payments.md): a
// section on the Driver page, under the driver's drives, to set payouts up
// on Stripe's pages and see what is waiting and what was sent; and the
// inbox's notifications about payouts. Loaded after app.js, bookings.js
// and payments.js. Nothing shows while payments are switched off.
// ---------------------------------------------------------------------

function payoutsBox() {
  let box = document.getElementById("payouts-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "payouts-box";
    box.hidden = true;
    document.getElementById("drive-mine").after(box);
  }
  return box;
}

// `back`: the driver just returned from Stripe's pages ("done"), or their
// link there had expired ("again").
async function loadPayouts(back) {
  const box = payoutsBox();
  if (!signedIn() || currentUser.is_demo) {
    box.hidden = true;
    return;
  }
  let status;
  try {
    status = await (back === "done" ? postJson("/api/payouts/refresh", {}) : fetchJson("/api/payouts"));
  } catch {
    box.hidden = true; // the Driver page works without it
    return;
  }
  box.hidden = !status.enabled;
  if (!status.enabled) return;

  const text = { none: "payout.none", incomplete: "payout.incomplete", ready: "payout.ready" }[status.state];
  const lines = [];
  if (status.waiting_chf > 0) lines.push(t("payout.waiting", { amount: fmtChf(status.waiting_chf) }));
  if (status.sent_chf > 0) lines.push(t("payout.sent", { amount: fmtChf(status.sent_chf) }));
  box.innerHTML = `
    <div class="section-title">${esc(t("payout.title"))}</div>
    <div class="status-msg"></div>
    <div class="hint">${esc(t(text))}</div>
    ${lines.map((line) => `<div class="meta">${esc(line)}</div>`).join("")}
    <div class="payouts-buttons"></div>
  `;
  const message = box.querySelector(".status-msg");
  if (back === "done") {
    message.textContent = t(status.state === "ready" ? "payout.backReady" : "payout.backPending");
    message.className = "status-msg show ok";
  }
  // Both buttons lead to Stripe's pages, at an address asked for at the click.
  const leave = (key, className, url) => {
    const button = actionButton(key, className, async () => {
      button.disabled = true;
      try {
        location.href = (await postJson(url, {})).url;
      } catch (err) {
        button.disabled = false;
        message.textContent = err.message;
        message.className = "status-msg show err";
      }
    });
    return button;
  };
  const buttons = box.querySelector(".payouts-buttons");
  if (status.state === "ready") buttons.append(leave("payout.dashboard", "link-btn", "/api/payouts/dashboard"));
  else buttons.append(leave(status.state === "none" ? "payout.setUp" : "payout.continue", "submit-btn", "/api/payouts/onboard"));
}

{
  // Back from Stripe's pages: the address says so (?payouts=done or again).
  const params = new URLSearchParams(location.search);
  let back = params.get("payouts");
  if (back) {
    params.delete("payouts");
    const query = params.toString();
    history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  }
  const show = PAGES.drive.onShow;
  PAGES.drive.onShow = (...args) => {
    show(...args);
    loadPayouts(back);
    back = null;
  };
  // app.js may have shown the page before this script ran.
  if (document.getElementById("page-drive").classList.contains("active")) PAGES.drive.onShow();
}

// ---------------------------------------------------------------------
// The inbox's notifications about payouts
// ---------------------------------------------------------------------
NOTIFICATION_CARDS.payout_setup = bookingNotification(
  (details) => t("payout.inboxSetup", { amount: fmtChf(details.amount_chf || 0) }),
  "payout.setUp", "drive",
);
NOTIFICATION_CARDS.payout_sent = bookingNotification(
  (details) => t("payout.inboxSent", { amount: fmtChf(details.amount_chf || 0) }),
  null,
);
NOTIFICATION_CARDS.payout_lapsed = bookingNotification(() => t("payout.inboxLapsed"), null);
