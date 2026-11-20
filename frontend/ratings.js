"use strict";

// ---------------------------------------------------------------------
// Ratings after a ride and reports of a problem (backend/ratings.py,
// reports.py, see docs/ratings-and-reports.md): the stars asked for in the
// inbox once a ride is over, a person's rating next to their name, and
// "Report a problem" on a booking. Loaded after app.js and bookings.js,
// whose lists of extras it adds to.
// ---------------------------------------------------------------------

// "★ 4.8 (12)": the average of someone's stars as a driver or as a
// passenger, and how many ratings; nothing without ratings yet.
function ratingBadge(rating, role) {
  if (!rating) return "";
  const average = rating.average.toLocaleString(I18N.locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const hint = role === "driver" ? t("rating.hintDriver") : t("rating.hintPassenger");
  return `<span class="badge rating" title="${esc(hint)}">${esc(t("rating.badge", { average, count: rating.count }))}</span>`;
}

// On search results and the list of drives: the driver's rating.
CARD_EXTRAS.push((kind, item, card) => {
  if (kind === "mine" || !item.driver_rating) return;
  // Next to the driver's name on a match, to the route on a drive.
  card.querySelector(".match-name, .route")?.insertAdjacentHTML("beforeend", ratingBadge(item.driver_rating, "driver"));
});

// On a booking: the other side's rating, and "Report a problem".
BOOKING_EXTRAS.push((booking, element, otherRole) => {
  const rating = otherRole === "driver" ? booking.drive.driver_rating : booking.passenger_rating;
  const where = element.querySelector(otherRole === "driver" ? ".route" : ".booking-who");
  where?.insertAdjacentHTML("beforeend", ratingBadge(rating, otherRole));
  const name = otherRole === "driver" ? booking.drive.driver_name : booking.passenger_name;
  element.appendChild(reportLink(booking.id, name));
});

// ---------------------------------------------------------------------
// Reporting the other side of a booking to the team
// ---------------------------------------------------------------------
function reportLink(bookingId, name) {
  const holder = document.createElement("div");
  holder.className = "report-box";
  // Clicks here aren't clicks on the card around it.
  holder.addEventListener("click", (e) => e.stopPropagation());
  holder.appendChild(actionButton("report.link", "link-btn report-link", () => showReportForm(holder, bookingId, name)));
  return holder;
}

function showReportForm(holder, bookingId, name) {
  const reasons = [
    ["no_show", t("report.reasonNoShow")], ["late", t("report.reasonLate")], ["unsafe", t("report.reasonUnsafe")],
    ["behaviour", t("report.reasonBehaviour")], ["other", t("report.reasonOther")],
  ];
  holder.innerHTML = `
    <div class="report-title">${esc(t("report.title"))}</div>
    <select aria-label="${esc(t("report.title"))}">${reasons.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("")}</select>
    <input type="text" maxlength="1000" placeholder="${esc(t("report.details"))}" aria-label="${esc(t("report.details"))}" />
    <div class="hint">${esc(t("report.hint", { name }))}</div>
    <div class="booking-form-row booking-buttons"></div>
    <div class="status-msg"></div>
  `;
  const send = actionButton("report.send", "submit-btn", async () => {
    send.disabled = true;
    try {
      await postJson(`/api/bookings/${bookingId}/report`, {
        reason: holder.querySelector("select").value,
        details: holder.querySelector("input").value.trim() || null,
      });
      holder.innerHTML = `<div class="hint">${esc(t("report.sent"))}</div>`;
    } catch (err) {
      send.disabled = false;
      const status = holder.querySelector(".status-msg");
      status.textContent = err.message;
      status.className = "status-msg show err";
    }
  });
  holder.querySelector(".booking-buttons").append(
    send,
    actionButton("booking.notNow", "link-btn", () => holder.replaceWith(reportLink(bookingId, name))),
  );
}

// ---------------------------------------------------------------------
// The inbox's "how was the ride?" once a ride is over: five stars, given
// with one click, and "Report a problem" for the rest.
// ---------------------------------------------------------------------
NOTIFICATION_CARDS.rate_ride = (n, card) => {
  const details = n.details || {};
  const drive = details.drive || {};
  const title = details.rated_role === "driver"
    ? t("rating.askDriver", { name: details.rated_name })
    : t("rating.askPassenger", { name: details.rated_name });
  card.innerHTML = `
    <div class="route">${esc(title)}</div>
    <div class="meta">${esc(drive.origin_name)} → ${esc(drive.destination_name)} · ${fmtTime(drive.departure_time)}</div>
    <div class="rating-stars"></div>
    <div class="status-msg"></div>
  `;
  const stars = card.querySelector(".rating-stars");
  const rated = () => {
    stars.innerHTML = `<span class="hint">${esc(t("rating.done"))}</span>`;
  };
  if (n.action) rated();
  else {
    for (let count = 1; count <= 5; count++) {
      const star = document.createElement("button");
      star.type = "button";
      star.className = "rating-star";
      star.textContent = "★".repeat(count);
      star.setAttribute("aria-label", t("rating.stars", { count }));
      star.title = t("rating.stars", { count });
      star.addEventListener("click", async () => {
        try {
          await postJson(`/api/bookings/${details.booking_id}/rating`, { stars: count });
        } catch (err) {
          const status = card.querySelector(".status-msg");
          status.textContent = err.message;
          status.className = "status-msg show err";
          return;
        }
        await actOn(n, "view", false);  // answered: it no longer asks
        rated();
      });
      stars.appendChild(star);
    }
  }
  card.appendChild(reportLink(details.booking_id, details.rated_name));
};
