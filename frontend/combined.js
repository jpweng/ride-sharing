"use strict";

// ---------------------------------------------------------------------
// A ride for part of the way and public transport for the rest
// (docs/combined-trips.md): the search's "combined_matches", shown under the
// drives that make the whole trip. A card starts with the whole trip (its
// times, how long it takes, the ride and the ticket together), then gives
// its steps, in order, with their times: the ride and public transport
// before it, after it, or both ("transit_ride_transit": a drive that is near
// neither end of the trip), then who drives. A click opens its details.
// Loaded after app.js, whose results, cards and map it uses.
// ---------------------------------------------------------------------

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

// What the whole trip costs: the ride's seat and the tickets of its public
// transport legs (2nd class, full fare; the details give the half fare),
// with the two amounts under it. Without a ticket's price, the ride's alone,
// saying so.
function combinedPrice(m, legs) {
  if (typeof m.price_chf !== "number") return "";
  const fares = legs.map((leg) => leg.fare);
  if (!fares.every((fare) => fare && typeof fare.full_chf === "number")) {
    return tripPrice(t("price.amount", { price: fmtChf(m.price_chf) }), t("price.withoutTicket"));
  }
  const train = fares.reduce((sum, fare) => sum + fare.full_chf, 0);
  const approx = fares.some((fare) => fare.approximate) ? "Approx" : "";
  return tripPrice(
    t(`price.amount${approx}`, { price: fmtChf(m.price_chf + train) }),
    t(`price.rideAndTrain${approx}`, { ride: fmtChf(m.price_chf), train: fmtChf(train) }),
  );
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
  const between = m.shape === "transit_ride_transit";
  const rideFirst = m.shape === "ride_then_transit";
  // Public transport before the car, after it, or both.
  const before = between ? m.transit_before : rideFirst ? null : m.transit;
  const after = between ? m.transit_after : rideFirst ? m.transit : null;
  const legs = [before, after].filter(Boolean);
  // The car's part, for asking for a seat on it (bookings.js): to or from
  // the stop where the passenger changes, or between the two.
  m.ride_from = between ? m.change_before : rideFirst ? origin : m.change;
  m.ride_to = between ? m.change_after : rideFirst ? m.change : destination;

  let byCar;
  if (between) {
    byCar = esc(t("combined.carBetween", { from: m.change_before.name, to: m.change_after.name }));
  } else {
    // How far the car's end of the trip is from the passenger's own, unless it is there.
    const end = rideFirst ? m.pickup : m.dropoff;
    const gap = end.gap_km > 0 ? t(rideFirst ? "combined.pickupGap" : "combined.dropoffGap", { km: fmtNumber(end.gap_km) }) : "";
    byCar = `${esc(t(rideFirst ? "combined.carTo" : "combined.carFrom", { place: m.change.name }))}${
      gap ? `<span class="combined-gap">${esc(gap)}</span>` : ""
    }`;
  }
  const ride = combinedStep(CAR_ICON, m.pickup.time, m.dropoff.time, byCar);
  const transitStep = (leg) => (leg ? combinedStep(
    TRANSIT_ICON, leg.connection.departure, leg.connection.arrival,
    esc(t("combined.transitTo", { place: leg.to_station })),
  ) : "");

  const card = document.createElement("div");
  card.className = `match-card combined-card clickable${m.is_demo ? " demo" : ""}`;
  const quicker = quickerThanTransit(m.vs_transit_min);
  card.innerHTML = `
    ${tripLine(m.starts, m.arrives, m.total_min, combinedPrice(m, legs))}
    <div class="combined-steps">${transitStep(before)}${ride}${transitStep(after)}</div>
    ${quicker ? `<span class="score">${esc(quicker)}</span>` : ""}
    ${driverLine(m)}
    <div class="match-details" hidden>
      <div class="meta">${esc(m.origin_name)} → ${esc(m.destination_name)} · ${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))} · ${esc(t("match.rideKm", { km: fmtNumber(m.ride_km) }))}</div>
      ${legs.map((leg) => `<div class="last-mile"><strong>${esc(leg.from_station)} → ${esc(leg.to_station)}</strong>
        ${renderFare(leg.fare)}${renderConnections([leg.connection])}</div>`).join("")}
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
    if (e.target.closest(".match-on-map")) return showCombinedOnMap(m, origin, destination);
    details.hidden = !details.hidden;
    card.classList.toggle("open", !details.hidden);
    more.setAttribute("aria-expanded", String(!details.hidden));
  });
  return card;
}

RESULT_SECTIONS.push((data, origin, destination) => {
  const container = document.getElementById("ride-combined");
  // Still being looked for (app.js fills them in when they arrive): said under
  // the drives found. Without any, the page says it is searching as it is.
  if (data.combined_matches === STILL_SEARCHING) {
    container.innerHTML = data.driver_matches.length ? `<div class="hint combined-searching">${esc(t("combined.searching"))}</div>` : "";
    return;
  }
  const trips = data.combined_matches || [];
  container.innerHTML = trips.length ? `<div class="section-title">${esc(t("combined.title"))}</div>` : "";
  trips.forEach((m) => container.appendChild(combinedCard(m, origin, destination)));
});
