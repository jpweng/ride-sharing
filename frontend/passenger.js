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
        const input = document.getElementById(`name-${role}`);
        input.value = ""; // the place picked gives the field its name
        input.blur();
        startPicking(role);
      },
    },
  ];
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
// The search starts by itself once both ends are set (and named: a point
// picked on the map gets its name a moment later), and again when the time
// is changed. It is for now, unless the passenger chose a time that is
// still ahead.
// ---------------------------------------------------------------------
let rideSearchTimer = null;

function searchSoon(delayMs = 50) {
  clearTimeout(rideSearchTimer);
  rideSearchTimer = setTimeout(() => {
    const ready = RIDE_ROLES.every((role) => points[role] && points[role].name != null);
    if (!ready || !document.getElementById("page-ride").classList.contains("active")) return;
    if (!rideTimeChosen || !(new Date(getDateTime("ride")) > new Date())) {
      rideTimeChosen = false;
      setDateTime("ride", new Date());
    }
    document.getElementById("ride-when").hidden = false;
    findRide();
  }, delayMs);
}

["point:set", "point:named"].forEach((event) => {
  window.addEventListener(event, (e) => {
    if (RIDE_ROLES.includes(e.detail.role)) searchSoon();
  });
});

["ride-date", "ride-hour", "ride-minute"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    rideTimeChosen = true;
    searchSoon(400); // the hour, then the minutes: one search
  });
});
