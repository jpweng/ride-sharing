"use strict";

// ---------------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------------
const map = L.map("map").setView([46.8182, 8.2275], 8); // Switzerland
const mapTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
});

// In passenger mode the map stays out of the way (styles/passenger.css): it
// keeps its size, so everything can still be drawn and framed on it, but it
// isn't shown until it's asked for (openMap), and then lies over the page.
// Its tiles are only loaded while it can be seen. In driver mode it is
// beside the page (above it on phones) all the time.
const passengerMode = () => document.body.dataset.mode === "passenger";
const mapIsOpen = () => document.body.classList.contains("map-open");

// After the mode changed, or the map opened or closed.
function syncMap() {
  map.invalidateSize();
  if (!passengerMode() || mapIsOpen()) mapTiles.addTo(map);
  else map.removeLayer(mapTiles);
}

function openMap() {
  if (!passengerMode() || mapIsOpen()) return;
  document.body.classList.add("map-open");
  syncMap();
}

function closeMap() {
  if (!mapIsOpen()) return;
  document.body.classList.remove("map-open");
  // Closing it gives up picking a point on it.
  stopPicking();
  syncMap();
}

document.getElementById("map-close").addEventListener("click", closeMap);
document.getElementById("map-backdrop").addEventListener("click", closeMap);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMap();
});

// On phones the map is a band at the top of a scrolling page (see style.css),
// so it can be out of sight when something is shown on it or picked from it.
const PHONE_LAYOUT = window.matchMedia("(max-width: 760px)");

function revealMap() {
  if (passengerMode()) return openMap();
  if (!PHONE_LAYOUT.matches) return;
  const box = map.getContainer().getBoundingClientRect();
  if (box.top < 0 || box.bottom > window.innerHeight) {
    map.getContainer().scrollIntoView({ block: "start", behavior: "smooth" });
  }
}

// After picking on the map, back to the field that was being filled.
function revealOnPhone(el) {
  if (PHONE_LAYOUT.matches && el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

// Layer groups so we can clear/redraw per-view without touching each other.
const pickerLayer = L.layerGroup().addTo(map);   // origin/destination pins being picked
const resultsLayer = L.layerGroup().addTo(map);  // match / drive results (routes, markers)

// role -> { lat, lon, name, marker }
const points = {};

const PICK_COLORS = {
  "drive-origin": "#1a7f37",
  "drive-destination": "#d8232a",
  "ride-origin": "#1a7f37",
  "ride-destination": "#d8232a",
};

let activePickRole = null;

function setPicking(role) {
  document.querySelectorAll(".point-picker button[data-role]").forEach((btn) => {
    btn.classList.toggle("picking", btn.dataset.role === role && activePickRole === role);
  });
  document.body.classList.toggle("map-picking", !!role); // passenger mode: the map says what to do
}

// Clicks on the map now propose places for `role`'s point (see below).
function startPicking(role) {
  activePickRole = role;
  setPicking(role);
  revealMap();
}

function stopPicking() {
  activePickRole = null;
  setPicking(null);
  pickLookup++; // drop the answer to a lookup still running
  map.closePopup();
}

document.querySelectorAll(".point-picker button[data-role]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (activePickRole !== btn.dataset.role) return startPicking(btn.dataset.role);
    stopPicking();
  });
});

// ---------------------------------------------------------------------
// Picking a place on the map. A click doesn't set the point at once: the
// app looks up what is there (a restaurant, a shop, a stop, a park, a
// company..., else the address) and proposes it in a small card on the
// map, with what else is right next to it; "Use this place" takes it, a
// click elsewhere proposes another. Zoomed out too far to tell places
// apart, the click proposes the town.
//
// The places come from Photon's reverse search on OpenStreetMap data (free,
// no key), like the places of the address fields (searchPhoton below).
// ---------------------------------------------------------------------
const PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse";
// From this zoom on, a click is precise enough to mean a place (a pixel is
// then under about 20 m).
const PICK_PLACE_ZOOM = 13;
const PICK_OTHERS_SHOWN = 3;
const PICK_STATION_VALUES = new Set(["station", "halt", "stop"]);
let pickLookup = 0;

function metersPerPixel(lat) {
  return (40075016.686 * Math.cos((lat * Math.PI) / 180)) / 2 ** (map.getZoom() + 8);
}

// The places at and right next to a point of the map, the one meant first:
// [{label, detail, lat, lon, kind}]. A named place within a finger's width
// of the click is the one meant; otherwise the nearest address, then the
// named places around. [] when nothing is known there (or Photon can't be
// reached).
async function placesAt(lat, lon) {
  const mpp = metersPerPixel(lat);
  const reach = Math.min(300, Math.max(40, 40 * mpp)); // metres around the click
  const finger = Math.min(150, Math.max(15, 15 * mpp));
  try {
    const resp = await fetch(
      `${PHOTON_REVERSE_URL}?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}&limit=12&radius=${(reach / 1000).toFixed(3)}&lang=${photonLang()}`
    );
    if (!resp.ok) throw new Error("place lookup failed");
    const named = [];
    const addresses = [];
    for (const f of (await resp.json()).features || []) {
      const p = f.properties || {};
      const [flon, flat] = f.geometry?.coordinates || [];
      if (typeof flat !== "number") continue;
      const street = [p.street, p.housenumber].filter(Boolean).join(" ");
      const town = [p.postcode, p.city].filter(Boolean).join(" ");
      const place = { lat: flat, lon: flon, kind: "address", meters: map.distance([lat, lon], [flat, flon]) };
      // A stop is a place to meet, though a "highway" for OpenStreetMap.
      if (p.name && (!PHOTON_SKIPPED_KEYS.has(p.osm_key) || p.osm_value === "bus_stop")) {
        place.label = p.city && p.city !== p.name ? `${p.name}, ${p.city}` : p.name;
        // A railway station is named after its town: say which of the two it is.
        if (p.osm_key === "railway" && PICK_STATION_VALUES.has(p.osm_value)) place.label = t("map.station", { name: p.name });
        place.detail = [street, town].filter(Boolean).join(", ");
        if (!named.some((q) => q.label === place.label)) named.push(place);
      } else if (p.street && p.housenumber) {
        place.label = [street, town].filter(Boolean).join(", ");
        place.detail = "";
        if (!addresses.some((q) => q.label === place.label)) addresses.push(place);
      }
    }
    const byDistance = (a, b) => a.meters - b.meters;
    named.sort(byDistance);
    addresses.sort(byDistance);
    const meant = named[0] && named[0].meters <= finger ? named.shift() : addresses.shift() || named.shift();
    return meant ? [meant, ...addresses.slice(0, 1), ...named].slice(0, 1 + PICK_OTHERS_SHOWN) : [];
  } catch {
    return [];
  }
}

map.on("click", async (e) => {
  if (!activePickRole) return;
  const role = activePickRole;
  const { lat, lng } = e.latlng;
  const lookup = ++pickLookup;
  const popup = L.popup({ className: "pick-popup", minWidth: 190, maxWidth: 270, autoPanPadding: [16, 76] })
    .setLatLng(e.latlng)
    .setContent(`<div class="pick-detail">${esc(t("common.loading"))}</div>`)
    .openOn(map);
  // The place proposed, marked on the map while its card is open.
  const marker = L.circleMarker(e.latlng, {
    radius: 9, weight: 3, color: PICK_COLORS[role] || "#333", fillColor: "#fff", fillOpacity: 1, interactive: false,
  }).addTo(map);
  popup.on("remove", () => map.removeLayer(marker));

  let places = map.getZoom() >= PICK_PLACE_ZOOM ? await placesAt(lat, lng) : [];
  if (!places.length && lookup === pickLookup) {
    // Nothing known there, or zoomed out: the town, at the point clicked.
    places = [{ label: await reverseGeocode(lat, lng), detail: "", lat, lon: lng, kind: "point" }];
  }
  if (lookup !== pickLookup || activePickRole !== role) return; // another click, or picking was given up
  proposePlace(role, popup, marker, places);
});

// The card of a place proposed: its name, "Use this place", and the other
// places next to it (a click on one proposes it instead).
function proposePlace(role, popup, marker, places) {
  const [place, ...others] = places;
  const card = document.createElement("div");
  card.className = "pick-card";
  card.innerHTML = `
    <div class="pick-name">${esc(place.label)}</div>
    ${place.detail ? `<div class="pick-detail">${esc(place.detail)}</div>` : ""}
  `;
  card.appendChild(actionButton("map.useThisPlace", "submit-btn", () => setTimeout(() => usePlace(role, place), 0)));
  if (others.length) {
    const list = document.createElement("div");
    list.className = "pick-others";
    list.innerHTML = `<div class="pick-detail">${esc(t("map.nearby"))}</div>`;
    others.forEach((other) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "link-btn";
      button.textContent = other.label;
      // Once the click is over: the map takes a click on a button that has
      // left the card meanwhile for a click on the map itself.
      button.addEventListener("click", () => setTimeout(
        () => proposePlace(role, popup, marker, [other, place, ...others.filter((o) => o !== other)]), 0,
      ));
      list.appendChild(button);
    });
    card.appendChild(list);
  }
  marker.setLatLng([place.lat, place.lon]);
  popup.setLatLng([place.lat, place.lon]).setContent(card);
}

function usePlace(role, place) {
  const input = document.getElementById(`name-${role}`);
  if (input) input.value = place.label;
  stopPicking();
  setPointCoords(role, place.lat, place.lon, place.label, place.kind);
  closeMap(); // passenger mode: back to the page
  revealOnPhone(input);
}

// kind: "place" (a town picked by name -- public transport then starts/ends at
// its train station), "poi" (a named place -- at its stop), "address", or
// "point" (clicked on the map).
function setPointCoords(role, lat, lon, name, kind = "point") {
  clearPoint(role);
  const marker = L.circleMarker([lat, lon], {
    radius: 8,
    color: PICK_COLORS[role] || "#333",
    fillColor: PICK_COLORS[role] || "#333",
    fillOpacity: 0.9,
  }).addTo(pickerLayer);

  points[role] = { lat, lon, name, kind, marker, circle: radiusCircle(role, lat, lon) };

  const readout = document.getElementById(`readout-${role}`);
  if (readout) readout.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;

  zoomToTrip(role.split("-")[0]);
  if (role.startsWith("drive-")) updatePriceBand();
  // For the scripts loaded after this one (areas.js).
  window.dispatchEvent(new CustomEvent("point:set", { detail: { role } }));
}

// ---------------------------------------------------------------------
// A circle around a picked point, sized by a control next to it: role ->
// the control's id. Scripts loaded after this one add theirs (areas.js:
// the area being added, sized by its radius).
// ---------------------------------------------------------------------
const RADIUS_SLIDERS = {};

function radiusKm(role) {
  return Number(document.getElementById(RADIUS_SLIDERS[role]).value);
}

function radiusCircle(role, lat, lon) {
  if (!RADIUS_SLIDERS[role]) return null;
  return L.circle([lat, lon], {
    radius: radiusKm(role) * 1000,
    color: PICK_COLORS[role],
    weight: 2,
    fillOpacity: 0.08,
    interactive: false,
  }).addTo(pickerLayer);
}

// Bounds of a point and its circle, if any (L.Circle.getBounds() needs the
// circle to be on the map).
function pointBounds(role) {
  const p = points[role];
  const km = p.circle ? radiusKm(role) : 0;
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.cos((p.lat * Math.PI) / 180));
  return L.latLngBounds([p.lat - dLat, p.lon - dLon], [p.lat + dLat, p.lon + dLon]);
}

// Once a page has both its start and arrival set, frame the trip on the map.
function zoomToTrip(page) {
  const origin = `${page}-origin`;
  const destination = `${page}-destination`;
  if (!points[origin] || !points[destination]) return;
  map.fitBounds(pointBounds(origin).extend(pointBounds(destination)), { padding: [30, 30], maxZoom: 14 });
}

function clearPoint(role) {
  if (points[role] && points[role].marker) pickerLayer.removeLayer(points[role].marker);
  if (points[role] && points[role].circle) pickerLayer.removeLayer(points[role].circle);
  delete points[role];
  const readout = document.getElementById(`readout-${role}`);
  if (readout) readout.textContent = t("form.notSet");
}

async function reverseGeocode(lat, lon) {
  try {
    const resp = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}&zoom=14&accept-language=${I18N.lang()}`,
      { headers: { Accept: "application/json" } }
    );
    if (!resp.ok) throw new Error("reverse geocode failed");
    const data = await resp.json();
    const a = data.address || {};
    return (
      a.town || a.village || a.city || a.suburb || a.municipality ||
      data.display_name?.split(",")[0] ||
      `${lat.toFixed(4)}, ${lon.toFixed(4)}`
    );
  } catch {
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
}

function getPoint(role, nameInputId) {
  const p = points[role];
  if (!p) return null;
  const typedName = document.getElementById(nameInputId).value.trim();
  return {
    name: typedName || p.name || `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`,
    lat: p.lat,
    lon: p.lon,
    kind: p.kind || "point",
  };
}

// ---------------------------------------------------------------------
// Address autocomplete: Swiss addresses and places from geo.admin.ch,
// named places (hotels, shops, stations...) from Photon
// See "address auto complete.md" for the option comparison behind this choice.
// ---------------------------------------------------------------------
const GEOADMIN_SEARCH_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer";

function debounce(fn, delayMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  };
}

function stripHtml(html) {
  const div = document.createElement("div");
  div.innerHTML = html || "";
  return div.textContent || div.innerText || "";
}

// Origins: "gg25" = municipalities, "zipcode" = postcodes, "gazetteer" =
// swissNAMES3D, "address" = street addresses. Places and addresses are
// searched separately: the limit applies to all origins together, and the
// gazetteer's many matches would crowd the addresses out.
async function geoAdminSearch(query, origins, limit) {
  try {
    const resp = await fetch(
      `${GEOADMIN_SEARCH_URL}?searchText=${encodeURIComponent(query)}&type=locations&origins=${origins}&limit=${limit}&sr=4326&lang=${I18N.lang()}`
    );
    if (!resp.ok) throw new Error("geocoding failed");
    const data = await resp.json();
    return (data.results || [])
      .map((r) => r.attrs || {})
      .filter((a) => typeof a.lat === "number" && typeof a.lon === "number");
  } catch {
    return []; // fail soft -- "Pick on map" is always a working fallback
  }
}

// Of the gazetteer, only settlement names: towns, villages and city
// districts that aren't municipalities of their own (Oerlikon, Verbier,
// Wengen, Ouchy...). The rest is noise here: schools, buildings, peaks.
const SETTLEMENT_CLASS = "tlm_siedlungsname";
const MAX_PLACE_SUGGESTIONS = 4;
const MAX_SUGGESTIONS = 8;
// How long after the last key a search starts. The servers answer in about
// 0.1 s; a longer wait than this shows nothing while someone types.
const SEARCH_DELAY_MS = 150;

// A gazetteer label: "<i>Quartier</i> <b>Oerlikon</b> (ZH) - Zürich" ->
// "Oerlikon (ZH)", with "Quartier · Zürich" as detail.
function settlementSuggestion(a) {
  const div = document.createElement("div");
  div.innerHTML = a.label || "";
  const name = (div.querySelector("b")?.textContent || "").trim();
  const type = (div.querySelector("i")?.textContent || "").trim();
  const where = /\(([^)]+)\)\s*-\s*(.*)$/.exec(div.textContent.trim());
  if (!name || !where) return null;
  const municipalities = where[2].split(",");
  const municipality = municipalities.length === 1 && municipalities[0] !== name ? municipalities[0] : "";
  return {
    label: `${name} (${where[1]})`,
    detail: [type, municipality].filter(Boolean).join(" · "),
    lat: a.lat,
    lon: a.lon,
    kind: "place",
  };
}

// "St. Moritz" -> "stmoritz": compares names regardless of accents and punctuation.
function searchKey(text) {
  return text.normalize("NFD").replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
}

// The gazetteer also matches on the municipality: "Zermatt" would list every
// hamlet of Zermatt. Settlements must have the typed words in their name.
function nameMatches(name, query) {
  const key = searchKey(name);
  return query.split(/\s+/).map(searchKey).every((word) => key.includes(word));
}

function placeSuggestion(a, query) {
  if (a.origin === "gazetteer") {
    if (a.objectclass?.toLowerCase() !== SETTLEMENT_CLASS) return null;
    const p = settlementSuggestion(a);
    return p && nameMatches(p.label.replace(/ \([^)]*\)$/, ""), query) ? p : null;
  }
  // "Zermatt (VS)"; postcodes come as "8050 - Zürich".
  const label = stripHtml(a.label).replace(/^(\d{4}) - /, "$1 ").trim();
  const municipality = a.origin === "gg25";
  return { label, detail: "", lat: a.lat, lon: a.lon, kind: municipality ? "place" : "address", municipality };
}

// Named places -- hotels, shops, stations, venues -- from Photon, komoot's
// search on OpenStreetMap data (free, no key). geo.admin.ch has addresses
// and place names but no businesses. See "address auto complete.md".
const PHOTON_URL = "https://photon.komoot.io/api/";
const PHOTON_BBOX = "5.8,45.7,10.6,47.9"; // Switzerland and a strip of its neighbours
// Streets, addresses and towns come from geo.admin.ch; the rest is no
// place to meet (bus platforms, fire alarms, forests...).
const PHOTON_SKIPPED_KEYS = new Set([
  "place", "highway", "boundary", "landuse", "natural", "waterway", "emergency", "power", "man_made",
]);
const MAX_POI_SUGGESTIONS = 4;
// Photon answers in these languages only, and refuses the others (Italian).
const photonLang = () => (["en", "de", "fr"].includes(I18N.lang()) ? I18N.lang() : "default");

async function searchPhoton(query) {
  try {
    // Places near where the map is looking first.
    const center = map.getCenter();
    const resp = await fetch(
      `${PHOTON_URL}?q=${encodeURIComponent(query)}&limit=12&lang=${photonLang()}&bbox=${PHOTON_BBOX}&lat=${center.lat.toFixed(4)}&lon=${center.lng.toFixed(4)}`
    );
    if (!resp.ok) throw new Error("place search failed");
    const data = await resp.json();
    const pois = [];
    for (const f of data.features || []) {
      const p = f.properties || {};
      const [lon, lat] = f.geometry?.coordinates || [];
      if (!p.name || PHOTON_SKIPPED_KEYS.has(p.osm_key) || typeof lat !== "number") continue;
      const label = p.city && p.city !== p.name ? `${p.name}, ${p.city}` : p.name;
      if (pois.some((q) => q.label === label)) continue;
      const street = [p.street, p.housenumber].filter(Boolean).join(" ");
      const town = [p.postcode, p.city].filter(Boolean).join(" ");
      const country = p.countrycode && p.countrycode !== "CH" ? p.country : "";
      // "poi": public transport starts/ends at the stop of that name (Zürich HB),
      // else at the nearest stop.
      pois.push({ label, detail: [street, town, country].filter(Boolean).join(", "), lat, lon, kind: "poi" });
    }
    return pois;
  } catch {
    return []; // fail soft, like geo.admin.ch
  }
}

async function searchPlaces(query) {
  const [placeResults, addressResults, pois] = await Promise.all([
    geoAdminSearch(query, "zipcode,gg25,gazetteer", 30),
    geoAdminSearch(query, "address", MAX_SUGGESTIONS),
    searchPhoton(query),
  ]);
  const places = [];
  for (const a of placeResults) {
    const p = placeSuggestion(a, query);
    if (!p) continue;
    // A municipality and its namesake town ("Vevey (VD)") show once, at the
    // town's coordinates: the municipality's are the middle of its area,
    // which for a lakeside town (Vevey, Neuchâtel, Zug) is in the lake.
    const same = places.find((q) => q.label === p.label);
    if (!same) places.push(p);
    else if (same.municipality && !p.municipality) Object.assign(same, { lat: p.lat, lon: p.lon, municipality: false });
  }
  const addresses = addressResults.map((a) => ({
    label: stripHtml(a.label),
    detail: "",
    lat: a.lat,
    lon: a.lon,
    kind: "address",
  }));
  const shownPlaces = places.slice(0, MAX_PLACE_SUGGESTIONS);
  const shownPois = pois.slice(0, MAX_POI_SUGGESTIONS);
  const shownAddresses = addresses.slice(0, Math.max(2, MAX_SUGGESTIONS - shownPlaces.length - shownPois.length));
  // Someone typing a house number wants an address first (but a bare
  // postcode, its area).
  return /\d/.test(query) && !/^\d{4}$/.test(query.trim())
    ? [...shownAddresses, ...shownPlaces, ...shownPois]
    : [...shownPlaces, ...shownPois, ...shownAddresses];
}

// Words that find nothing ("pneus direct sevaz": a shop neither source
// knows) may still name a town: the same words without the first ones, and
// without the last ones, the longest first.
const MAX_SHORTER_QUERIES = 6;

function shorterQueries(query) {
  const words = query.trim().split(/\s+/);
  const shorter = [];
  for (let n = words.length - 1; n > 0; n--) {
    shorter.push(words.slice(-n).join(" "), words.slice(0, n).join(" "));
  }
  return [...new Set(shorter)].filter((q) => q.length >= 3).slice(0, MAX_SHORTER_QUERIES);
}

// The towns, postcodes and settlements named by part of the words: {words,
// places} for the longest part that names any, null when none does. geo.admin
// also answers with names that only resemble the words, which are left out.
async function searchTownAmong(query) {
  const queries = shorterQueries(query);
  const answers = await Promise.all(queries.map((q) => geoAdminSearch(q, "zipcode,gg25,gazetteer", 30)));
  for (const [i, words] of queries.entries()) {
    const places = [];
    for (const a of answers[i]) {
      const p = placeSuggestion(a, words);
      if (p && nameMatches(p.label.replace(/ \([^)]*\)$/, ""), words) && !places.some((q) => q.label === p.label)) {
        places.push(p);
      }
    }
    if (places.length) return { words, places: places.slice(0, MAX_PLACE_SUGGESTIONS) };
  }
  return null;
}

// Scripts loaded after this one (passenger.js) add entries to a field's
// list: role -> (query) => [items]. An item is a place ({label, detail, lat,
// lon, kind}) or something to do ({label, detail, action}). They come after
// the places found, and alone while there is nothing to search for yet.
// The list itself adds a line to read ({label, detail, note: true}) when the
// words find nothing.
const FIELD_SUGGESTIONS = {};

function fieldSuggestions(role, query) {
  return FIELD_SUGGESTIONS[role] ? FIELD_SUGGESTIONS[role](query) : [];
}

function setupAutocomplete(role) {
  const input = document.getElementById(`name-${role}`);
  const list = document.getElementById(`suggest-${role}`);
  if (!input || !list) return;

  let items = [];
  let activeIndex = -1;

  function closeList() {
    searchCount++; // drop the answer to a search still running
    list.classList.remove("show");
    list.innerHTML = "";
    items = [];
    activeIndex = -1;
  }

  function renderList() {
    list.innerHTML = items
      .map(
        (it, i) =>
          `<li data-index="${i}" class="${i === activeIndex ? "active" : ""}${it.action ? " action" : ""}${it.note ? " note" : ""}">${esc(it.label)}${
            it.detail ? `<span class="suggest-detail">${esc(it.detail)}</span>` : ""
          }</li>`
      )
      .join("");
    list.classList.toggle("show", items.length > 0);
  }

  function selectItem(it) {
    if (it.action) {
      closeList();
      return it.action();
    }
    input.value = it.label;
    setPointCoords(role, it.lat, it.lon, it.label, it.kind);
    closeList();
  }

  // The arrow keys skip the lines that are only there to be read.
  function moveActive(step) {
    for (let n = 0; n < items.length; n++) {
      activeIndex = (activeIndex + step + items.length) % items.length;
      if (!items[activeIndex].note) return;
    }
    activeIndex = -1;
  }

  let searchCount = 0;
  const runSearch = debounce(async (query) => {
    const search = ++searchCount;
    if (query.trim().length < 3) {
      closeList();
      showOwnEntries();
      return;
    }
    let found = await searchPlaces(query);
    if (search !== searchCount) return; // a newer search has started
    if (!found.length) {
      // Say so, and offer the town the words name, if they name one.
      const town = await searchTownAmong(query);
      if (search !== searchCount) return;
      const detail = town ? t("form.noMatchPlaces", { words: town.words }) : t("form.noMatchHint");
      found = [{ label: t("form.noMatch"), detail, note: true }, ...(town ? town.places : [])];
    }
    items = [...found, ...fieldSuggestions(role, query)];
    activeIndex = -1;
    renderList();
  }, SEARCH_DELAY_MS);

  // Nothing to search for yet: what the field offers by itself, if anything.
  function showOwnEntries() {
    if (document.activeElement !== input) return;
    items = fieldSuggestions(role, input.value);
    activeIndex = -1;
    renderList();
  }

  input.addEventListener("input", () => runSearch(input.value));
  input.addEventListener("focus", () => {
    if (input.value.trim().length < 3) showOwnEntries();
  });

  input.addEventListener("keydown", (e) => {
    if (!items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveActive(1);
      renderList();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (activeIndex < 0) activeIndex = 0; // from the field, up is the last line
      moveActive(-1);
      renderList();
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      selectItem(items[activeIndex]);
    } else if (e.key === "Escape") {
      closeList();
    }
  });

  list.addEventListener("mousedown", (e) => {
    // mousedown (not click) fires before the input's blur handler closes the list
    const li = e.target.closest("li[data-index]");
    if (!li) return;
    const it = items[Number(li.dataset.index)];
    if (it.note) return e.preventDefault(); // nothing to choose: the field keeps the focus
    selectItem(it);
  });

  input.addEventListener("blur", closeList);
}

Object.keys(PICK_COLORS).forEach(setupAutocomplete);

// ---------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------
function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function fitToResults() {
  const group = L.featureGroup(resultsLayer.getLayers());
  if (group.getBounds().isValid()) map.fitBounds(group.getBounds(), { padding: [30, 30] });
}

// API errors carry a code (see backend/api.py) that maps to "errors.<code>".
// The session cookie goes with every request (same origin). A 401 means
// the session ended (signed out elsewhere, expired, account closed): back
// to the sign-in page.
async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  let data = {};
  try {
    data = await resp.json();
  } catch {
    // e.g. the proxy's 429, which has no JSON body
  }
  if (!resp.ok) {
    if (resp.status === 401 && currentUser) {
      setUser(null);
      route();
    }
    const code = data.detail?.code || (resp.status === 429 ? "too_many_requests" : null);
    throw new Error(code ? t(`errors.${code}`) : t("errors.request_failed", { status: resp.status }));
  }
  return data;
}

function postJson(url, body, method = "POST") {
  return fetchJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------
// Signing in: an email with a one-time link (see backend/auth.py), or
// "view as a demo driver" (read-only, no email). The session is an
// HttpOnly cookie, so this page only knows who is signed in by asking
// /api/auth/me.
// ---------------------------------------------------------------------
let currentUser = null; // see auth.public_user()

// Before sign-in by email, the typed name was kept in local storage.
try {
  localStorage.removeItem("rideshare.user");
} catch {
  // storage unavailable (private window etc.)
}

async function loadCurrentUser() {
  try {
    currentUser = (await fetchJson("/api/auth/me")).user;
  } catch {
    currentUser = null;
  }
  renderAccount();
}

function setUser(user) {
  currentUser = user;
  if (user) hideStatus("signin-notice");
  renderAccount();
  // For the scripts loaded after this one (passenger.js: a search shows more to a signed-in user).
  window.dispatchEvent(new CustomEvent("user:changed"));
}

// Searching needs no account; asking for a seat or asking drivers does. The
// sign-in page, saying why. The search stays in this tab's memory.
function goToSignIn() {
  goTo("signin");
  showStatus("signin-notice", t("signin.needed"), true);
}

// The sign-in link opens in another tab: back in this one, the user is
// signed in, and the page they came from is there again.
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible" || currentUser) return;
  let user = null;
  try {
    user = (await fetchJson("/api/auth/me")).user;
  } catch {
    return;
  }
  if (!user || currentUser) return;
  setUser(user);
  route();
});

const signedIn = () => !!currentUser?.profile_complete;

function renderAccount() {
  const el = document.getElementById("account");
  const note = document.getElementById("demo-view-note");
  note.hidden = !currentUser?.is_demo;
  if (currentUser?.is_demo) note.textContent = t("demoView.note", { name: currentUser.name });
  if (!currentUser) {
    el.innerHTML = `<a class="account-signin" href="#/signin">${esc(t("signin.title"))}</a>`;
    return;
  }
  const name = currentUser.name
    ? `<a href="#/account">${esc(currentUser.name)}</a>${currentUser.is_demo ? ` <span class="badge demo">${esc(t("account.demoBadge"))}</span>` : ""}`
    : "";
  el.innerHTML = `${name} <button type="button" data-action="sign-out">${esc(t("account.signOut"))}</button>`;
}

async function signOut() {
  try {
    await fetchJson("/api/auth/sign_out", { method: "POST" });
  } catch {
    // signed out locally anyway
  }
  setUser(null);
  goTo("ride");
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-action='sign-out']")) signOut();
});

document.getElementById("signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("signin-email").value.trim();
  if (!email) return showStatus("signin-status", t("signin.needEmail"), false);
  const btn = document.getElementById("signin-submit");
  btn.disabled = true;
  btn.textContent = t("signin.sending");
  try {
    await postJson("/api/auth/link", { email, language: I18N.lang() });
    showStatus("signin-status", t("signin.sent", { email }), true);
    // For the scripts loaded after this one (signin_code.js: the email's code can be typed here).
    window.dispatchEvent(new CustomEvent("signin:sent", { detail: { email } }));
  } catch (err) {
    showStatus("signin-status", err.message, false);
  } finally {
    btn.disabled = false;
    btn.textContent = t("signin.submit");
  }
});

async function loadDemoDrivers() {
  const select = document.getElementById("signin-demo-driver");
  if (select.options.length) return;
  try {
    const names = await fetchJson("/api/auth/demo_drivers");
    select.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  } catch {
    // the button then reports the error
  }
}

document.getElementById("signin-demo").addEventListener("click", async () => {
  try {
    const data = await postJson("/api/auth/demo", { driver_name: document.getElementById("signin-demo-driver").value });
    setUser(data.user);
    goTo("drive");
  } catch (err) {
    showStatus("signin-demo-status", err.message, false);
  }
});

// The emailed link opens #/signin/<token>: the token goes to the server in
// a POST (never in a URL it logs), and leaves the address bar and history
// at once. Mail scanners that open links don't run this, so they can't use
// up the token.
async function verifySignInLink(token) {
  history.replaceState(null, "", `${location.pathname}${location.search}#/${MODE_HOME[mode]}`);
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("active", s.id === "page-verify"));
  document.getElementById("verify-retry").hidden = true;
  hideStatus("verify-status");
  try {
    const data = await postJson("/api/auth/verify", { token });
    setUser(data.user);
    route();
  } catch (err) {
    showStatus("verify-status", t("signin.verifyFailed", { error: err.message }), false);
    document.getElementById("verify-retry").hidden = false;
  }
}

// The address bar already shows the mode's home page: route() shows the sign-in page.
document.getElementById("verify-retry").addEventListener("click", () => route());

// First sign-in (or a new code of conduct): name, 18+, code of conduct.
function showProfile() {
  const firstTime = !currentUser.name;
  document.getElementById("profile-names").hidden = !firstTime;
  document.getElementById("profile-conduct-changed").hidden = !currentUser.needs_conduct;
}

document.getElementById("profile-submit").addEventListener("click", async () => {
  const firstTime = !currentUser.name;
  const first = document.getElementById("profile-first-name").value.trim();
  const last = document.getElementById("profile-last-name").value.trim();
  const adult = document.getElementById("profile-adult").checked;
  const conduct = document.getElementById("profile-conduct").checked;
  if (firstTime && !first) return showStatus("profile-status", t("profile.needFirstName"), false);
  if (firstTime && !last) return showStatus("profile-status", t("profile.needLastName"), false);
  if (!adult || !conduct) return showStatus("profile-status", t("profile.needConsent"), false);
  try {
    const data = await postJson("/api/auth/profile", {
      first_name: first, last_name: last, adult, accept_conduct: conduct,
    });
    hideStatus("profile-status");
    setUser(data.user);
    route();
  } catch (err) {
    showStatus("profile-status", err.message, false);
  }
});

function showAccount() {
  const details = document.getElementById("account-details");
  details.innerHTML = [
    currentUser.name ? `<p>${esc(t("account.name", { name: currentUser.name }))}</p>` : "",
    currentUser.email ? `<p>${esc(t("account.email", { email: currentUser.email }))}</p>` : "",
  ].join("");
  document.getElementById("account-delete-box").hidden = currentUser.is_demo;
  hideStatus("account-status");
}

document.getElementById("account-delete").addEventListener("click", async () => {
  if (!confirm(t("account.deleteConfirm"))) return;
  try {
    await fetchJson("/api/account", { method: "DELETE" });
    setUser(null);
    goTo("signin");
    showStatus("signin-notice", t("signin.deleted"), true);
  } catch (err) {
    showStatus("account-status", err.message, false);
  }
});

// ---------------------------------------------------------------------
// Pages: #/ride (passenger, public: searching needs no account), #/drive
// (driver), #/inbox, #/account, #/signin. Until signed in, the sign-in page
// shows instead of those that need a user; until the profile is complete,
// the profile page does (but the account page stays reachable, to delete it).
// ---------------------------------------------------------------------
const PAGES = {
  ride: { needsUser: false, onShow: () => { loadMyRequests(); loadRecentSearches(); } },
  drive: { needsUser: true, onShow: loadMyDrives },
  inbox: { needsUser: true, onShow: loadInbox },
  account: { needsUser: true, beforeProfile: true, onShow: showAccount },
  signin: { onShow: loadDemoDrivers },
  profile: { onShow: showProfile },
};
const LINKED_PAGES = ["ride", "drive", "inbox", "account", "signin"];

// ---------------------------------------------------------------------
// Two modes, passenger and driver, each with its own tabs (the inbox and
// the account are in both). A button switches to the other one; opening a
// page of the other mode (a link, a button of the inbox) switches too. The
// mode is remembered in this browser: the app opens where it was left.
// ---------------------------------------------------------------------
const MODE_OF_PAGE = { ride: "passenger", drive: "driver" };
const MODE_HOME = { passenger: "ride", driver: "drive" };
const MODE_STORAGE_KEY = "rideandrail.mode";

function savedMode() {
  try {
    const saved = localStorage.getItem(MODE_STORAGE_KEY);
    return saved in MODE_HOME ? saved : null;
  } catch {
    return null; // storage unavailable (private window etc.)
  }
}

let mode = savedMode() || "passenger";
document.body.dataset.mode = mode;
syncMap();

function setMode(next) {
  mode = next;
  document.body.dataset.mode = next; // style.css shows that mode's tabs
  document.body.classList.remove("map-open");
  syncMap(); // the map is beside the page for drivers, out of the way for passengers
  try {
    localStorage.setItem(MODE_STORAGE_KEY, next);
  } catch {
    // fine: the mode just won't be remembered
  }
  renderModeSwitch();
}

// One button: it names the current mode, and a click switches to the other
// one (its tooltip says so).
function renderModeSwitch() {
  const button = document.getElementById("mode-switch");
  button.textContent = mode === "passenger" ? t("nav.passengerMode") : t("nav.driverMode");
  button.title = mode === "passenger" ? t("nav.toDriver") : t("nav.toPassenger");
}

document.getElementById("mode-switch").addEventListener("click", () => {
  goTo(MODE_HOME[mode === "passenger" ? "driver" : "passenger"]); // route() sets the mode
});

function route() {
  const requested = location.hash.replace(/^#\/?/, "");
  if (requested.startsWith("signin/")) return verifySignInLink(requested.slice("signin/".length));
  const page = LINKED_PAGES.includes(requested) ? requested : MODE_HOME[mode];
  if (MODE_OF_PAGE[page] && MODE_OF_PAGE[page] !== mode) setMode(MODE_OF_PAGE[page]);
  // Signed in already: the sign-in page has nothing to offer.
  if (page === "signin" && currentUser) return goTo(MODE_HOME[mode]);
  let shown = page;
  if (PAGES[page].needsUser && !currentUser) shown = "signin";
  else if (currentUser && !currentUser.profile_complete && !PAGES[page].beforeProfile) shown = "profile";
  if (shown !== "signin") hideStatus("signin-notice");

  document.querySelectorAll(".tab-link").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("active", s.id === `page-${shown}`));
  document.getElementById("picker-hint").hidden = shown !== "drive";
  closeMap();

  // Only show the picked origin/destination pins that belong to this page.
  stopPicking();
  pickerLayer.clearLayers();
  Object.entries(points).forEach(([role, p]) => {
    if (!role.startsWith(`${shown}-`)) return;
    if (p.marker) pickerLayer.addLayer(p.marker);
    if (p.circle) pickerLayer.addLayer(p.circle);
  });
  resultsLayer.clearLayers();

  if (PAGES[shown]?.onShow) PAGES[shown].onShow();
  refreshUnread();
}

// Go to a page now (route() runs synchronously, unlike on hashchange), so
// what's done next isn't undone by route() clearing the map.
function goTo(page) {
  if (location.hash !== `#/${page}`) history.pushState(null, "", `#/${page}`);
  route();
}

window.addEventListener("hashchange", route);

// ---------------------------------------------------------------------
// A short notice over the page, closed with OK, Escape or a click beside it
// ---------------------------------------------------------------------
function showNotice(title, text) {
  const dialog = document.getElementById("notice");
  document.getElementById("notice-title").textContent = title;
  document.getElementById("notice-text").textContent = text;
  if (!dialog.open) dialog.showModal();
}

document.getElementById("notice-ok").addEventListener("click", () => document.getElementById("notice").close());
document.getElementById("notice").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close(); // the backdrop
});

// ---------------------------------------------------------------------
// Status helper
// ---------------------------------------------------------------------
function showStatus(elId, message, ok) {
  const el = document.getElementById(elId);
  el.textContent = message;
  el.className = `status-msg show ${ok ? "ok" : "err"}`;
}

function hideStatus(elId) {
  document.getElementById(elId).className = "status-msg";
}

// ---------------------------------------------------------------------
// Driver page: your drives + offer a new one
// ---------------------------------------------------------------------
// Fictional drives kept by the backend (demo_drives.py) are flagged is_demo.
function demoBadge(d) {
  return d.is_demo ? `<span class="badge demo">${esc(t("badge.demo"))}</span>` : "";
}

function fmtChf(chf) {
  return chf.toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// What a passenger pays for a drive or a match (see pricing.py), with the
// train fare for the same distance next to it.
function priceBadges(item) {
  if (typeof item.price_chf !== "number") return "";
  let html = `<span class="badge price">${esc(t("price.perSeat", { price: fmtChf(item.price_chf) }))}</span>`;
  const train = item.train_fare;
  if (train) {
    const key = train.approximate ? "price.trainApprox" : "price.train";
    html += `<span class="badge train-fare" title="${esc(t("price.trainHint"))}">${esc(t(key, { price: fmtChf(train.full_chf) }))}</span>`;
  }
  return html;
}

// The price per passenger falls as more join: "2 passengers: CHF 7.50
// each · 3 passengers: ...", up to MAX_SHARED_SHOWN passengers.
const MAX_SHARED_SHOWN = 4;

function sharedPricesText(byPassengers) {
  const items = byPassengers
    .slice(1, MAX_SHARED_SHOWN)
    .map((price, i) => t("price.withPassengers", { n: i + 2, price: fmtChf(price) }));
  return items.length ? t("drive.priceShared", { list: items.join(" · ") }) : "";
}

function sharedPricesHtml(item) {
  const text = sharedPricesText(item.price_by_passengers || []);
  return text ? `<div class="meta">${esc(text)}</div>` : "";
}

function driveCardHtml(d) {
  const km = d.route_distance_m ? (d.route_distance_m / 1000).toFixed(1) : "?";
  return `
    <div class="route">${esc(d.origin_name)} → ${esc(d.destination_name)}${demoBadge(d)}</div>
    <div class="meta">${fmtTime(d.departure_time)} · ${esc(t("common.seats", { count: d.seats_available }))} · ${km} km</div>
    ${priceBadges(d)}
    ${sharedPricesHtml(d)}
  `;
}

// Scripts loaded after this one (bookings.js) add to the cards of drives:
// each function gets the kind of card ("mine": the driver's own, "match": a
// search result), the drive or match, and the card's element.
const CARD_EXTRAS = [];

function cardExtras(kind, item, card) {
  CARD_EXTRAS.forEach((extra) => extra(kind, item, card));
}

// ---------------------------------------------------------------------
// The driver's price: a band from /api/price_band once both ends are set,
// prefilled with its default, a warning above the half-fare train ticket,
// and what each passenger pays when more join (see pricing.py).
// ---------------------------------------------------------------------
const priceState = { band: null, tripKey: "", loading: false, error: "", request: 0 };

function drivePriceInput() {
  return document.getElementById("drive-price");
}

async function updatePriceBand() {
  const origin = points["drive-origin"];
  const destination = points["drive-destination"];
  const input = drivePriceInput();
  if (!origin || !destination) {
    Object.assign(priceState, { band: null, tripKey: "", loading: false, error: "" });
    priceState.request++;
    input.value = "";
    input.disabled = true;
    renderPriceBand();
    return;
  }
  const tripKey = [origin.lat, origin.lon, destination.lat, destination.lon].join(",");
  if (tripKey === priceState.tripKey) return;
  const request = ++priceState.request;
  Object.assign(priceState, { band: null, tripKey, loading: true, error: "" });
  input.disabled = true;
  renderPriceBand();
  try {
    const params = new URLSearchParams({
      origin_lat: origin.lat, origin_lon: origin.lon, destination_lat: destination.lat, destination_lon: destination.lon,
    });
    const band = await fetchJson(`/api/price_band?${params}`);
    if (request !== priceState.request) return; // the trip changed meanwhile
    priceState.band = band;
    input.min = band.min_chf;
    input.max = band.max_chf;
    input.value = band.default_chf.toFixed(2);
    input.disabled = false;
  } catch (err) {
    if (request !== priceState.request) return;
    // Posting still works: the server then uses the default price.
    Object.assign(priceState, { tripKey: "", error: err.message });
  } finally {
    if (request === priceState.request) {
      priceState.loading = false;
      renderPriceBand();
    }
  }
}

// Mirrors pricing.passenger_price_chf: min(price, ceiling, train), rounded
// down to 10 centimes.
function passengerPrice(price, passengers) {
  const band = priceState.band;
  const capped = Math.min(price, band.ceilings_chf[passengers - 1], band.train_fare.full_chf);
  return Math.floor(capped * 10 + 1e-9) / 10;
}

function renderPriceBand() {
  const bandEl = document.getElementById("drive-price-band");
  const warnEl = document.getElementById("drive-price-warn");
  const sharedEl = document.getElementById("drive-price-shared");
  warnEl.hidden = true;
  sharedEl.hidden = true;
  const band = priceState.band;
  if (!band) {
    bandEl.textContent = priceState.error || t(priceState.loading ? "drive.priceLoading" : "drive.priceNeedPoints");
    return;
  }
  bandEl.textContent = t("drive.priceBand", {
    min: fmtChf(band.min_chf),
    max: fmtChf(band.max_chf),
    km: fmtNumber(band.km),
    full: fmtChf(band.train_fare.full_chf),
    half: fmtChf(band.train_fare.half_chf),
  });
  const price = Number(drivePriceInput().value);
  if (!drivePriceInput().value || !(price >= band.min_chf && price <= band.max_chf)) return;
  if (price > band.warn_above_chf) {
    warnEl.textContent = t("drive.priceWarn", { half: fmtChf(band.warn_above_chf) });
    warnEl.hidden = false;
  }
  const seats = Math.min(8, Math.max(1, parseInt(document.getElementById("drive-seats").value, 10) || 1));
  const byPassengers = [...Array(seats).keys()].map((i) => passengerPrice(price, i + 1));
  sharedEl.textContent = sharedPricesText(byPassengers);
  sharedEl.hidden = !sharedEl.textContent;
}

drivePriceInput().addEventListener("input", renderPriceBand);
document.getElementById("drive-seats").addEventListener("input", renderPriceBand);

// The price to post: null to let the server use the default (no band yet),
// undefined after showing why the typed price can't be posted.
function drivePriceToPost() {
  const band = priceState.band;
  const input = drivePriceInput();
  if (!band || !input.value) return null;
  const price = Math.round(Number(input.value) * 100) / 100;
  if (!(price >= band.min_chf && price <= band.max_chf)) {
    showStatus("drive-status", t("drive.priceOutOfBand", { min: fmtChf(band.min_chf), max: fmtChf(band.max_chf) }), false);
    return undefined;
  }
  return price;
}

async function loadMyDrives() {
  const list = document.getElementById("drive-mine");
  list.innerHTML = `<div class="empty-state">${esc(t("common.loading"))}</div>`;
  try {
    const drives = await fetchJson("/api/drives?mine=true");
    if (!drives.length) {
      list.innerHTML = `<div class="empty-state">${esc(t("drive.empty"))}</div>`;
      return;
    }
    list.innerHTML = "";
    drives.forEach((d) => {
      const card = document.createElement("div");
      card.className = "drive-card clickable";
      card.innerHTML = `${driveCardHtml(d)}<button type="button" class="link-btn">${esc(t("drive.cancel"))}</button>`;
      card.addEventListener("click", () => showDriveOnMap(d));
      card.querySelector(".link-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        cancelDrive(d);
      });
      list.appendChild(card);
      cardExtras("mine", d, card);
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("drive.loadFailed", { error: err.message }))}</div>`;
  }
}

async function cancelDrive(d) {
  if (!confirm(t("drive.cancelConfirm", { from: d.origin_name, to: d.destination_name }))) return;
  try {
    await fetchJson(`/api/drives/${d.id}`, { method: "DELETE" });
    resultsLayer.clearLayers();
    loadMyDrives();
  } catch (err) {
    showStatus("drive-status", err.message, false);
  }
}

document.getElementById("drive-submit").addEventListener("click", async () => {
  const origin = getPoint("drive-origin", "name-drive-origin");
  const destination = getPoint("drive-destination", "name-drive-destination");
  const departure = getDateTime("drive");
  const seats = parseInt(document.getElementById("drive-seats").value, 10) || 1;

  if (!origin) return showStatus("drive-status", t("form.needOrigin"), false);
  if (!destination) return showStatus("drive-status", t("form.needDestination"), false);
  if (!departure) return showStatus("drive-status", t("drive.needDeparture"), false);
  const price = drivePriceToPost();
  if (price === undefined) return;

  const btn = document.getElementById("drive-submit");
  btn.disabled = true;
  btn.textContent = t("drive.posting");
  try {
    const data = await postJson("/api/drives", {
      origin,
      destination,
      departure_time: departure,
      seats_available: seats,
      price_chf: price,
    });
    showStatus("drive-status", t("drive.offered", { from: origin.name, to: destination.name }), true);
    ["drive-origin", "drive-destination"].forEach((role) => {
      clearPoint(role);
      document.getElementById(`name-${role}`).value = "";
    });
    updatePriceBand();
    await loadMyDrives();
    showDriveOnMap(data);
  } catch (err) {
    showStatus("drive-status", err.message, false);
  } finally {
    btn.disabled = false;
    btn.textContent = t("drive.submit");
  }
});

// ---------------------------------------------------------------------
// Passenger page: find a ride. The page's own behaviour (the From/To card,
// searching once both ends are set, the time) is in passenger.js.
// ---------------------------------------------------------------------
// The passenger's recent searches: their places are offered under the empty
// From and To fields (passenger.js).
let recentSearches = [];

async function loadRecentSearches() {
  if (!signedIn()) {
    recentSearches = [];
    return;
  }
  try {
    recentSearches = await fetchJson("/api/ride_requests");
  } catch {
    recentSearches = []; // they're a convenience: the fields work without
  }
}

// Whether the passenger chose the time themselves; otherwise a search is
// for now (passenger.js).
let rideTimeChosen = false;

// Both ends of a past search or request, in the form: passenger.js then
// searches, as for any trip whose two ends are set.
function repeatSearch(r) {
  document.getElementById("name-ride-origin").value = r.origin_name;
  document.getElementById("name-ride-destination").value = r.destination_name;
  setPointCoords("ride-origin", r.origin_lat, r.origin_lon, r.origin_name);
  setPointCoords("ride-destination", r.destination_lat, r.destination_lon, r.destination_name);
}

// The passenger form as an API payload (search or ride request), or null
// after showing what's missing in `statusId`.
function rideFormPayload(statusId) {
  const origin = getPoint("ride-origin", "name-ride-origin");
  const destination = getPoint("ride-destination", "name-ride-destination");
  const requestedTime = getDateTime("ride");
  const missing = !origin ? "form.needOrigin" : !destination ? "form.needDestination" : !requestedTime ? "ride.needTime" : null;
  if (missing) {
    showStatus(statusId, t(missing), false);
    return null;
  }
  return {
    origin,
    destination,
    requested_time: requestedTime,
    time_kind: document.getElementById("ride-time-kind").value, // when to leave, or when to be there
  };
}

async function findRide() {
  const payload = rideFormPayload("ride-status");
  if (!payload) return;
  const { origin, destination } = payload;

  const search = ++searchNumber;
  hideStatus("ride-status");
  hideStatus("ride-ask-status");
  document.getElementById("ride-results").hidden = false;
  document.getElementById("ride-matches").innerHTML = `<div class="empty-state">${esc(t("ride.searching"))}</div>`;
  document.getElementById("ride-combined").innerHTML = "";
  document.getElementById("ride-transit").innerHTML = "";
  resultsLayer.clearLayers();
  lastResults = null;

  // The drives come first: finding them waits for nothing. Public transport
  // alone and the rides for part of the way (combined.js) wait for the
  // timetable, and are filled in as they arrive (see renderResults).
  const later = (path, body, key, none) => postJson(path, body).then((part) => part[key], () => none);
  const transitAlone = later("/api/match/transit", payload, "transit_option", null);
  try {
    const data = await postJson("/api/match", { ...payload, drives_only: true });
    if (search !== searchNumber) return; // a newer search has started
    Object.assign(data, { combined_matches: STILL_SEARCHING, transit_option: STILL_SEARCHING });
    lastResults = { data, origin, destination };
    renderResults();
    loadRecentSearches();
    const fillIn = (key) => (value) => {
      if (search !== searchNumber) return;
      data[key] = value;
      renderResults(key);
    };
    transitAlone.then(fillIn("transit_option"));
    later("/api/match/combined", { ...payload, exclude_ids: data.driver_matches.map((m) => m.id) }, "combined_matches", [])
      .then(fillIn("combined_matches"));
  } catch (err) {
    if (search !== searchNumber) return;
    showStatus("ride-status", err.message, false);
    document.getElementById("ride-matches").innerHTML = "";
    document.getElementById("ride-transit").innerHTML = "";
    document.getElementById("ride-results").hidden = true;
  }
}

// Searches start by themselves (passenger.js): only the latest one's answer is shown.
let searchNumber = 0;

// A ride request's time in words: when the passenger wants to leave, or
// "arrive by ..." when it is when they want to be there (time_kind).
function requestTimeText(r) {
  const time = fmtTime(r.requested_time);
  return r.time_kind === "arrive" ? t("common.arriveBy", { time }) : time;
}

// The last search's results, kept to re-render them in another language.
let lastResults = null;

// Scripts loaded after this one (combined.js) add their part of a search's
// results: each function gets the search's answer and the passenger's two ends.
const RESULT_SECTIONS = [];

// A part of the results that hasn't arrived yet ("combined_matches",
// "transit_option"): the page says it is still searching there.
const STILL_SEARCHING = "searching";

// `arrived`: the part that has just come in, when the rest is already shown.
// The drives' cards are then left as they are (one may be open), unless
// there are none: what the page says instead depends on the combined trips.
function renderResults(arrived) {
  const { data, origin, destination } = lastResults;
  if (arrived !== "transit_option") {
    if (!arrived || !data.driver_matches.length) renderMatches(data.driver_matches, origin, destination, data);
    RESULT_SECTIONS.forEach((render) => render(data, origin, destination));
  }
  if (arrived !== "combined_matches") renderTransitOption(data.transit_option);
}

// A card's first line is the passenger's trip, as a timetable shows a
// connection: when it starts and ends, how long it takes and what it costs
// (`priceHtml`: tripPrice's). The arrow at its end says the card opens
// (styles/passenger.css).
function tripLine(startIso, endIso, minutes, priceHtml) {
  return `<div class="trip-line">
    <span class="trip-when">
      <span class="trip-times">${fmtClock(startIso)} → ${fmtClock(endIso)}</span>
      <span class="trip-duration">${esc(fmtMinutes(Math.round(minutes)))}</span>
    </span>
    ${priceHtml}
  </div>`;
}

// The price on a card's first line, with a few words under it: what it is
// compared with, or made of ("ride CHF 4 + train CHF 20": on a narrow card
// that breaks at the "+", not inside an amount).
function tripPrice(amount, note, hint = "") {
  const parts = note.split(" + ").map((part, i) => `<span>${i ? "+ " : ""}${esc(part)}</span>`).join(" ");
  return `<span class="trip-price"><span class="trip-amount">${esc(amount)}</span>${
    note ? `<span class="trip-price-note"${hint ? ` title="${esc(hint)}"` : ""}>${parts}</span>` : ""
  }</span>`;
}

// A ride's price for one seat, and the train fare for the same distance.
function ridePrice(m) {
  if (typeof m.price_chf !== "number") return "";
  const train = m.train_fare;
  const note = train ? t(train.approximate ? "price.trainApprox" : "price.train", { price: fmtChf(train.full_chf) }) : "";
  return tripPrice(t("price.amount", { price: fmtChf(m.price_chf) }), note, train ? t("price.trainHint") : "");
}

// Where the car picks the passenger up and drops them, when that is more
// than `overKm` from their own start or destination: "pickup 0.5 km from
// your start". A card's details say it from the first metre; the card itself
// only beyond a short walk (FAR_GAP_KM), where the trip doesn't end at the
// door.
const FAR_GAP_KM = 1;

function gapsText(m, overKm = 0) {
  return [["combined.pickupGap", m.pickup], ["combined.dropoffGap", m.dropoff]]
    .filter(([, point]) => point.gap_km > overKm)
    .map(([key, point]) => t(key, { km: fmtNumber(point.gap_km) }))
    .join(" · ");
}

function farGapsNote(m) {
  const gaps = gapsText(m, FAR_GAP_KM);
  return gaps ? `<div class="trip-gap">${esc(gaps)}</div>` : "";
}

const CAR_ICON = '<svg class="pt-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 16.5v-4l1.8-4.6a2 2 0 0 1 1.9-1.4h7.6a2 2 0 0 1 1.9 1.4l1.8 4.6v4M3.5 12.5h17M7 16.5v2M17 16.5v2M7.5 14.6h.01M16.5 14.6h.01"/></svg>';

// On a card a stop goes by its town: "Zürich, Bahnhofquai/HB" is "Zürich"
// there, and the details give the stop.
function townOf(stop) {
  return stop ? stop.split(",")[0].trim() : stop;
}

// The two ends of a ride by their towns, unless that is the same town twice.
function townsOf(from, to) {
  const [a, b] = [townOf(from), townOf(to)];
  return a && a === b ? { from, to } : { from: a, to: b };
}

// One part of a trip on a card: the sign of what the passenger rides, in a
// circle, and a few words. In a row they read as a journey: the signs are
// joined by a line (styles/passenger.css).
function tripPart(kind, html) {
  return `<span class="trip-part"><span class="trip-icon ${kind}">${kind === "car" ? CAR_ICON : TRANSIT_ICON}</span><span>${html}</span></span>`;
}

function transitPart(stop) {
  return tripPart("transit", esc(t("combined.transitTo", { place: townOf(stop) })));
}

// Who drives, in words that can't be taken for anything else: "Judith
// drives you to Zürich" (a person, at the wheel, in their car), after the
// car's sign, with their small circle (initial or photo) and their name in
// bold (ratings.js puts the rating next to it). Without a name (signed
// out): "A driver takes you to Zürich". `places`: {from, to}, either one,
// none, or {on: true} ("on from there": the place was just named, by the
// public transport before).
const NAME_HERE = "\u0001";

function whoDrives(m, places = {}) {
  const form = places.on ? "On" : places.from && places.to ? "Between" : places.to ? "To" : places.from ? "From" : "";
  const sentence = t(`match.${m.driver_name ? "drives" : "takes"}${form}`, {
    name: NAME_HERE, from: places.from, to: places.to, place: places.to || places.from,
  });
  const name = `${m.driver_name ? driverAvatar(m) : ""}<span class="match-name">${esc(m.driver_name || t("match.aDriver"))}</span>`;
  return tripPart("car", esc(sentence).replace(NAME_HERE, name));
}

// A trip's parts, in order, one a line: "Judith drives you to Zürich", then
// "Public transport to Sévaz".
function tripHow(parts) {
  return `<div class="trip-how">${parts.filter(Boolean).join("")}</div>`;
}

// A demo ride is fictional and has no button to ask for a seat: where a
// ride has it, the card says what it is, open or not.
function demoNote(m) {
  return m.is_demo ? `<div class="demo-note">${demoBadge(m)}<span>${esc(t("match.demoNote"))}</span></div>` : "";
}

// A step of a trip in a card's details: its times, the sign of what the
// passenger rides, and `html` (a few words, with smaller lines under them).
function tripStep(icon, from, to, html) {
  return `<div class="combined-step">
    <span class="combined-time">${fmtClock(from)} → ${fmtClock(to)}</span>
    <span class="combined-what">${icon}<span>${html}</span></span>
  </div>`;
}

// In the details of a drive for the whole trip: the ride as a step, between
// the stops near which the car picks the passenger up and drops them, by
// their full names, and how far these are from their own start and
// destination. Without a stop's name, those distances alone.
function rideStepDetail(m) {
  const [from, to] = [m.pickup.name, m.dropoff.name];
  const gaps = gapsText(m);
  if (!from && !to) return gaps ? `<div class="meta">${esc(gaps)}</div>` : "";
  const byCar = from && to ? t("combined.carBetween", { from, to }) : t(from ? "combined.carFrom" : "combined.carTo", { place: from || to });
  return `<div class="combined-steps">${tripStep(CAR_ICON, m.pickup.time, m.dropoff.time,
    `${esc(byCar)}${gaps ? `<span class="combined-gap">${esc(gaps)}</span>` : ""}`)}</div>`;
}

function minutesBetween(startIso, endIso) {
  return (new Date(endIso) - new Date(startIso)) / 60000;
}

// For a match's details: the way between the passenger's own point and the
// pickup or drop-off, how long it takes (to the pickup: on foot; from the
// drop-off: on foot, by car and by public transport) and a Google Maps
// directions link (its plain URL scheme: no API, no key). `onward` is the
// API's structured data; the walking time is an estimate, not a route, and
// left out beyond MAX_WALK_SHOWN_MIN. "" for a point practically at the
// passenger's own.
const MAX_WALK_SHOWN_MIN = 60;

function onwardLine(kind, point, passengerPoint, onward) {
  if (!(kind === "pickup" ? point.walk_min : onward)) return "";
  const minutes = kind === "pickup"
    ? [["match.onwardWalk", point.walk_min]]
    : [["match.onwardWalk", onward.walk_min], ["match.onwardCar", onward.car_min], ["match.onwardTransit", onward.transit_min]];
  // A walk of more than an hour isn't proposed: by car or public transport then.
  const shown = minutes.filter(([key, min]) => min && !(key === "match.onwardWalk" && min > MAX_WALK_SHOWN_MIN));
  const [from, to] = kind === "pickup" ? [passengerPoint, point] : [point, passengerPoint];
  const url = "https://www.google.com/maps/dir/?api=1"
    + `&origin=${from.lat},${from.lon}&destination=${to.lat},${to.lon}`;
  const label = t(kind === "pickup" ? "match.firstMile" : "match.lastMile");
  const times = shown.map(([key, min]) => esc(t(key, { duration: fmtMinutes(min) }))).join(" · ");
  return `<div class="meta">${esc(label)} ${times}
    <a class="directions-link" href="${esc(url)}" target="_blank" rel="noopener">${esc(t("match.directions"))}</a></div>`;
}

// Who drives, in a circle: the first letter of their name (profiles.js puts
// their photo over it), or a figure when the name isn't shown (signed out).
const PERSON_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="9" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0z"/></svg>';

function driverAvatar(m) {
  const initial = (m.driver_name || "").trim().charAt(0).toUpperCase();
  return `<span class="avatar" aria-hidden="true">${initial ? esc(initial) : PERSON_ICON}</span>`;
}

// The drives that serve the trip, the best first (see backend/search.py).
// `limits`: the search's answer, for its two distances. A card says when the
// passenger gets in and out, how long the ride takes and its price, then
// who drives them from where to where; a
// click opens its details, and "Show on map" there opens the map on that
// drive's route. Without any, asking the drivers is what the page proposes
// (styles/passenger.css: .no-matches).
function renderMatches(matches, passengerOrigin, passengerDestination, limits = {}) {
  const container = document.getElementById("ride-matches");
  container.classList.remove("has-selection");
  // A ride for part of the way (combined.js) is something to choose too.
  // While those are still being looked for, nothing is said about there being none.
  const searching = limits.combined_matches === STILL_SEARCHING;
  const combined = searching ? 0 : (limits.combined_matches || []).length;
  document.getElementById("ride-results").classList.toggle("no-matches", !matches.length && !combined && !searching);
  document.getElementById("ride-results").classList.toggle("no-direct", !matches.length && combined > 0);
  if (!matches.length && searching) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.searching"))}</div>`;
    return;
  }
  // No drive for the whole way, but rides for part of it: their own title says what they are.
  if (!matches.length && combined) {
    container.innerHTML = "";
    return;
  }
  if (!matches.length) {
    container.innerHTML = `<div class="no-rides">
      <img src="icons/no-rides.svg" alt="" width="150" height="84" />
      <div class="no-rides-text">${esc(t("ride.noMatches"))}</div>
    </div>`;
    return;
  }
  container.innerHTML = "";
  // Nothing bookable passes near: the search looked further away.
  if (matches.some((m) => m.wider)) {
    const note = document.createElement("div");
    note.className = "hint wider-note";
    note.textContent = t("ride.widerNote", {
      near: fmtNumber(limits.search_radius_km),
      far: fmtNumber(limits.wider_radius_km),
    });
    container.appendChild(note);
  }

  L.marker([passengerOrigin.lat, passengerOrigin.lon], { title: t("map.pickupTitle") })
    .addTo(resultsLayer)
    .bindPopup(esc(t("map.pickupPopup", { name: passengerOrigin.name })));
  L.marker([passengerDestination.lat, passengerDestination.lon], { title: t("map.destinationTitle") })
    .addTo(resultsLayer)
    .bindPopup(esc(t("map.destinationPopup", { name: passengerDestination.name })));

  const routeColors = ["#1a73e8", "#8e24aa", "#00897b", "#f9a825", "#6d4c41"];
  // One entry per match: its card, route line (if any) and colour.
  const shown = [];
  let selected = null;
  let meetingMarkers = [];

  // Showing a match on the map highlights its route, with where you'd get in
  // and out, and fades the others; again, and they all show as before.
  function select(entry) {
    selected = selected === entry ? null : entry;
    meetingMarkers.forEach((marker) => resultsLayer.removeLayer(marker));
    meetingMarkers = [];
    shown.forEach((s) => {
      s.card.classList.toggle("selected", s === selected);
      if (!s.line) return;
      if (!selected) s.line.setStyle({ color: s.color, weight: 4, opacity: 0.7 });
      else if (s === selected) s.line.setStyle({ color: s.color, weight: 7, opacity: 1 }).bringToFront();
      else s.line.setStyle({ color: "#9aa0a6", weight: 3, opacity: 0.35 });
    });
    container.classList.toggle("has-selection", !!selected);
    if (!selected) return fitToResults();

    const m = selected.match;
    const bounds = L.latLngBounds([
      [passengerOrigin.lat, passengerOrigin.lon],
      [passengerDestination.lat, passengerDestination.lon],
      [m.pickup.lat, m.pickup.lon],
      [m.dropoff.lat, m.dropoff.lon],
    ]);
    [
      [m.pickup, "map.pickupPoint", "ride-origin"],
      [m.dropoff, "map.dropoffPoint", "ride-destination"],
    ].forEach(([point, key, role]) => {
      const marker = L.circleMarker([point.lat, point.lon], {
        radius: 7, color: PICK_COLORS[role], fillColor: "#fff", fillOpacity: 1, weight: 3,
      })
        .addTo(resultsLayer)
        .bindTooltip(esc(t(key, { time: fmtClock(point.time) })), { permanent: true, direction: "top" });
      meetingMarkers.push(marker);
    });
    map.fitBounds(bounds, { padding: [30, 30] });
    revealMap();
  }

  matches.forEach((m, idx) => {
    const card = document.createElement("div");
    card.className = "match-card clickable";
    const color = routeColors[idx % routeColors.length];
    card.style.borderLeftColor = color;
    card.innerHTML = `
      ${tripLine(m.pickup.time, m.dropoff.time, minutesBetween(m.pickup.time, m.dropoff.time), ridePrice(m))}
      ${tripHow([whoDrives(m, townsOf(m.pickup.name, m.dropoff.name))])}
      ${farGapsNote(m)}
      ${m.wider ? `<span class="badge wider">${esc(t("match.wider"))}</span>` : ""}
      ${demoNote(m)}
      <div class="match-details" hidden>
        ${rideStepDetail(m)}
        <div class="meta">${esc(m.origin_name)} → ${esc(m.destination_name)} · ${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))}</div>
        ${onwardLine("pickup", m.pickup, passengerOrigin, null)}
        ${onwardLine("dropoff", m.dropoff, passengerDestination, m.dropoff.onward)}
        ${timeDiffBadge(m.time_diff_min)}
        <span class="badge">${esc(t("match.rideKm", { km: fmtNumber(m.ride_km) }))}</span>
        ${renderTransitLeg("match.firstMile", m.first_mile_transit)}
        ${renderTransitLeg("match.lastMile", m.last_mile_transit)}
        <button type="button" class="link-btn match-on-map">${esc(t("match.showOnMap"))}</button>
      </div>
    `;
    container.appendChild(card);
    cardExtras("match", m, card);

    const entry = { match: m, card, color, line: null };
    shown.push(entry);
    // A demo ride is fictional: it opens and shows on the map like any ride,
    // but can't be booked (bookings.js gives it no button).
    if (m.is_demo) card.classList.add("demo");
    const details = card.querySelector(".match-details");
    // The driver's profile belongs to the details; "Details" closes the card.
    const profile = card.querySelector(":scope > .profile-link-box");
    if (profile) details.appendChild(profile);
    const more = document.createElement("button");
    more.type = "button";
    more.className = "link-btn match-more";
    more.textContent = t("match.details");
    more.setAttribute("aria-expanded", "false");
    // Below the price: the booking's button (bookings.js), "Details", the details.
    card.appendChild(more);
    card.appendChild(details);
    card.addEventListener("click", (e) => {
      if (e.target.closest("a")) return; // directions and ticket links
      if (e.target.closest(".match-on-map")) return selected === entry ? revealMap() : select(entry);
      details.hidden = !details.hidden;
      card.classList.toggle("open", !details.hidden);
      more.setAttribute("aria-expanded", String(!details.hidden));
    });

    if (m.route_geometry && m.route_geometry.length) {
      const latlngs = m.route_geometry.map(([lon, lat]) => [lat, lon]);
      entry.line = L.polyline(latlngs, { color, weight: 4, opacity: 0.7 })
        .addTo(resultsLayer)
        .bindTooltip(`${m.driver_name ? `${esc(m.driver_name)}: ` : ""}${esc(m.origin_name)} → ${esc(m.destination_name)}`, { sticky: true })
        .on("click", () => {
          if (selected !== entry) select(entry);
          // On phones that would scroll the map away from under the finger.
          if (!PHONE_LAYOUT.matches) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
    }
  });

  fitToResults();
}

// How far the pickup is from the time the passenger asked for.
function timeDiffBadge(minutes) {
  if (Math.abs(minutes) < MINUTE_STEP) return `<span class="score">${esc(t("match.onTime"))}</span>`;
  const key = minutes > 0 ? "match.later" : "match.earlier";
  const warn = Math.abs(minutes) > 60 ? "warn" : "";
  return `<span class="score ${warn}">${esc(t(key, { duration: fmtMinutes(Math.abs(minutes)) }))}</span>`;
}

function fmtMinutes(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} h ${pad2(m)}` : `${m} min`;
}

// Public transport to the pickup or from the drop-off (backend/transit_legs.py).
function renderTransitLeg(labelKey, leg) {
  if (!leg) return "";
  return `<div class="last-mile"><strong>${esc(t(labelKey))}</strong> ${esc(leg.from_station)} → ${esc(
    leg.to_station
  )}${renderConnections(leg.connections)}</div>`;
}

// Public transport for the whole trip, under the rides: one line (how many
// connections, the first one's times and duration), opened on a click. The
// first is the one to take: the next to leave or, to arrive by a time, the
// last that gets there (transit.py: best_first_to_arrive_by).
const TRANSIT_ICON = '<svg class="pt-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="5.5" y="3.5" width="13" height="13" rx="3"/><path d="M5.5 10.5h13M9 13.6h.01M15 13.6h.01M8.5 16.5l-2 4M15.5 16.5l2 4"/></svg>';

function renderTransitOption(transit) {
  const container = document.getElementById("ride-transit");
  if (transit === STILL_SEARCHING) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.searching"))}</div>`;
    return;
  }
  if (!transit || !transit.connections.length) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.noTransit"))}</div>`;
    return;
  }
  const open = container.querySelector("details")?.open; // stays open when the language changes
  const first = transit.connections[0];
  const summary = t("transit.summary", {
    count: transit.connections.length, departure: fmtClock(first.departure), arrival: fmtClock(first.arrival),
    duration: fmtDuration(first.duration),
  });
  container.innerHTML = `<details class="transit-box alt"${open ? " open" : ""}>
    <summary>${TRANSIT_ICON}<span class="pt-title">${esc(t("ride.transitTitle"))}</span><span class="pt-summary">${esc(summary)}</span></summary>
    <div class="pt-stations">${esc(transit.from_station)} → ${esc(transit.to_station)}</div>
    ${renderFare(transit.fare)}${renderConnections(transit.connections)}
  </details>`;
}

// The train ticket's price, 2nd class (see fares.py): from the SBB price
// service when the server has a key, else estimated from the distance.
function renderFare(fare) {
  return fare ? `<div class="pt-fare">${esc(fareText(fare))}</div>` : "";
}

function fareText(fare) {
  const key = fare.approximate ? "transit.fareApprox" : "transit.fare";
  return t(key, { full: fmtChf(fare.full_chf), half: fmtChf(fare.half_chf) });
}

// One row per connection: times, duration and transfers, the lines taken
// and who runs them, as plain text, and a link to its tickets on SBB (one
// ticket covers every Swiss operator on the journey).
function renderConnections(connections) {
  return connections
    .map((c) => `
        <div class="pt-conn">
          <div class="pt-body">
            <div class="pt-times"><span class="pt-clock">${fmtClock(c.departure)} → ${fmtClock(c.arrival)}</span><span class="pt-meta">${esc(connectionLength(c))}</span></div>
            <div class="pt-lines">${esc(connectionLines(c))}</div>
          </div>
          ${ticketsLink(c)}
        </div>`)
    .join("");
}

// "2 h 28 min · 2 transfers"
function connectionLength(c) {
  return `${fmtDuration(c.duration)} · ${t("transit.transfers", { count: c.transfers })}`;
}

// The lines taken and who runs them: "IC 1 · S 30 · B 554 (SBB CFF FFS, TPF)".
function connectionLines(c) {
  const lines = (c.legs || [])
    .map((l) => [l.category, l.number].filter(Boolean).join(" ") || l.operator?.code || "?")
    .join(" · ");
  const operators = (c.operators || []).map((o) => o.name).join(", ");
  return `${lines}${operators ? ` (${operators})` : ""}`;
}

function ticketsLink(c) {
  return c.booking_url
    ? `<a class="pt-book" href="${esc(sbbUrl(c.booking_url))}" target="_blank" rel="noopener">${esc(t("transit.tickets"))}</a>`
    : "";
}

// SBB's site in the visitor's language: the backend links to /en.
function sbbUrl(url) {
  return url.replace(/^(https:\/\/www\.sbb\.ch\/)(de|fr|it|en)(?=[/?]|$)/, `$1${I18N.lang()}`);
}

function fmtNumber(n) {
  return typeof n === "number" ? n.toLocaleString(I18N.locale()) : n;
}

function fmtClock(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "?";
  return d.toLocaleTimeString(I18N.locale(), { hour: "2-digit", minute: "2-digit" });
}

// transport.opendata.ch durations look like "00d01:44:00".
function fmtDuration(duration) {
  const m = /^(\d+)d(\d+):(\d+)/.exec(duration || "");
  if (!m) return duration || "";
  const hours = parseInt(m[1], 10) * 24 + parseInt(m[2], 10);
  const minutes = parseInt(m[3], 10);
  return hours ? `${hours} h ${String(minutes).padStart(2, "0")} min` : `${minutes} min`;
}

function fmtTime(iso) {
  if (!iso) return "?";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(I18N.locale(), { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------
// Ride requests: ask the drivers who often make a trip (see notify.py)
// ---------------------------------------------------------------------
document.getElementById("ride-ask").addEventListener("click", async () => {
  if (!signedIn()) return goToSignIn();
  const payload = rideFormPayload("ride-ask-status");
  if (!payload) return;
  const btn = document.getElementById("ride-ask");
  btn.disabled = true;
  try {
    const data = await postJson("/api/requests", payload);
    // Only real drivers are counted. A demo driver's request is a demo one:
    // it only reaches demo drivers, who are named.
    const message = !data.drivers_told
      ? t("ride.askSentNone")
      : data.demo_request
        ? t("ride.askSentDemo", { count: data.drivers_told, names: data.demo_drivers_told.join(", ") })
        : t("ride.askSent", { count: data.drivers_told });
    showStatus("ride-ask-status", message, true);
    loadMyRequests();
  } catch (err) {
    showStatus("ride-ask-status", err.message, false);
  } finally {
    btn.disabled = false;
  }
});

async function loadMyRequests() {
  const list = document.getElementById("ride-requests");
  const show = (shown) => {
    list.hidden = !shown;
    document.getElementById("ride-requests-title").hidden = !shown;
  };
  if (!signedIn()) return show(false);
  try {
    const requests = await fetchJson("/api/requests");
    show(requests.length > 0); // the section is there when it has something
    list.innerHTML = "";
    requests.forEach((r) => {
      const card = document.createElement("div");
      card.className = "drive-card clickable";
      card.innerHTML = `
        <div class="route">${esc(r.origin_name)} → ${esc(r.destination_name)}</div>
        <div class="meta">${esc(requestTimeText(r))}</div>
        <button type="button" class="link-btn">${esc(t("ride.cancelRequest"))}</button>
      `;
      card.addEventListener("click", () => searchForRequest(r));
      card.querySelector(".link-btn").addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await fetchJson(`/api/requests/${r.id}`, { method: "DELETE" });
          loadMyRequests();
        } catch (err) {
          showStatus("ride-ask-status", err.message, false);
        }
      });
      list.appendChild(card);
    });
  } catch (err) {
    show(true);
    list.innerHTML = `<div class="empty-state">${esc(t("ride.requestsLoadFailed", { error: err.message }))}</div>`;
  }
}

// Fill the passenger form with a request (its trip and time) and search.
function searchForRequest(r) {
  goTo("ride");
  setDateTime("ride", new Date(r.requested_time));
  document.getElementById("ride-time-kind").value = r.time_kind === "arrive" ? "arrive" : "depart";
  rideTimeChosen = true;
  repeatSearch(r);
}

// ---------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------
const UNREAD_REFRESH_MS = 60 * 1000;

function showUnread(count) {
  const badge = document.getElementById("unread-count");
  badge.textContent = count;
  badge.hidden = !count;
}

async function refreshUnread() {
  if (!signedIn()) return showUnread(0);
  try {
    const data = await fetchJson("/api/notifications");
    showUnread(data.unread);
  } catch {
    // keep the last count
  }
}

setInterval(refreshUnread, UNREAD_REFRESH_MS);

async function loadInbox() {
  const list = document.getElementById("inbox-list");
  try {
    const data = await fetchJson("/api/notifications");
    renderInbox(data);
    if (data.unread) {
      await fetchJson("/api/notifications/read", { method: "POST" });
      showUnread(0);
    }
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("inbox.loadFailed", { error: err.message }))}</div>`;
  }
}

function renderInbox(data) {
  document.getElementById("inbox-notify-requests").checked = data.notify_requests;
  document.getElementById("inbox-notify-email").checked = data.notify_email;
  const paused = document.getElementById("inbox-paused");
  paused.hidden = !data.paused_until;
  if (data.paused_until) {
    document.getElementById("inbox-paused-text").textContent = t("inbox.paused", { date: fmtDate(data.paused_until) });
  }

  const list = document.getElementById("inbox-list");
  if (!data.notifications.length) {
    list.innerHTML = `<div class="empty-state">${esc(t("inbox.empty"))}</div>`;
    return;
  }
  list.innerHTML = "";
  data.notifications.forEach((n) => list.appendChild(notificationCard(n)));
}

// Kinds of notification added by scripts loaded after this one
// (bookings.js): kind -> a function that fills the card.
const NOTIFICATION_CARDS = {};

// Scripts loaded after this one (bookings.js) add to the cards of the ride
// requests a driver is shown, in the inbox and in the list of their areas
// (areas.js): each function gets the request, the element holding the
// card's buttons, and what the card knows more ({ notification } in the
// inbox).
const REQUEST_EXTRAS = [];

function requestExtras(request, actions, context = {}) {
  REQUEST_EXTRAS.forEach((extra) => extra(request, actions, context));
}

function notificationCard(n) {
  const card = document.createElement("div");
  card.className = `drive-card notification${n.read_at ? "" : " unread"}`;
  if (NOTIFICATION_CARDS[n.kind]) {
    NOTIFICATION_CARDS[n.kind](n, card);
    return card;
  }
  const r = n.request;
  const trip = r ? `<div class="meta">${esc(r.origin_name)} → ${esc(r.destination_name)} · ${esc(requestTimeText(r))}</div>` : "";
  const open = r && r.status === "open" && new Date(r.requested_time) > new Date();

  if (n.kind === "request_for_driver") {
    // The driver was chosen for a trip they usually make, or for an area
    // of theirs the request starts or ends in (see notify.py).
    const usual = n.details?.trip;
    const area = n.details?.area;
    const title = n.passengers > 1
      ? t("inbox.requestTitleMany", { count: n.passengers })
      : t("inbox.requestTitle", { name: r?.passenger_name || "?" });
    card.innerHTML = `
      <div class="route">${esc(title)}</div>
      ${trip}
      ${usual ? `<div class="meta">${esc(t("inbox.requestWhy", { from: usual.origin.name, to: usual.destination.name, time: usual.time, count: usual.times }))}</div>` : ""}
      ${area ? `<div class="meta">${esc(requestInAreaText(area, n.details.ends || []))}</div>` : ""}
      <div class="notification-actions"></div>
    `;
    const actions = card.querySelector(".notification-actions");
    if (n.action) actions.innerHTML = `<span class="hint">${esc(t(`inbox.done.${n.action}`))}</span>`;
    else if (!open) actions.innerHTML = `<span class="hint">${esc(t("inbox.noLongerOpen"))}</span>`;
    else {
      actions.append(
        actionButton("inbox.offer", "submit-btn", () => offerDrive(n)),
        actionButton("inbox.decline", "submit-btn secondary", () => actOn(n, "decline")),
        actionButton("inbox.mute", "link-btn", () => actOn(n, "mute")),
      );
      requestExtras(r, actions, { notification: n });
    }
  } else if (n.kind === "drive_cancelled") {
    // The drive is deleted; the notification keeps what it was.
    const d = n.details?.drive || {};
    card.innerHTML = `
      <div class="route">${esc(t("inbox.cancelledTitle"))}</div>
      ${trip}
      <div class="meta">${esc(d.driver_name)} · ${esc(d.origin_name)} → ${esc(d.destination_name)}, ${fmtTime(d.departure_time)}${demoBadge(d)}</div>
      <div class="notification-actions"></div>
    `;
    if (open) {
      card.querySelector(".notification-actions").append(
        actionButton("inbox.seeMatches", "submit-btn", () => searchForRequest(r)),
      );
    }
  } else {
    const drives = (n.drives || [])
      .map((d) => `<div class="meta">${esc(d.driver_name)} · ${esc(d.origin_name)} → ${esc(d.destination_name)}, ${fmtTime(d.departure_time)}${demoBadge(d)}</div>`)
      .join("");
    card.innerHTML = `
      <div class="route">${esc(t("inbox.driveTitle", { count: (n.drives || []).length }))}</div>
      ${trip}
      ${drives}
      <div class="notification-actions"></div>
    `;
    if (r) {
      card.querySelector(".notification-actions").append(
        actionButton("inbox.seeMatches", "submit-btn", async () => {
          await actOn(n, "view", false);
          searchForRequest(r);
        }),
      );
    }
  }
  return card;
}

function actionButton(key, className, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.textContent = t(key);
  btn.addEventListener("click", onClick);
  return btn;
}

async function actOn(n, action, reload = true) {
  try {
    await postJson(`/api/notifications/${n.id}/action`, { action });
    if (reload) loadInbox();
  } catch (err) {
    alert(err.message);
  }
}

function requestInAreaText(area, ends) {
  if (ends.length > 1) return t("inbox.requestInAreas");
  return ends[0] === "destination"
    ? t("inbox.requestEndsInArea", { area: area.name })
    : t("inbox.requestStartsInArea", { area: area.name });
}

// "Offer this drive": the Driver form, filled with the driver's usual trip
// and a departure that reaches the passenger at their time; for a driver
// chosen by area, with the passenger's own trip and time. The driver still
// checks it and clicks "Offer drive".
async function offerDrive(n) {
  await actOn(n, "offer", false);
  const name = n.request?.passenger_name || "";
  if (n.details?.trip) {
    fillDriveForm(n.details.trip.origin, n.details.trip.destination, n.details.suggested_departure);
    showStatus("drive-status", t("inbox.offerFilled", { name }), true);
  } else {
    fillDriveFormForRequest(n.request);
    showStatus("drive-status", t("inbox.offerFilledArea", { name }), true);
  }
}

// The Driver form with a trip ({name, lat, lon} at each end) and a
// departure time, in view.
function fillDriveForm(origin, destination, departure) {
  goTo("drive");
  [["drive-origin", origin], ["drive-destination", destination]].forEach(([role, place]) => {
    setPointCoords(role, place.lat, place.lon, place.name);
    document.getElementById(`name-${role}`).value = place.name;
  });
  setDateTime("drive", new Date(departure));
  document.getElementById("drive-submit").scrollIntoView({ block: "center", behavior: "smooth" });
}

// The Driver form with a passenger's ride request: their trip and time (for
// a time to arrive by: when the trip has to start, the request's leave_by).
function fillDriveFormForRequest(r) {
  fillDriveForm(
    { name: r.origin_name, lat: r.origin_lat, lon: r.origin_lon },
    { name: r.destination_name, lat: r.destination_lat, lon: r.destination_lon },
    r.leave_by || r.requested_time,
  );
}

function fmtDate(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(I18N.locale(), { day: "numeric", month: "long" });
}

document.getElementById("inbox-notify-requests").addEventListener("change", (e) => updateNotificationSettings({ notify_requests: e.target.checked }));
document.getElementById("inbox-notify-email").addEventListener("change", (e) => updateNotificationSettings({ notify_email: e.target.checked }));
document.getElementById("inbox-resume").addEventListener("click", () => updateNotificationSettings({ resume: true }));

async function updateNotificationSettings(changes) {
  try {
    const data = await postJson("/api/notification_settings", changes, "PUT");
    renderInbox(data);
  } catch (err) {
    alert(err.message);
  }
}

// ---------------------------------------------------------------------
// A drive on the map (a booking's: bookings.js)
// ---------------------------------------------------------------------
// Lists come without route geometry (it's large); fetch it for the drive shown.
async function showDriveOnMap(drive) {
  if (!drive.route_geometry) {
    try {
      drive = await fetchJson(`/api/drives/${drive.id}`);
    } catch {
      // Drive gone or network error: show its start and end without the route.
    }
  }
  resultsLayer.clearLayers();
  if (drive.route_geometry && drive.route_geometry.length) {
    const latlngs = drive.route_geometry.map(([lon, lat]) => [lat, lon]);
    const line = L.polyline(latlngs, { color: "#1a73e8", weight: 4 }).addTo(resultsLayer);
    map.fitBounds(line.getBounds(), { padding: [30, 30] });
  } else {
    map.setView([drive.origin_lat, drive.origin_lon], 10);
  }
  L.marker([drive.origin_lat, drive.origin_lon]).addTo(resultsLayer).bindPopup(esc(drive.origin_name)).openPopup();
  L.marker([drive.destination_lat, drive.destination_lon]).addTo(resultsLayer).bindPopup(esc(drive.destination_name));
  revealMap();
}

// ---------------------------------------------------------------------
// Date + time pickers: times in 5-minute steps, like SBB's timetable.
// ---------------------------------------------------------------------
const MINUTE_STEP = 5;

const pad2 = (n) => String(n).padStart(2, "0");

function setupDateTime(prefix) {
  const options = (values) => values.map((v) => `<option value="${pad2(v)}">${pad2(v)}</option>`).join("");
  document.getElementById(`${prefix}-hour`).innerHTML = options([...Array(24).keys()]);
  document.getElementById(`${prefix}-minute`).innerHTML = options(
    [...Array(60 / MINUTE_STEP).keys()].map((i) => i * MINUTE_STEP)
  );
}

// Rounded up to the next 5 minutes.
function setDateTime(prefix, date) {
  const step = MINUTE_STEP * 60 * 1000;
  const d = new Date(Math.ceil(date.getTime() / step) * step);
  document.getElementById(`${prefix}-date`).value = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  document.getElementById(`${prefix}-hour`).value = pad2(d.getHours());
  document.getElementById(`${prefix}-minute`).value = pad2(d.getMinutes());
}

// "2026-11-01T16:25" (local time, like the backend expects), or "" without a date.
function getDateTime(prefix) {
  const date = document.getElementById(`${prefix}-date`).value;
  if (!date) return "";
  return `${date}T${document.getElementById(`${prefix}-hour`).value}:${document.getElementById(`${prefix}-minute`).value}`;
}

// A drive is offered for "in 2 hours" unless changed; a search is for now
// (passenger.js sets it again when the search starts).
["drive", "ride"].forEach(setupDateTime);
setDateTime("drive", new Date(Date.now() + 2 * 60 * 60 * 1000));
setDateTime("ride", new Date());

function translateUnsetReadouts() {
  Object.keys(PICK_COLORS).forEach((role) => {
    const readout = document.getElementById(`readout-${role}`);
    if (readout && !points[role]) readout.textContent = t("form.notSet");
  });
}

// Another language picked: redo everything built in JavaScript.
window.addEventListener("i18n:change", () => {
  renderAccount();
  renderModeSwitch();
  ["signin-status", "drive-status", "ride-status", "profile-status", "account-status"].forEach(hideStatus);
  translateUnsetReadouts();
  renderPriceBand();
  route();
  // The search page is also what the site opens on, without "#/ride".
  if (lastResults && document.getElementById("page-ride").classList.contains("active")) renderResults();
});

I18N.ready.then(async () => {
  translateUnsetReadouts();
  renderModeSwitch();
  renderPriceBand();
  await loadCurrentUser();
  route();
});
