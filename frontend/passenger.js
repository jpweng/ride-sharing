"use strict";

// ---------------------------------------------------------------------
// The passenger page (docs/passenger-page.md): one card with two fields,
// From and To. Once both are set the search runs by itself, for now; the
// time shows with the results and can be changed. Loaded after app.js,
// whose map, points, fields and search (findRide) it uses.
// ---------------------------------------------------------------------
const RIDE_ROLES = ["ride-origin", "ride-destination"];

// How many of the passenger's recent places an empty field offers.
const RECENT_PLACES_SHOWN = 4;

// The places of the passenger's recent searches (app.js: recentSearches),
// the latest first; for From their starts come first, for To their
// destinations. Not the place already in the other field.
function recentPlaces(role) {
  const ends = role === "ride-origin" ? ["origin", "destination"] : ["destination", "origin"];
  const other = points[RIDE_ROLES.find((r) => r !== role)];
  const places = [];
  ends.forEach((end) => {
    recentSearches.forEach((r) => {
      const place = { label: r[`${end}_name`], lat: r[`${end}_lat`], lon: r[`${end}_lon`] };
      const same = (p) => p.label === place.label || (Math.abs(p.lat - place.lat) < 1e-4 && Math.abs(p.lon - place.lon) < 1e-4);
      if (other && same({ label: other.name, lat: other.lat, lon: other.lon })) return;
      if (!places.some(same)) places.push(place);
    });
  });
  return places.slice(0, RECENT_PLACES_SHOWN).map((place) => ({ ...place, detail: t("ride.recentPlace"), kind: "point" }));
}

// What a field offers besides the places found: recent places while it's
// empty, and picking the place on the map, which opens the map.
RIDE_ROLES.forEach((role) => {
  FIELD_SUGGESTIONS[role] = (query) => [
    ...(query.trim() ? [] : recentPlaces(role)),
    {
      label: t("form.pickOnMap"),
      action: () => {
        document.getElementById(`name-${role}`).blur(); // the place picked gives the field its name
        startPicking(role);
      },
    },
  ];
  // A field left with other words than its place's name (emptied, half
  // typed, the map closed without picking) shows that place again: it is
  // still the one the search uses.
  document.getElementById(`name-${role}`).addEventListener("blur", (e) => {
    if (points[role] && e.target.value.trim() !== points[role].name) e.target.value = points[role].name;
  });
});

// From and To change places.
document.getElementById("ride-swap").addEventListener("click", () => {
  const [from, to] = RIDE_ROLES.map((role) => getPoint(role, `name-${role}`));
  const [fromInput, toInput] = RIDE_ROLES.map((role) => document.getElementById(`name-${role}`));
  [fromInput.value, toInput.value] = [toInput.value, fromInput.value];
  RIDE_ROLES.forEach(clearPoint);
  if (to) setPointCoords("ride-origin", to.lat, to.lon, to.name, to.kind);
  if (from) setPointCoords("ride-destination", from.lat, from.lon, from.name, from.kind);
});

// ---------------------------------------------------------------------
// The search starts by itself once both ends are set, and again when the time
// or what it stands for is changed. It is for now (to arrive by a time: in
// an hour), unless the passenger chose a time that is still ahead.
// ---------------------------------------------------------------------
let rideSearchTimer = null;

// "Arrive by" without a time chosen: an hour from now (nobody arrives by now).
const ARRIVE_BY_DEFAULT_MS = 60 * 60 * 1000;

function searchSoon(delayMs = 50) {
  clearTimeout(rideSearchTimer);
  rideSearchTimer = setTimeout(() => {
    const ready = RIDE_ROLES.every((role) => points[role]);
    if (!ready || !document.getElementById("page-ride").classList.contains("active")) return;
    if (!rideTimeChosen || !(new Date(getDateTime("ride")) > new Date())) {
      rideTimeChosen = false;
      const arriving = document.getElementById("ride-time-kind").value === "arrive";
      setDateTime("ride", new Date(Date.now() + (arriving ? ARRIVE_BY_DEFAULT_MS : 0)));
    }
    document.getElementById("ride-when").hidden = false;
    rememberTrip();
    findRide();
  }, delayMs);
}

window.addEventListener("point:set", (e) => {
  if (RIDE_ROLES.includes(e.detail.role)) searchSoon();
});

// Signed in: the search again, now with the drivers' names. Signed out (or
// the account deleted): the trip goes, from the page and from the browser.
window.addEventListener("user:changed", () => (currentUser ? searchSoon() : clearTrip()));

// Leave at / Arrive by: the same time now stands for the other end of the trip.
document.getElementById("ride-time-kind").addEventListener("change", () => searchSoon());

["ride-date", "ride-hour", "ride-minute"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    rideTimeChosen = true;
    searchSoon(400); // the hour, then the minutes: one search
  });
});

// ---------------------------------------------------------------------
// The last trip searched is remembered in this browser, on the device only,
// for TRIP_KEPT_MS: the search page comes back with it after a reload, and
// in the tab the emailed sign-in link opens. Signing out forgets it. The
// privacy page says so.
// ---------------------------------------------------------------------
const TRIP_STORAGE_KEY = "rideandrail.trip";
const TRIP_KEPT_MS = 24 * 60 * 60 * 1000;

function rememberTrip() {
  const [origin, destination] = RIDE_ROLES.map((role) => getPoint(role, `name-${role}`));
  const trip = {
    saved: Date.now(),
    origin,
    destination,
    kind: document.getElementById("ride-time-kind").value,
    time: rideTimeChosen ? getDateTime("ride") : null, // otherwise the search is for now
  };
  try {
    localStorage.setItem(TRIP_STORAGE_KEY, JSON.stringify(trip));
  } catch {
    // storage unavailable (private window etc.): the trip just isn't remembered
  }
}

function forgetTrip() {
  try {
    localStorage.removeItem(TRIP_STORAGE_KEY);
  } catch {
    // nothing was kept
  }
}

// The trip remembered, if it is still fresh and looks like one.
function rememberedTrip() {
  const place = (p) => p && typeof p.name === "string" && p.name.length <= 200
    && Number.isFinite(p.lat) && Math.abs(p.lat) <= 90 && Number.isFinite(p.lon) && Math.abs(p.lon) <= 180;
  try {
    const trip = JSON.parse(localStorage.getItem(TRIP_STORAGE_KEY));
    if (trip && Date.now() - trip.saved < TRIP_KEPT_MS && place(trip.origin) && place(trip.destination)) return trip;
  } catch {
    // unreadable: as if there were none
  }
  forgetTrip();
  return null;
}

// Into the empty search page: the trip remembered, which then searches.
function restoreTrip() {
  const inputs = RIDE_ROLES.map((role) => document.getElementById(`name-${role}`));
  if (RIDE_ROLES.some((role) => points[role]) || inputs.some((input) => input.value.trim())) return;
  const trip = rememberedTrip();
  if (!trip) return;
  document.getElementById("ride-time-kind").value = trip.kind === "arrive" ? "arrive" : "depart";
  if (typeof trip.time === "string" && new Date(trip.time) > new Date()) {
    setDateTime("ride", new Date(trip.time));
    rideTimeChosen = true;
  }
  [trip.origin, trip.destination].forEach((p, i) => {
    inputs[i].value = p.name;
    setPointCoords(RIDE_ROLES[i], p.lat, p.lon, p.name, ["place", "address"].includes(p.kind) ? p.kind : "point");
  });
}

// The search page as when first opened.
function clearTrip() {
  forgetTrip();
  clearTimeout(rideSearchTimer);
  RIDE_ROLES.forEach((role) => {
    clearPoint(role);
    document.getElementById(`name-${role}`).value = "";
  });
  rideTimeChosen = false;
  document.getElementById("ride-time-kind").value = "depart";
  searchNumber++; // an answer still on its way isn't shown
  lastResults = null;
  ["ride-when", "ride-results"].forEach((id) => (document.getElementById(id).hidden = true));
  hideStatus("ride-status");
  resultsLayer.clearLayers();
}

// Hooking into app.js's page: the remembered trip comes back when the search
// page shows empty.
{
  const showRidePage = PAGES.ride.onShow;
  PAGES.ride.onShow = (...args) => {
    showRidePage(...args);
    restoreTrip();
  };
  // app.js may have shown the page before this script ran.
  if (document.getElementById("page-ride").classList.contains("active")) restoreTrip();
}
