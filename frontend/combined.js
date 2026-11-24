"use strict";

// ---------------------------------------------------------------------
// A ride for part of the way and public transport for the rest
// (docs/combined-trips.md): the search's "combined_matches", shown under the
// drives that make the whole trip. A card starts with the whole trip (its
// times, how long it takes, the ride and the ticket together), then says how
// it goes, in order: who drives the passenger, and public transport before
// that, after it, or both ("transit_ride_transit": a drive that is near
// neither end of the trip). A click opens its details: the trip's steps,
// once, with their times and what belongs to each (the ride: where the car
// passes and the seat's price; a connection: its lines, its ticket and the
// link to it).
// Loaded after app.js, whose results, cards and map it uses.
// ---------------------------------------------------------------------

// A trip quicker than public transport alone by at least this says so on its
// card. One that isn't says nothing about it.
const QUICKER_WORTH_SAYING_MIN = 5;

function quickerThanTransit(minutes) {
  if (typeof minutes !== "number" || minutes > -QUICKER_WORTH_SAYING_MIN) return "";
  return t("combined.vsFaster", { duration: fmtMinutes(-minutes) });
}

// What the whole trip costs: the ride's seat and the tickets of its public
// transport legs (2nd class, full fare; the details give the half fare).
// `total`: the amount, or null without a ticket's price. `parts`: the two
// amounts it is made of ("ride CHF 4 + train ≈ CHF 20").
function combinedCost(m, legs) {
  const fares = legs.map((leg) => leg.fare);
  if (typeof m.price_chf !== "number" || !fares.every((fare) => fare && typeof fare.full_chf === "number")) return null;
  const train = fares.reduce((sum, fare) => sum + fare.full_chf, 0);
  const approx = fares.some((fare) => fare.approximate) ? "Approx" : "";
  return {
    total: t(`price.amount${approx}`, { price: fmtChf(m.price_chf + train) }),
    parts: t(`price.rideAndTrain${approx}`, { ride: fmtChf(m.price_chf), train: fmtChf(train) }),
  };
}

// On the card's first line: that amount, and under it what the train alone
// costs for the whole trip (the search's "transit_alone_fare"), to compare.
// Not known: what the amount is made of. Without a ticket's price, the
// ride's alone, saying so.
function combinedPrice(m, cost) {
  if (typeof m.price_chf !== "number") return "";
  if (!cost) return tripPrice(t("price.amount", { price: fmtChf(m.price_chf) }), t("price.withoutTicket"));
  const alone = m.transit_alone_fare;
  if (!alone || typeof alone.full_chf !== "number") return tripPrice(cost.total, cost.parts);
  return tripPrice(cost.total, t(alone.approximate ? "price.trainAloneApprox" : "price.trainAlone", { price: fmtChf(alone.full_chf) }));
}

// The way between the passenger's own end of the trip and the car's, on
// foot: the trip's times count it when the search gives it ("walk_min", on
// the pickup of a ride first and the drop-off of a ride last: an estimate,
// not a route). `kind`: "pickup" or "dropoff"; `own`: the passenger's start
// or destination.
const WALK_ICON = '<svg class="pt-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="13" cy="4.5" r="1.6"/><path d="M13 8.5l-3 3.5 3 3v5.5M10 12l-.8 3.5L7 20.5M13 8.5l2.5 3 3 1M10 9.5l-3 1.5v3"/></svg>';

// As a step of the details: when to leave (or when the passenger is there),
// about how long it takes, how far the car passes, and a Google Maps
// directions link (as a match's details, app.js: onwardLine).
function walkStep(kind, m, own) {
  const point = m[kind];
  if (!point.walk_min) return "";
  const [from, to] = kind === "pickup" ? [own, point] : [point, own];
  const [starts, ends] = kind === "pickup" ? [m.starts, point.time] : [point.time, m.arrives];
  const url = `https://www.google.com/maps/dir/?api=1&origin=${from.lat},${from.lon}&destination=${to.lat},${to.lon}&travelmode=walking`;
  return tripStep(WALK_ICON, starts, ends,
    `${esc(t(kind === "pickup" ? "combined.walkToPickup" : "combined.walkFromDropoff"))}
    <span class="combined-gap">${esc(t("combined.walkAbout", { duration: fmtMinutes(point.walk_min) }))} · ${esc(gapsText({ pickup: {}, dropoff: {}, [kind]: point }))}
    <a class="directions-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(t("match.directions"))}</a></span>`);
}

// On the card, beyond a short walk (FAR_GAP_KM): "20 min on foot to the
// pickup (1.3 km)", so the trip's first time isn't taken for the car's.
function walkNote(kind, m) {
  const point = m[kind];
  if (!(point.gap_km > FAR_GAP_KM)) return "";
  const key = kind === "pickup" ? "combined.pickupWalk" : "combined.dropoffWalk";
  return `<div class="trip-gap">${esc(t(key, { duration: fmtMinutes(point.walk_min), km: fmtNumber(point.gap_km) }))}</div>`;
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

  // Beyond a short walk, the card says how far the car's end of the trip is
  // from the passenger's own (a ride between two legs has no such end).
  const carEnd = { pickup: {}, dropoff: {}, ...(between ? {} : rideFirst ? { pickup: m.pickup } : { dropoff: m.dropoff }) };
  // That end, when the trip's times count the walk to or from the car: the
  // walk is then a step of its own, which says how far.
  const ownEnd = rideFirst ? "pickup" : "dropoff";
  const walked = !between && m[ownEnd].walk_min ? ownEnd : "";

  // In the details, the steps by their stops' full names. The ride: how far
  // the car's end of the trip is from the passenger's own, and the seat's price.
  const byCar = between ? t("combined.carBetween", { from: m.change_before.name, to: m.change_after.name })
    : t(rideFirst ? "combined.carTo" : "combined.carFrom", { place: m.change.name });
  const about = [walked ? "" : gapsText(carEnd), typeof m.price_chf === "number" ? t("price.perSeat", { price: fmtChf(m.price_chf) }) : ""];
  const ride = tripStep(CAR_ICON, m.pickup.time, m.dropoff.time,
    `${esc(byCar)}<span class="combined-gap">${esc(about.filter(Boolean).join(" · "))}</span>`);
  // A connection: how long it takes and its lines, then its ticket and the link to it.
  const transitStep = (leg) => (leg ? tripStep(TRANSIT_ICON, leg.connection.departure, leg.connection.arrival,
    `${esc(t("combined.transitTo", { place: leg.to_station }))}
    <span class="combined-gap">${esc(connectionLength(leg.connection))} · ${esc(connectionLines(leg.connection))}</span>
    <span class="combined-gap">${leg.fare ? `${esc(fareText(leg.fare))} ` : ""}${ticketsLink(leg.connection)}</span>`,
  ) : "");

  // On the card: who drives the passenger and to where, in the trip's order,
  // the stops by their towns. After public transport the place was just
  // named: "on from there".
  const part = (leg) => (leg ? transitPart(leg.to_station) : "");
  const drives = whoDrives(m, between ? { to: townOf(m.change_after.name) } : rideFirst ? { to: townOf(m.change.name) } : { on: true });

  const card = document.createElement("div");
  card.className = `match-card combined-card clickable${m.is_demo ? " demo" : ""}`;
  const quicker = quickerThanTransit(m.vs_transit_min);
  const cost = combinedCost(m, legs);
  card.innerHTML = `
    ${tripLine(m.starts, m.arrives, m.total_min, combinedPrice(m, cost))}
    ${tripHow([part(before), drives, part(after)])}
    ${walked ? walkNote(walked, m) : farGapsNote(carEnd)}
    ${quicker ? `<span class="score">${esc(quicker)}</span>` : ""}
    ${demoNote(m)}
    <div class="match-details" hidden>
      <div class="combined-steps">${walked === "pickup" ? walkStep("pickup", m, origin) : ""}${transitStep(before)}${ride}${transitStep(after)}${walked === "dropoff" ? walkStep("dropoff", m, destination) : ""}</div>
      <div class="meta">${esc(m.origin_name)} → ${esc(m.destination_name)} · ${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))} · ${esc(t("match.rideKm", { km: fmtNumber(m.ride_km) }))}</div>
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
