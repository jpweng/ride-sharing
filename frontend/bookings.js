"use strict";

// ---------------------------------------------------------------------
// Bookings (backend/bookings.py, see docs/booking.md): asking for a seat
// from a search result or the list of drives, the passenger's bookings on
// the Passenger page, the driver's passengers and requests on each of
// their drives, and the inbox's booking notifications. Loaded after
// app.js, whose map, pages and helpers it uses; it adds to the cards
// through app.js's CARD_EXTRAS and NOTIFICATION_CARDS.
// ---------------------------------------------------------------------

// GET /api/bookings: the user's bookings as a passenger and as a driver.
// Reloaded each time a page that shows them opens, and after each action.
const NO_BOOKINGS = { as_passenger: [], as_driver: [] };
let bookingsData = NO_BOOKINGS;
let bookingsError = "";
let bookingsLoaded = Promise.resolve();

function loadBookings() {
  bookingsLoaded = (signedIn() ? fetchJson("/api/bookings") : Promise.resolve(NO_BOOKINGS))
    .then((data) => {
      bookingsData = data;
      bookingsError = "";
    })
    .catch((err) => {
      bookingsData = NO_BOOKINGS;
      bookingsError = err.message;
    });
  return bookingsLoaded;
}

// Scripts loaded after this one (ratings.js) add to the cards of bookings:
// each function gets the booking, its element, and the role of the OTHER
// side ("driver" on the passenger's own bookings, "passenger" on a
// driver's passengers and requests).
const BOOKING_EXTRAS = [];

function bookingExtras(booking, element, otherRole) {
  BOOKING_EXTRAS.forEach((extra) => extra(booking, element, otherRole));
}

function bookingStatusText(status) {
  if (status === "requested") return t("booking.statusRequested");
  if (status === "accepted") return t("booking.statusAccepted");
  if (status === "declined") return t("booking.statusDeclined");
  return t("booking.statusCancelledByDriver");
}

function bookingStatusBadge(status) {
  return `<span class="badge booking-status ${esc(status)}">${esc(bookingStatusText(status))}</span>`;
}

// What the passenger reads about their own booking.
function myBookingText(status) {
  if (status === "requested") return t("booking.mineRequested");
  if (status === "accepted") return t("booking.mineAccepted");
  if (status === "declined") return t("booking.mineDeclined");
  return t("booking.mineCancelledByDriver");
}

function showBoxError(box, message) {
  const status = box.querySelector(".status-msg");
  status.textContent = message;
  status.className = "status-msg show err";
}

// The drive's route with where the passenger gets in and out.
async function showBookingOnMap(b) {
  await showDriveOnMap({ ...b.drive });
  const points = [
    [b.pickup, t("map.pickupPoint", { time: fmtClock(b.pickup.time) }), "ride-origin"],
    [b.dropoff, t("map.dropoffPoint", { time: fmtClock(b.dropoff.time) }), "ride-destination"],
  ];
  points.forEach(([point, label, role]) => {
    L.circleMarker([point.lat, point.lon], {
      radius: 7, color: PICK_COLORS[role], fillColor: "#fff", fillOpacity: 1, weight: 3,
    })
      .addTo(resultsLayer)
      .bindTooltip(esc(label), { permanent: true, direction: "top" });
  });
}

// ---------------------------------------------------------------------
// On the cards of drives
// ---------------------------------------------------------------------
CARD_EXTRAS.push((kind, item, card) => {
  const box = document.createElement("div");
  box.className = "booking-box";
  // Clicks in the box aren't clicks on the card (which shows it on the map).
  box.addEventListener("click", (e) => e.stopPropagation());
  card.appendChild(box);
  bookingsLoaded.then(() => {
    if (kind === "mine") fillPassengers(box, item);
    else fillAsk(box, kind, item);
  });
});

// A search result or a listed drive: what became of the passenger's
// request, if they made one, and "Request a seat" unless it's waiting or
// accepted (after a decline or a cancellation, they can ask again).
// Nothing on one's own drives, on demo drives (fictional), or before
// signing in.
function fillAsk(box, kind, item, sent = false) {
  box.innerHTML = "";
  if (!item.bookable) return;
  if (!signedIn()) {
    // Asking for a seat needs an account: the button leads to signing in.
    if (item.seats_available > 0) box.appendChild(actionButton("booking.signInToAsk", "submit-btn secondary", goToSignIn));
    return;
  }
  if (currentUser.is_demo || item.driver_name === currentUser.name) return;
  const mine = bookingsData.as_passenger.find((b) => b.drive_id === item.id);
  if (mine) {
    const text = sent ? t("booking.askSent") : myBookingText(mine.status);
    box.innerHTML = `<div class="hint booking-mine">${bookingStatusBadge(mine.status)} ${esc(text)}</div>`;
    if (mine.status === "requested" || mine.status === "accepted") return;
  }
  if (!(item.seats_available > 0)) return;
  box.appendChild(actionButton("booking.ask", "submit-btn secondary", () => showAskForm(box, kind, item)));
}

function showAskForm(box, kind, item) {
  const seats = [...Array(Math.min(item.seats_available, 8)).keys()].map((i) => `<option>${i + 1}</option>`).join("");
  box.innerHTML = `
    <div class="booking-form-row"${item.seats_available > 1 ? "" : " hidden"}>
      <label>${esc(t("booking.askSeats"))}</label>
      <select aria-label="${esc(t("booking.askSeats"))}">${seats}</select>
    </div>
    <input type="text" maxlength="300" placeholder="${esc(t("booking.askNote"))}" aria-label="${esc(t("booking.askNote"))}" />
    <div class="booking-form-row booking-buttons"></div>
    <div class="status-msg"></div>
  `;
  const send = actionButton("booking.askSend", "submit-btn", async () => {
    const body = {
      seats: Number(box.querySelector("select").value),
      note: box.querySelector("input").value.trim() || null,
    };
    // From a search: the passenger's own start and destination, so the
    // driver knows where to meet them. From the list: the whole drive.
    if (kind === "match" && lastResults) Object.assign(body, { origin: lastResults.origin, destination: lastResults.destination });
    // A combined trip (combined.js): the car's part of it, to or from the stop where the passenger changes.
    if (kind === "combined") Object.assign(body, { origin: item.ride_from, destination: item.ride_to });
    send.disabled = true;
    try {
      await postJson(`/api/drives/${item.id}/bookings`, body);
      await loadBookings();
      fillAsk(box, kind, item, true);
      renderMyBookings();
    } catch (err) {
      send.disabled = false;
      showBoxError(box, err.message);
    }
  });
  box.querySelector(".booking-buttons").append(
    send,
    actionButton("booking.notNow", "link-btn", () => fillAsk(box, kind, item)),
  );
}

// One of the driver's own drives: its passengers and the requests to join.
function fillPassengers(box, drive) {
  box.innerHTML = "";
  const rows = bookingsData.as_driver.filter((b) => b.drive_id === drive.id);
  if (!rows.length) return;
  box.innerHTML = `<div class="booking-list-title">${esc(t("booking.passengers"))}</div>`;
  rows.forEach((b) => box.appendChild(passengerRow(b)));
}

function passengerRow(b) {
  const row = document.createElement("div");
  row.className = "booking-row";
  const trip = t("booking.trip", { from: b.origin_name, to: b.destination_name, time: fmtClock(b.pickup.time) });
  row.innerHTML = `
    <div class="booking-who">${esc(b.passenger_name)} · ${esc(t("common.seats", { count: b.seats }))} ${bookingStatusBadge(b.status)}</div>
    <div class="meta">${esc(trip)}</div>
    ${priceBadges({ price_chf: b.price_chf })}
    ${b.passenger_note ? `<div class="booking-note">${esc(t("booking.passengerNote", { name: b.passenger_name, note: b.passenger_note }))}</div>` : ""}
    ${b.status === "requested" ? `<input type="text" maxlength="300" placeholder="${esc(t("booking.acceptNote"))}" aria-label="${esc(t("booking.acceptNote"))}" />` : ""}
    <div class="booking-form-row booking-buttons"></div>
    <div class="status-msg"></div>
  `;
  row.addEventListener("click", (e) => {
    if (!e.target.closest("input, button, select")) showBookingOnMap(b);
  });

  // After the driver's answer the drive's seats have changed: reload both.
  const act = async (action, body) => {
    try {
      await postJson(`/api/bookings/${b.id}/${action}`, body || {});
      await loadBookings();
      loadMyDrives();
    } catch (err) {
      showBoxError(row, err.message);
    }
  };
  const buttons = row.querySelector(".booking-buttons");
  if (b.status === "requested") {
    buttons.append(
      actionButton("booking.accept", "submit-btn", () => act("accept", { note: row.querySelector("input").value.trim() || null })),
      actionButton("booking.decline", "submit-btn secondary", () => act("decline")),
    );
  } else {
    buttons.append(
      actionButton("booking.cancelPassenger", "link-btn", () => {
        if (confirm(t("booking.cancelPassengerConfirm", { name: b.passenger_name }))) act("cancel");
      }),
    );
  }
  bookingExtras(b, row, "passenger");
  return row;
}

// ---------------------------------------------------------------------
// The passenger's bookings, on the Passenger page above their ride requests
// ---------------------------------------------------------------------
function myBookingsList() {
  let list = document.getElementById("ride-bookings");
  if (!list) {
    const requestsTitle = document.getElementById("ride-requests").previousElementSibling;
    requestsTitle.insertAdjacentHTML(
      "beforebegin",
      '<div class="section-title" id="ride-bookings-title"></div><div id="ride-bookings"></div>'
    );
    list = document.getElementById("ride-bookings");
  }
  document.getElementById("ride-bookings-title").textContent = t("booking.title");
  return list;
}

async function renderMyBookings() {
  const list = myBookingsList();
  await bookingsLoaded;
  if (bookingsError) {
    list.hidden = false;
    document.getElementById("ride-bookings-title").hidden = false;
    list.innerHTML = `<div class="empty-state">${esc(t("booking.loadFailed", { error: bookingsError }))}</div>`;
    return;
  }
  // The section is there when it has something.
  const none = !bookingsData.as_passenger.length;
  list.hidden = none;
  document.getElementById("ride-bookings-title").hidden = none;
  list.innerHTML = "";
  bookingsData.as_passenger.forEach((b) => list.appendChild(myBookingCard(b)));
}

function myBookingCard(b) {
  const d = b.drive;
  const active = b.status === "requested" || b.status === "accepted";
  const waitingForASeat = b.status === "requested" && d.seats_available < b.seats;
  const card = document.createElement("div");
  card.className = "drive-card clickable booking-card";
  card.innerHTML = `
    <div class="route">${esc(d.origin_name)} → ${esc(d.destination_name)} ${bookingStatusBadge(b.status)}</div>
    <div class="meta">${esc(t("common.driver", { name: d.driver_name }))} · ${fmtTime(d.departure_time)} · ${esc(t("common.seats", { count: b.seats }))}</div>
    <div class="meta">${esc(t("match.pickup", { time: fmtClock(b.pickup.time), km: fmtNumber(b.pickup.gap_km) }))}</div>
    <div class="meta">${esc(t("match.dropoff", { time: fmtClock(b.dropoff.time), km: fmtNumber(b.dropoff.gap_km) }))}</div>
    ${priceBadges({ price_chf: b.price_chf })}
    ${b.seats > 1 && typeof b.total_chf === "number" ? `<div class="meta">${esc(t("booking.total", { seats: b.seats, total: fmtChf(b.total_chf) }))}</div>` : ""}
    <div class="meta">${esc(myBookingText(b.status))}${waitingForASeat ? ` ${esc(t("booking.fullForNow"))}` : ""}</div>
    ${b.driver_note ? `<div class="booking-note">${esc(t("booking.driverNote", { note: b.driver_note }))}</div>` : ""}
    ${active ? `<div class="hint">${esc(t("booking.priceNote"))}</div>` : ""}
    ${active ? `<button type="button" class="link-btn">${esc(b.status === "requested" ? t("booking.withdraw") : t("booking.cancel"))}</button>` : ""}
    <div class="status-msg"></div>
  `;
  card.addEventListener("click", (e) => {
    if (!e.target.closest("button")) showBookingOnMap(b);
  });
  card.querySelector("button")?.addEventListener("click", async () => {
    const trip = { from: d.origin_name, to: d.destination_name };
    const question = b.status === "requested" ? t("booking.withdrawConfirm", trip) : t("booking.cancelConfirm", trip);
    if (!confirm(question)) return;
    try {
      await postJson(`/api/bookings/${b.id}/cancel`, {});
      await loadBookings();
      renderMyBookings();
      // The search results still say "requested" on that drive.
      if (lastResults) renderResults();
    } catch (err) {
      showBoxError(card, err.message);
    }
  });
  // After the cancel button is wired: extras may add buttons of their own.
  bookingExtras(b, card, "driver");
  return card;
}

// ---------------------------------------------------------------------
// The inbox's booking notifications. Their details keep the drive and the
// passenger's name, as the booking itself may be gone.
// ---------------------------------------------------------------------
function bookingNotification(title, buttonKey, page) {
  return (n, card) => {
    const details = n.details || {};
    const drive = details.drive || {};
    card.innerHTML = `
      <div class="route">${esc(title(details, drive))}</div>
      <div class="meta">${esc(drive.origin_name)} → ${esc(drive.destination_name)} · ${fmtTime(drive.departure_time)}</div>
      <div class="notification-actions"></div>
    `;
    const button = typeof buttonKey === "function" ? buttonKey(details) : buttonKey;
    if (!button) return;
    card.querySelector(".notification-actions").append(
      actionButton(button, "submit-btn secondary", () => {
        goTo(typeof page === "function" ? page(details) : page);
        if (button === "booking.inboxSeeBookings") {
          document.getElementById("ride-bookings-title")?.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      }),
    );
  };
}

NOTIFICATION_CARDS.booking_requested = bookingNotification(
  (details) => t("booking.inboxRequested", { name: details.passenger_name, count: details.seats }),
  "booking.inboxAnswer", "drive",
);
NOTIFICATION_CARDS.booking_accepted = bookingNotification(
  (details, drive) => t("booking.inboxAccepted", { name: drive.driver_name }),
  "booking.inboxSeeBookings", "ride",
);
NOTIFICATION_CARDS.booking_declined = bookingNotification(
  (details, drive) => t("booking.inboxDeclined", { name: drive.driver_name }),
  "booking.inboxSeeBookings", "ride",
);
NOTIFICATION_CARDS.booking_cancelled = bookingNotification(
  (details, drive) => {
    if (details.by === "driver") return t("booking.inboxCancelledByDriver", { name: drive.driver_name });
    return details.was === "accepted"
      ? t("booking.inboxCancelledByPassenger", { name: details.passenger_name })
      : t("booking.inboxWithdrawn", { name: details.passenger_name });
  },
  (details) => (details.by === "driver" ? "booking.inboxSeeBookings" : "booking.inboxSeeDrives"),
  (details) => (details.by === "driver" ? "ride" : "drive"),
);
// The drive is gone, and the booking with it: nothing to go and see.
NOTIFICATION_CARDS.booking_drive_cancelled = bookingNotification(
  (details, drive) =>
    details.was === "accepted"
      ? t("booking.inboxDriveCancelled", { name: drive.driver_name })
      : t("booking.inboxDriveCancelledRequested", { name: drive.driver_name }),
  null,
);

NOTIFICATION_CARDS.request_accepted = bookingNotification(
  (details, drive) => t("booking.inboxRequestAccepted", { name: drive.driver_name }),
  "booking.inboxSeeBookings", "ride",
);

// ---------------------------------------------------------------------
// Accepting a passenger's ride request directly, from the inbox or from
// the list of requests in one's areas (app.js's REQUEST_EXTRAS): the drive
// is posted and the passenger booked on it in one step, and the first
// driver to accept gets it (bookings.accept_request).
// ---------------------------------------------------------------------
const DEFAULT_SEATS_OFFERED = 3;

REQUEST_EXTRAS.push((request, actions, context) => {
  if (!signedIn() || currentUser.is_demo) return;
  // "Accept this ride" comes first; the buttons already there step back.
  actions.querySelectorAll(".submit-btn").forEach((button) => button.classList.add("secondary"));
  actions.prepend(actionButton("booking.acceptRequest", "submit-btn", () => showAcceptRequest(request, actions, context)));
});

function showAcceptRequest(request, actions, context) {
  // A driver told because they usually make a trip drives that trip, and
  // picks the passenger up on the way; otherwise it's the passenger's trip.
  const usual = context.notification?.details?.trip;
  const trip = usual
    ? { origin: usual.origin, destination: usual.destination, departure_time: context.notification.details.suggested_departure }
    : null;
  const name = request.passenger_name;
  const text = trip
    ? t("booking.acceptRequestUsual", { name, from: trip.origin.name, to: trip.destination.name, time: fmtTime(trip.departure_time) })
    : t("booking.acceptRequestTheirs", { name, from: request.origin_name, to: request.destination_name, time: fmtTime(request.leave_by || request.requested_time) });
  const seats = [...Array(8).keys()]
    .map((i) => `<option${i + 1 === DEFAULT_SEATS_OFFERED ? " selected" : ""}>${i + 1}</option>`)
    .join("");
  const panel = document.createElement("div");
  panel.className = "booking-box accept-request";
  panel.innerHTML = `
    <div class="hint">${esc(text)}</div>
    <div class="booking-form-row">
      <label>${esc(t("booking.acceptRequestSeats"))}</label>
      <select aria-label="${esc(t("booking.acceptRequestSeats"))}">${seats}</select>
    </div>
    <input type="text" maxlength="300" placeholder="${esc(t("booking.acceptNote"))}" aria-label="${esc(t("booking.acceptNote"))}" />
    <div class="booking-form-row booking-buttons"></div>
    <div class="status-msg"></div>
  `;
  const confirmButton = actionButton("booking.acceptRequestConfirm", "submit-btn", async () => {
    confirmButton.disabled = true;
    try {
      await postJson(`/api/requests/${request.id}/accept`, {
        seats_available: Number(panel.querySelector("select").value),
        note: panel.querySelector("input").value.trim() || null,
        ...(trip || {}),
      });
    } catch (err) {
      confirmButton.disabled = false;
      showBoxError(panel, err.message);
      return;
    }
    // Answered: the notification no longer waits for one.
    if (context.notification) await actOn(context.notification, "offer", false);
    panel.innerHTML = `<div class="hint">${esc(t("booking.acceptRequestDone", { name }))}</div><div class="booking-form-row booking-buttons"></div>`;
    panel.querySelector(".booking-buttons").append(actionButton("booking.inboxSeeDrives", "submit-btn secondary", () => goTo("drive")));
    // On the Driver page: the new drive is listed, the request leaves the list.
    if (document.getElementById("page-drive").classList.contains("active")) {
      await loadBookings();
      loadMyDrives();
    }
  });
  panel.querySelector(".booking-buttons").append(
    confirmButton,
    actionButton("booking.notNow", "link-btn", () => {
      panel.remove();
      actions.hidden = false;
    }),
  );
  actions.hidden = true;
  actions.after(panel);
}

// ---------------------------------------------------------------------
// Hooking into app.js's pages: the bookings are loaded before the page's
// own lists, whose cards wait for them (CARD_EXTRAS above).
// ---------------------------------------------------------------------
const BOOKING_PAGES = ["ride", "drive"];

BOOKING_PAGES.forEach((page) => {
  const show = PAGES[page].onShow;
  PAGES[page].onShow = (...args) => {
    loadBookings();
    show(...args);
    if (page === "ride") renderMyBookings();
  };
});

// app.js may have shown a page before this script ran (it routes as soon
// as it knows who is signed in): show it again, with the bookings.
{
  const shown = BOOKING_PAGES.find((page) => document.getElementById(`page-${page}`).classList.contains("active"));
  if (shown) PAGES[shown].onShow();
}
