"use strict";

// ---------------------------------------------------------------------
// A ride for part of the way and public transport for the rest
// (docs/combined-trips.md): the search's "combined_matches", shown under the
// drives that make the whole trip. A card says who drives and gives the
// trip's two steps, in order, with their times; a click opens its details.
// Loaded after app.js, whose results, cards and map it uses.
// ---------------------------------------------------------------------
const CAR_ICON = '<svg class="pt-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 16.5v-4l1.8-4.6a2 2 0 0 1 1.9-1.4h7.6a2 2 0 0 1 1.9 1.4l1.8 4.6v4M3.5 12.5h17M7 16.5v2M17 16.5v2M7.5 14.6h.01M16.5 14.6h.01"/></svg>';

// A trip quicker than public transport alone by at least this says so on its
// card. One that isn't says nothing about it.
const QUICKER_WORTH_SAYING_MIN = 5;

function combinedStep(icon, from, to, text) {
  return `<div class="combined-step">
    <span class="combined-time">${fmtClock(from)} → ${fmtClock(to)}</span>
    <span class="combined-what">${icon}<span>${text}</span></span>
  </div>`;
}

function quickerThanTransit(minutes) {
  if (typeof minutes !== "number" || minutes > -QUICKER_WORTH_SAYING_MIN) return "";
  return t("combined.vsFaster", { duration: fmtMinutes(-minutes) });
}

// The car's part of a combined trip on the map: its route, where the
// passenger gets in and out, and their own two ends.
function showCombinedOnMap(m, origin, destination) {
  resultsLayer.clearLayers();
  const bounds = L.latLngBounds([[origin.lat, origin.lon], [destination.lat, destination.lon]]);
  if (m.route_geometry && m.route_geometry.length) {
    L.polyline(m.route_geometry.map(([lon, lat]) => [lat, lon]), { color: "#1a73e8", weight: 5, opacity: 0.8 }).addTo(resultsLayer);
  }
  [[origin, "map.pickupTitle"], [destination, "map.destinationTitle"]].forEach(([p, key]) => {
    L.marker([p.lat, p.lon], { title: t(key) }).addTo(resultsLayer).bindPopup(esc(p.name));
  });
  [[m.pickup, "map.pickupPoint", "ride-origin"], [m.dropoff, "map.dropoffPoint", "ride-destination"]].forEach(([point, key, role]) => {
    L.circleMarker([point.lat, point.lon], { radius: 7, color: PICK_COLORS[role], fillColor: "#fff", fillOpacity: 1, weight: 3 })
      .addTo(resultsLayer)
      .bindTooltip(esc(t(key, { time: fmtClock(point.time) })), { permanent: true, direction: "top" });
    bounds.extend([point.lat, point.lon]);
  });
  map.fitBounds(bounds, { padding: [30, 30] });
  revealMap();
}

function combinedCard(m, origin, destination) {
  const rideFirst = m.shape === "ride_then_transit";
  const connection = m.transit.connection;
  // The car's part, for asking for a seat on it (bookings.js): to or from
  // the stop where the passenger changes.
  m.ride_from = rideFirst ? origin : m.change;
  m.ride_to = rideFirst ? m.change : destination;

  const gap = rideFirst
    ? t("combined.pickupGap", { km: fmtNumber(m.pickup.gap_km) })
    : t("combined.dropoffGap", { km: fmtNumber(m.dropoff.gap_km) });
  const ride = combinedStep(
    CAR_ICON, m.pickup.time, m.dropoff.time,
    `${esc(t(rideFirst ? "combined.carTo" : "combined.carFrom", { place: m.change.name }))}<span class="combined-gap">${esc(gap)}</span>`,
  );
  const transit = combinedStep(
    TRANSIT_ICON, connection.departure, connection.arrival,
    esc(t("combined.transitTo", { place: m.transit.to_station })),
  );

  const card = document.createElement("div");
  card.className = `match-card combined-card clickable${m.is_demo ? " demo" : ""}`;
  card.innerHTML = `
    <div class="match-head">
      ${driverAvatar(m)}
      <div class="match-who">
        <div class="match-name">${esc(m.driver_name || t("match.driver"))}${demoBadge(m)}</div>
        <div class="meta">${esc(m.origin_name)} → ${esc(m.destination_name)}</div>
      </div>
    </div>
    <div class="combined-steps">${rideFirst ? ride + transit : transit + ride}</div>
    <div class="meta combined-total">${esc(
      [t("combined.total", { duration: fmtMinutes(m.total_min) }), quickerThanTransit(m.vs_transit_min)].filter(Boolean).join(" · ")
    )}</div>
    ${priceBadges(m)}
    <div class="match-details" hidden>
      <div class="meta">${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))} · ${esc(t("match.rideKm", { km: fmtNumber(m.ride_km) }))}</div>
      <div class="last-mile"><strong>${esc(m.transit.from_station)} → ${esc(m.transit.to_station)}</strong>
        ${renderFare(m.transit.fare)}${renderConnections([connection])}</div>
      <button type="button" class="link-btn match-on-map">${esc(t("match.showOnMap"))}</button>
    </div>
  `;
  cardExtras("combined", m, card);

  const details = card.querySelector(".match-details");
  const profile = card.querySelector(":scope > .profile-link-box");
  if (profile) details.appendChild(profile);
  const more = document.createElement("button");
  more.type = "button";
  more.className = "link-btn match-more";
  more.textContent = t("match.details");
  more.setAttribute("aria-expanded", "false");
  card.appendChild(more);
  card.appendChild(details);
  card.addEventListener("click", (e) => {
    if (e.target.closest("a")) return; // ticket links
    if (e.target.closest(".match-on-map")) {
      // As for any ride: a demo one is fictional, and says so instead.
      return m.is_demo ? showNotice(t("match.demoTitle"), t("match.demoText")) : showCombinedOnMap(m, origin, destination);
    }
    details.hidden = !details.hidden;
    card.classList.toggle("open", !details.hidden);
    more.setAttribute("aria-expanded", String(!details.hidden));
  });
  return card;
}

RESULT_SECTIONS.push((data, origin, destination) => {
  const container = document.getElementById("ride-combined");
  const trips = data.combined_matches || [];
  container.innerHTML = trips.length ? `<div class="section-title">${esc(t("combined.title"))}</div>` : "";
  trips.forEach((m) => container.appendChild(combinedCard(m, origin, destination)));
});
