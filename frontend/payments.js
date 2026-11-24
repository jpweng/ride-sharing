"use strict";

// ---------------------------------------------------------------------
// Paying for a seat (backend/payments.py, see docs/payments.md): "Pay" on
// the passenger's accepted booking, which leads to Stripe's payment page
// and back, what the driver sees of it, and the inbox's notifications
// about payments. Loaded after app.js and bookings.js, whose lists of
// extras it adds to. A booking carries a `payment` only when seats are paid
// through the site: without one, nothing here shows.
// ---------------------------------------------------------------------

// A passenger who cancels gets everything back until this long before the
// pickup (payments.py: FREE_CANCEL_UNTIL).
const FREE_CANCEL_HOURS = 2;

// GET /api/payments/config: whether seats are paid through the site, and
// the service fee ({percent, fixed_cts}, or null).
const paymentsConfig = fetchJson("/api/payments/config").catch(() => ({ enabled: false, fee: null }));

// The service fee on a ride price, in centimes, as payments.py computes
// it (fee_cts): the share plus the fixed part, rounded to 5 centimes.
function serviceFeeCts(rideCts, fee) {
  if (!fee || rideCts <= 0) return 0;
  return Math.floor((rideCts * fee.percent / 100 + fee.fixed_cts) / 5 + 0.5) * 5;
}

// A search result or a listed drive: the service fee one seat would cost,
// next to its price, so that the price shown is the price paid.
CARD_EXTRAS.push((kind, item, card) => {
  if (kind === "mine" || typeof item.price_chf !== "number") return;
  paymentsConfig.then((config) => {
    const fee = serviceFeeCts(Math.round(item.price_chf * 100), config.enabled ? config.fee : null);
    if (!fee) return;
    const text = esc(t("payment.fee", { amount: fmtChf(fee / 100) }));
    // Under the price of a search result (app.js: tripPrice), or after a price badge.
    const price = card.querySelector(".trip-price");
    if (price) price.insertAdjacentHTML("beforeend", `<span class="trip-price-note payment-fee">${text}</span>`);
    else card.querySelector(".badge.price")?.insertAdjacentHTML("afterend", `<span class="badge fee">${text}</span>`);
  });
});

function paymentBadge(key, className) {
  return `<span class="badge payment ${className}">${esc(t(key))}</span>`;
}

BOOKING_EXTRAS.push((booking, element, otherRole) => {
  const payment = booking.payment;
  if (!payment) return;
  if (otherRole === "driver") myPayment(booking, payment, element);
  else if (booking.status === "accepted") {
    // The driver, on a passenger of theirs: paid, still to pay, or on hold
    // because the passenger said the ride didn't take place.
    let badge = payment.status === "paid" ? paymentBadge("payment.paid", "paid") : paymentBadge("payment.waiting", "waiting");
    if (payment.on_hold) badge = paymentBadge("payment.onHold", "waiting");
    element.querySelector(".booking-who")?.insertAdjacentHTML("beforeend", ` ${badge}`);
  }
});

// Before a paid booking is cancelled: what comes back (payments.py:
// refund_on_cancel_cts).
BOOKING_CANCEL_NOTES.push((booking, by) => {
  const payment = booking.payment;
  if (!payment || payment.status !== "paid" || typeof payment.refund_if_cancelled_chf !== "number") return null;
  if (by === "driver") return t("payment.cancelByDriver", { name: booking.passenger_name });
  const back = payment.refund_if_cancelled_chf;
  if (back >= payment.amount_chf) return t("payment.cancelFull", { amount: fmtChf(back) });
  if (back > 0) {
    const key = payment.fee_chf > 0 ? "payment.cancelPartlyFee" : "payment.cancelPartly";
    return t(key, { amount: fmtChf(back), hours: FREE_CANCEL_HOURS });
  }
  return t("payment.cancelNothing");
});

// The passenger's own booking: what to pay and until when, or what was
// paid and what came back.
function myPayment(booking, payment, card) {
  // Not "you settle with the driver" any more; nothing to explain once paid.
  const note = card.querySelector(".booking-price-note");
  if (note) {
    note.textContent = t("payment.priceNote");
    note.hidden = payment.status !== "due";
  }
  const box = document.createElement("div");
  box.className = "payment-box";
  const lines = [];
  // With a service fee: what of the amount is the ride, what the fee.
  const breakdown = () => {
    if (payment.fee_chf > 0) {
      lines.push(t("payment.breakdown", { ride: fmtChf(payment.amount_chf - payment.fee_chf), fee: fmtChf(payment.fee_chf) }));
    }
  };
  if (payment.status === "paid") {
    card.querySelector(".route")?.insertAdjacentHTML("beforeend", ` ${paymentBadge("payment.paid", "paid")}`);
    lines.push(t("payment.paidAmount", { amount: fmtChf(payment.amount_chf) }));
    breakdown();
  }
  if (payment.refunded_chf > 0) lines.push(t("payment.refunded", { amount: fmtChf(payment.refunded_chf) }));
  if (payment.free_cancel_until && new Date(payment.free_cancel_until) > new Date()) {
    lines.push(t("payment.cancelFreeUntil", { time: fmtTime(payment.free_cancel_until) }));
  }
  if (payment.on_hold) lines.push(t("payment.onHoldMine"));
  const due = payment.status === "due" && booking.status === "accepted" && typeof payment.amount_chf === "number";
  if (due) {
    lines.push(t("payment.payBy", { time: fmtTime(payment.pay_by) }));
    breakdown();
  }
  if (!lines.length) return;
  box.innerHTML = lines.map((line) => `<div class="meta">${esc(line)}</div>`).join("");
  if (due) {
    const pay = actionButton("payment.pay", "submit-btn", async () => {
      pay.disabled = true;
      try {
        const { url } = await postJson(`/api/bookings/${booking.id}/pay`, {});
        location.href = url; // Stripe's payment page; it sends the passenger back here
      } catch (err) {
        pay.disabled = false;
        showBoxError(card, err.message);
      }
    });
    pay.textContent = t("payment.pay", { amount: fmtChf(payment.amount_chf) });
    box.appendChild(pay);
  }
  if (payment.can_dispute) {
    // From the pickup time until the driver is paid.
    box.appendChild(actionButton("payment.dispute", "link-btn", async () => {
      if (!confirm(t("payment.disputeConfirm"))) return;
      try {
        await postJson(`/api/bookings/${booking.id}/pay/dispute`, {});
        await loadBookings();
        renderMyBookings();
      } catch (err) {
        showBoxError(card, err.message);
      }
    }));
  }
  (note || card.querySelector(".status-msg")).before(box);
}

// ---------------------------------------------------------------------
// Back from the payment page: the address says for which booking
// (?paid=12 after paying, ?unpaid=12 after leaving the page without).
// ---------------------------------------------------------------------
function showPaymentBack(key, ok) {
  let message = document.getElementById("payment-back");
  if (!message) {
    message = document.createElement("div");
    message.id = "payment-back";
    document.getElementById("page-ride").prepend(message);
  }
  message.className = `status-msg show ${ok ? "ok" : "err"}`;
  message.textContent = t(key);
}

{
  const params = new URLSearchParams(location.search);
  const back = ["paid", "unpaid"].find((name) => params.has(name));
  if (back) {
    const bookingId = Number(params.get(back));
    params.delete(back);
    const query = params.toString();
    history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
    const refresh = () => {
      if (!signedIn()) return;
      loadBookings().then(renderMyBookings);
    };
    if (back === "unpaid") showPaymentBack("payment.backUnpaid", false);
    else {
      // Stripe tells the server itself, but perhaps not yet: ask.
      postJson(`/api/bookings/${bookingId}/pay/check`, {})
        .then((answer) => showPaymentBack(answer.status === "paid" ? "payment.backPaid" : "payment.backPending", true))
        .catch(() => showPaymentBack("payment.backPending", true))
        .then(refresh);
    }
  }
}

// ---------------------------------------------------------------------
// The inbox's notifications about payments (bookings.js: bookingNotification)
// ---------------------------------------------------------------------
NOTIFICATION_CARDS.payment_received = bookingNotification(
  (details) => t("payment.inboxReceived", { name: details.passenger_name }),
  "booking.inboxSeeDrives", "drive",
);
NOTIFICATION_CARDS.booking_unpaid = bookingNotification(() => t("payment.inboxUnpaid"), null);
NOTIFICATION_CARDS.payment_refunded = bookingNotification(
  (details) => t(details.reason === "shared" ? "payment.inboxRefundedShared" : "payment.inboxRefunded", { amount: fmtChf(details.amount_chf || 0) }),
  null,
);
// "The ride didn't take place": the driver hears the payment is on hold,
// then each side what the team decided.
NOTIFICATION_CARDS.payout_held = bookingNotification(
  (details) => t("payment.inboxHeld", { name: details.passenger_name }),
  null,
);
NOTIFICATION_CARDS.payment_released = bookingNotification(() => t("payment.inboxReleased"), null);
NOTIFICATION_CARDS.payout_refunded = bookingNotification(() => t("payment.inboxPayoutRefunded"), null);

// A booking cancelled because it wasn't paid in time says so to the driver.
{
  const cancelled = NOTIFICATION_CARDS.booking_cancelled;
  const notPaid = bookingNotification(
    (details) => t("payment.inboxNotPaid", { name: details.passenger_name }),
    "booking.inboxSeeDrives", "drive",
  );
  NOTIFICATION_CARDS.booking_cancelled = (n, card) => ((n.details || {}).reason === "unpaid" ? notPaid : cancelled)(n, card);
}

// ---------------------------------------------------------------------
// "Your payments" on the account page: what the user paid as a passenger
// (the receipts' content: date, trip, amount, fee, refund, Stripe's
// reference) and what was sent to them as a driver. Shown when there are
// any.
// ---------------------------------------------------------------------
async function renderPaymentHistory() {
  let box = document.getElementById("payments-history");
  if (!box) {
    box = document.createElement("div");
    box.id = "payments-history";
    document.getElementById("account-delete-box").before(box);
  }
  box.hidden = true;
  if (!signedIn() || currentUser.is_demo) return;
  let history;
  try {
    history = await fetchJson("/api/payments");
  } catch {
    return; // the account page works without it
  }
  if (!history.paid.length && !history.received.length) return;
  const row = (p, lines) => `
    <div class="drive-card payment-row">
      <div class="route">${esc(p.origin_name)} → ${esc(p.destination_name)}</div>
      <div class="meta">${fmtTime(p.pickup_time)} · ${esc(t("common.seats", { count: p.seats }))}</div>
      ${lines.map((line) => `<div class="meta">${esc(line)}</div>`).join("")}
    </div>`;
  const paid = history.paid.map((p) => {
    const lines = [t("payment.listPaid", { amount: fmtChf(p.amount_chf) })];
    if (p.fee_chf > 0) lines.push(t("payment.breakdown", { ride: fmtChf(p.amount_chf - p.fee_chf), fee: fmtChf(p.fee_chf) }));
    if (p.refunded_chf > 0) lines.push(t("payment.refunded", { amount: fmtChf(p.refunded_chf) }));
    lines.push(t("payment.listRef", { ref: p.reference }));
    return row(p, lines);
  });
  const received = history.received.map((p) => row(p, [t("payment.listReceived", { amount: fmtChf(p.amount_chf) })]));
  box.innerHTML = `<div class="section-title">${esc(t("payment.listTitle"))}</div>${paid.join("")}${received.join("")}`;
  box.hidden = false;
}

{
  const show = PAGES.account.onShow;
  PAGES.account.onShow = (...args) => {
    if (show) show(...args);
    renderPaymentHistory();
  };
  if (document.getElementById("page-account").classList.contains("active")) renderPaymentHistory();
}
