"use strict";

// ---------------------------------------------------------------------
// Paying for a seat (backend/payments.py, see docs/payments.md): "Pay" on
// the passenger's accepted booking, which leads to Stripe's payment page
// and back, what the driver sees of it, and the inbox's notifications
// about payments. Loaded after app.js and bookings.js, whose lists of
// extras it adds to. A booking carries a `payment` only when seats are paid
// through the site: without one, nothing here shows.
// ---------------------------------------------------------------------

function paymentBadge(key, className) {
  return `<span class="badge payment ${className}">${esc(t(key))}</span>`;
}

BOOKING_EXTRAS.push((booking, element, otherRole) => {
  const payment = booking.payment;
  if (!payment) return;
  if (otherRole === "driver") myPayment(booking, payment, element);
  else if (booking.status === "accepted") {
    // The driver, on a passenger of theirs: paid, or still to pay.
    const badge = payment.status === "paid" ? paymentBadge("payment.paid", "paid") : paymentBadge("payment.waiting", "waiting");
    element.querySelector(".booking-who")?.insertAdjacentHTML("beforeend", ` ${badge}`);
  }
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
  if (payment.status === "paid") {
    card.querySelector(".route")?.insertAdjacentHTML("beforeend", ` ${paymentBadge("payment.paid", "paid")}`);
    lines.push(t("payment.paidAmount", { amount: fmtChf(payment.amount_chf) }));
  }
  if (payment.refunded_chf > 0) lines.push(t("payment.refunded", { amount: fmtChf(payment.refunded_chf) }));
  const due = payment.status === "due" && booking.status === "accepted" && typeof payment.amount_chf === "number";
  if (due) lines.push(t("payment.payBy", { time: fmtTime(payment.pay_by) }));
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
  (details) => t("payment.inboxRefunded", { amount: fmtChf(details.amount_chf || 0) }),
  null,
);

// A booking cancelled because it wasn't paid in time says so to the driver.
{
  const cancelled = NOTIFICATION_CARDS.booking_cancelled;
  const notPaid = bookingNotification(
    (details) => t("payment.inboxNotPaid", { name: details.passenger_name }),
    "booking.inboxSeeDrives", "drive",
  );
  NOTIFICATION_CARDS.booking_cancelled = (n, card) => ((n.details || {}).reason === "unpaid" ? notPaid : cancelled)(n, card);
}
