"use strict";

// ---------------------------------------------------------------------
// Map setup
// ---------------------------------------------------------------------
const map = L.map("map").setView([46.8182, 8.2275], 8); // Switzerland
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

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
}

document.querySelectorAll(".point-picker button[data-role]").forEach((btn) => {
  btn.addEventListener("click", () => {
    activePickRole = activePickRole === btn.dataset.role ? null : btn.dataset.role;
    setPicking(activePickRole);
  });
});

map.on("click", async (e) => {
  if (!activePickRole) return;
  const role = activePickRole;
  const { lat, lng } = e.latlng;

  setPointCoords(role, lat, lng, null);
  activePickRole = null;
  setPicking(null);

  const name = await reverseGeocode(lat, lng);
  if (points[role]) {
    points[role].name = name;
    const nameInput = document.getElementById(`name-${role}`);
    if (nameInput && !nameInput.value.trim()) nameInput.value = name;
  }
});

// kind: "place" (a town picked by name -- public transport then starts/ends at
// its train station), "address", or "point" (clicked on the map).
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
}

// ---------------------------------------------------------------------
// Passenger search radii: a circle around the start and the destination,
// sized by the sliders. Drives whose route passes through both match.
// ---------------------------------------------------------------------
const RADIUS_SLIDERS = {
  "ride-origin": "ride-pickup-radius",
  "ride-destination": "ride-dropoff-radius",
};

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

Object.entries(RADIUS_SLIDERS).forEach(([role, sliderId]) => {
  const slider = document.getElementById(sliderId);
  const output = document.getElementById(`${sliderId}-value`);
  const update = () => {
    output.textContent = `${slider.value} km`;
    if (points[role]?.circle) points[role].circle.setRadius(slider.value * 1000);
  };
  slider.addEventListener("input", update);
  slider.addEventListener("change", () => zoomToTrip("ride"));
  update();
});

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

async function searchPhoton(query) {
  try {
    // Places near where the map is looking first.
    const center = map.getCenter();
    const resp = await fetch(
      `${PHOTON_URL}?q=${encodeURIComponent(query)}&limit=12&lang=${I18N.lang()}&bbox=${PHOTON_BBOX}&lat=${center.lat.toFixed(4)}&lon=${center.lng.toFixed(4)}`
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
      // "address": public transport starts/ends at the nearest stop.
      pois.push({ label, detail: [street, town, country].filter(Boolean).join(", "), lat, lon, kind: "address" });
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
          `<li data-index="${i}" class="${i === activeIndex ? "active" : ""}">${esc(it.label)}${
            it.detail ? `<span class="suggest-detail">${esc(it.detail)}</span>` : ""
          }</li>`
      )
      .join("");
    list.classList.toggle("show", items.length > 0);
  }

  function selectItem(it) {
    input.value = it.label;
    setPointCoords(role, it.lat, it.lon, it.label, it.kind);
    closeList();
  }

  let searchCount = 0;
  const runSearch = debounce(async (query) => {
    const search = ++searchCount;
    if (query.trim().length < 3) {
      closeList();
      return;
    }
    const found = await searchPlaces(query);
    if (search !== searchCount) return; // a newer search has started
    items = found;
    activeIndex = -1;
    renderList();
  }, 300);

  input.addEventListener("input", () => runSearch(input.value));

  input.addEventListener("keydown", (e) => {
    if (!items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = (activeIndex + 1) % items.length;
      renderList();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = (activeIndex - 1 + items.length) % items.length;
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
    selectItem(items[Number(li.dataset.index)]);
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

// API errors carry a code (see main.py) that maps to "errors.<code>".
async function fetchJson(url, options) {
  const resp = await fetch(url, options);
  const data = await resp.json();
  if (!resp.ok) {
    const code = data.detail?.code;
    throw new Error(code ? t(`errors.${code}`) : t("errors.request_failed", { status: resp.status }));
  }
  return data;
}

// ---------------------------------------------------------------------
// Current user -- just a first + last name, remembered in this browser.
// (No real auth in this prototype; the backend auto-registers any name.)
// ---------------------------------------------------------------------
const USER_KEY = "rideshare.user";

function loadUser() {
  try {
    const u = JSON.parse(localStorage.getItem(USER_KEY));
    return u && u.first && u.last ? u : null;
  } catch {
    return null;
  }
}

let currentUser = loadUser();

function fullName(user) {
  return `${user.first} ${user.last}`;
}

function setUser(user) {
  currentUser = user;
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
  } catch {
    // storage unavailable (private window etc.) -- fine for this session
  }
  renderAccount();
}

function renderAccount() {
  const el = document.getElementById("account");
  if (!currentUser) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `<span>${esc(fullName(currentUser))}</span> <button type="button" id="signout">${esc(t("account.signOut"))}</button>`;
  document.getElementById("signout").addEventListener("click", () => {
    setUser(null);
    route();
  });
}

document.getElementById("signin-submit").addEventListener("click", () => {
  const first = document.getElementById("signin-first-name").value.trim();
  const last = document.getElementById("signin-last-name").value.trim();
  if (!first) return showStatus("signin-status", t("signin.needFirstName"), false);
  if (!last) return showStatus("signin-status", t("signin.needLastName"), false);
  setUser({ first, last });
  route();
});

// ---------------------------------------------------------------------
// Pages: #/ride (passenger, the default), #/browse (public), #/drive
// (driver), #/inbox
// ---------------------------------------------------------------------
const PAGES = {
  ride: { needsUser: true, onShow: () => { loadMyRequests(); loadRecentSearches(); } },
  browse: { needsUser: false, onShow: loadBrowse },
  drive: { needsUser: true, onShow: loadMyDrives },
  inbox: { needsUser: true, onShow: loadInbox },
};
const DEFAULT_PAGE = "ride";

function route() {
  const requested = location.hash.replace(/^#\/?/, "");
  const page = PAGES[requested] ? requested : DEFAULT_PAGE;
  const shown = PAGES[page].needsUser && !currentUser ? "signin" : page;

  document.querySelectorAll(".tab-link").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("active", s.id === `page-${shown}`));
  document.getElementById("picker-hint").hidden = !(shown === "ride" || shown === "drive");

  // Only show the picked origin/destination pins that belong to this page.
  activePickRole = null;
  setPicking(null);
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

// Prices are placeholders until pricing is designed (see pricing.py).
function priceBadge(chf) {
  if (typeof chf !== "number") return "";
  const price = chf.toLocaleString(I18N.locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `<span class="badge price">${esc(t("price.demo", { price }))}</span>`;
}

function driveCardHtml(d) {
  const km = d.route_distance_m ? (d.route_distance_m / 1000).toFixed(1) : "?";
  return `
    <div class="route">${esc(d.origin_name)} → ${esc(d.destination_name)}${demoBadge(d)}</div>
    <div class="meta">${fmtTime(d.departure_time)} · ${esc(t("common.seats", { count: d.seats_available }))} · ${km} km</div>
    ${priceBadge(d.price_chf)}
  `;
}

async function loadMyDrives() {
  const list = document.getElementById("drive-mine");
  list.innerHTML = `<div class="empty-state">${esc(t("common.loading"))}</div>`;
  try {
    const drives = await fetchJson(`/api/drives?driver_name=${encodeURIComponent(fullName(currentUser))}`);
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
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("drive.loadFailed", { error: err.message }))}</div>`;
  }
}

async function cancelDrive(d) {
  if (!confirm(t("drive.cancelConfirm", { from: d.origin_name, to: d.destination_name }))) return;
  try {
    await fetchJson(`/api/drives/${d.id}?driver_name=${encodeURIComponent(fullName(currentUser))}`, {
      method: "DELETE",
    });
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

  const btn = document.getElementById("drive-submit");
  btn.disabled = true;
  btn.textContent = t("drive.posting");
  try {
    const data = await fetchJson("/api/drives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        driver_name: fullName(currentUser),
        origin,
        destination,
        departure_time: departure,
        seats_available: seats,
      }),
    });
    showStatus("drive-status", t("drive.offered", { from: origin.name, to: destination.name }), true);
    ["drive-origin", "drive-destination"].forEach((role) => {
      clearPoint(role);
      document.getElementById(`name-${role}`).value = "";
    });
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
// Passenger page: find a ride + your recent searches
// ---------------------------------------------------------------------
async function loadRecentSearches() {
  const list = document.getElementById("ride-recent");
  try {
    const searches = await fetchJson(`/api/ride_requests?passenger_name=${encodeURIComponent(fullName(currentUser))}`);
    if (!searches.length) {
      list.innerHTML = `<div class="empty-state">${esc(t("ride.recentEmpty"))}</div>`;
      return;
    }
    list.innerHTML = "";
    searches.forEach((r) => {
      const card = document.createElement("div");
      card.className = "drive-card clickable";
      card.innerHTML = `
        <div class="route">${esc(r.origin_name)} → ${esc(r.destination_name)}</div>
        <div class="meta">${esc(t("ride.recentItem", { time: fmtTime(r.requested_time) }))}</div>
      `;
      card.addEventListener("click", () => repeatSearch(r));
      list.appendChild(card);
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("ride.recentLoadFailed", { error: err.message }))}</div>`;
  }
}

function repeatSearch(r) {
  setPointCoords("ride-origin", r.origin_lat, r.origin_lon, r.origin_name);
  setPointCoords("ride-destination", r.destination_lat, r.destination_lon, r.destination_name);
  document.getElementById("name-ride-origin").value = r.origin_name;
  document.getElementById("name-ride-destination").value = r.destination_name;
  // Keep the time field as-is: the original requested time is likely in the past now.
  findRide();
}

document.getElementById("ride-submit").addEventListener("click", findRide);

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
    passenger_name: fullName(currentUser),
    origin,
    destination,
    requested_time: requestedTime,
    pickup_radius_km: radiusKm("ride-origin"),
    dropoff_radius_km: radiusKm("ride-destination"),
    time_tolerance_min: Number(document.getElementById("ride-tolerance").value),
  };
}

async function findRide() {
  const payload = rideFormPayload("ride-status");
  if (!payload) return;
  const { origin, destination } = payload;

  const btn = document.getElementById("ride-submit");
  btn.disabled = true;
  btn.textContent = t("ride.searching");
  hideStatus("ride-status");
  hideStatus("ride-ask-status");
  document.getElementById("ride-results").hidden = false;
  const searching = `<div class="empty-state">${esc(t("ride.searching"))}</div>`;
  document.getElementById("ride-matches").innerHTML = searching;
  document.getElementById("ride-matches-hint").hidden = true;
  document.getElementById("ride-transit").innerHTML = searching;
  resultsLayer.clearLayers();
  lastResults = null;

  try {
    const data = await fetchJson("/api/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    lastResults = { data, origin, destination, selection: payload };
    renderResults();
    loadRecentSearches();
  } catch (err) {
    showStatus("ride-status", err.message, false);
    document.getElementById("ride-matches").innerHTML = "";
    document.getElementById("ride-transit").innerHTML = "";
    document.getElementById("ride-results").hidden = true;
  } finally {
    btn.disabled = false;
    btn.textContent = t("ride.submit");
  }
}

// The last search's results, kept to re-render them in another language.
let lastResults = null;

function renderResults() {
  const { data, origin, destination, selection } = lastResults;
  renderMatches(data.driver_matches, data.outside_matches || [], origin, destination, selection);
  renderTransitOption(data.transit_option);
}

// matches: within the passenger's selection; outside: a few just outside
// it, listed after them and marked with what they're outside of.
function renderMatches(matches, outside, passengerOrigin, passengerDestination, selection) {
  const container = document.getElementById("ride-matches");
  container.classList.remove("has-selection");
  const all = [...matches, ...outside];
  document.getElementById("ride-matches-hint").hidden = !all.length;
  if (!all.length) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.noMatches"))}</div>`;
    return;
  }
  container.innerHTML = matches.length ? "" : `<div class="empty-state">${esc(t("ride.noMatchesInside"))}</div>`;

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

  // Clicking a match highlights its route, with where you'd get in and out,
  // and fades the others; clicking it again shows them all as before.
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
  }

  all.forEach((m, idx) => {
    const isOutside = idx >= matches.length;
    if (idx === matches.length) {
      container.insertAdjacentHTML(
        "beforeend",
        `<div class="outside-title">${esc(t("ride.outsideTitle"))}</div><div class="hint">${esc(t("ride.outsideHint"))}</div>`
      );
    }
    const card = document.createElement("div");
    card.className = `match-card clickable${isOutside ? " outside" : ""}`;
    const color = routeColors[idx % routeColors.length];
    card.style.borderLeftColor = color;
    card.innerHTML = `
      <div class="route">${esc(m.origin_name)} → ${esc(m.destination_name)}${demoBadge(m)}</div>
      ${isOutside ? outsideNote(m, selection) : ""}
      <div class="meta">${esc(t("common.driver", { name: m.driver_name }))} · ${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))}</div>
      <div class="meta">${esc(t("match.pickup", { time: fmtClock(m.pickup.time), km: fmtNumber(m.pickup.gap_km) }))}</div>
      <div class="meta">${esc(t("match.dropoff", { time: fmtClock(m.dropoff.time), km: fmtNumber(m.dropoff.gap_km) }))}</div>
      ${priceBadge(m.price_chf)}
      ${timeDiffBadge(m.time_diff_min)}
      <span class="badge">${esc(t("match.rideKm", { km: fmtNumber(m.ride_km) }))}</span>
      ${renderLastMile(m.last_mile_transit)}
    `;
    container.appendChild(card);

    const entry = { match: m, card, color, line: null };
    shown.push(entry);
    card.addEventListener("click", (e) => {
      if (e.target.closest("a")) return; // ticket and operator links
      select(entry);
    });

    if (m.route_geometry && m.route_geometry.length) {
      const latlngs = m.route_geometry.map(([lon, lat]) => [lat, lon]);
      entry.line = L.polyline(latlngs, { color, weight: 4, opacity: 0.7, dashArray: isOutside ? "6 8" : null })
        .addTo(resultsLayer)
        .bindTooltip(`${esc(m.driver_name)}: ${esc(m.origin_name)} → ${esc(m.destination_name)}`, { sticky: true })
        .on("click", () => {
          if (selected !== entry) select(entry);
          card.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
    }
  });

  fitToResults();
}

// What a ride outside the passenger's selection is outside of.
function outsideNote(m, selection) {
  const reasons = m.outside.map((limit) => {
    if (limit === "time") {
      const option = document.querySelector(`#ride-tolerance option[value="${selection.time_tolerance_min}"]`);
      return t("match.outsideTime", { tolerance: option ? option.textContent : `± ${fmtMinutes(selection.time_tolerance_min)}` });
    }
    const km = limit === "pickup" ? selection.pickup_radius_km : selection.dropoff_radius_km;
    return t(limit === "pickup" ? "match.outsidePickup" : "match.outsideDropoff", { km: fmtNumber(km) });
  });
  return `<div class="outside-note">${esc(t("match.outside"))} ${esc(reasons.join(" · "))}</div>`;
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

function renderLastMile(lastMile) {
  if (!lastMile) return "";
  return `<div class="last-mile"><strong>${esc(t("match.lastMile"))}</strong> ${esc(lastMile.from_station)} → ${esc(
    lastMile.to_station
  )}${renderConnections(lastMile.connections)}</div>`;
}

function renderTransitOption(transit) {
  const container = document.getElementById("ride-transit");
  if (!transit) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.noTransit"))}</div>`;
    return;
  }
  container.innerHTML = `<div class="transit-box alt"><strong>${esc(transit.from_station)} → ${esc(
    transit.to_station
  )}</strong>${renderConnections(transit.connections)}</div>`;
}

// One row per connection, flight-search style: operator logos, times,
// duration/transfers, the lines taken, and a link to book it on SBB (one
// ticket covers every Swiss operator on the journey).
function renderConnections(connections) {
  return connections
    .map((c) => {
      const ops = c.operators || [];
      const logos = ops.map(operatorLogo).join("");
      const legs = (c.legs || [])
        .map((l) => `<span class="pt-leg">${esc([l.category, l.number].filter(Boolean).join(" ") || l.operator?.code || "?")}</span>`)
        .join("");
      const opNames = ops
        .map((o) =>
          o.url
            ? `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.name)}</a>`
            : esc(o.name)
        )
        .join(" · ");
      const transfers = esc(t("transit.transfers", { count: c.transfers }));
      return `
        <div class="pt-conn">
          <div class="pt-logos">${logos}</div>
          <div class="pt-body">
            <div class="pt-times">${fmtClock(c.departure)} → ${fmtClock(c.arrival)}</div>
            <div class="pt-meta">${fmtDuration(c.duration)} · ${transfers}</div>
            <div class="pt-legs">${legs}</div>
            ${opNames ? `<div class="pt-ops">${opNames}</div>` : ""}
          </div>
          ${
            c.booking_url
              ? `<a class="pt-book" href="${esc(sbbUrl(c.booking_url))}" target="_blank" rel="noopener">${t("transit.tickets")}</a>`
              : ""
          }
        </div>`;
    })
    .join("");
}

function operatorLogo(o) {
  // Fall back to the operator's code as a text badge if the logo can't load.
  const inner = o.logo_url
    ? `<img src="${esc(o.logo_url)}" alt="${esc(o.code)}" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><span class="pt-code" hidden>${esc(o.code)}</span>`
    : `<span class="pt-code">${esc(o.code)}</span>`;
  return o.url
    ? `<a class="pt-logo" href="${esc(o.url)}" target="_blank" rel="noopener" title="${esc(o.name)}">${inner}</a>`
    : `<span class="pt-logo" title="${esc(o.name)}">${inner}</span>`;
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
  const payload = rideFormPayload("ride-ask-status");
  if (!payload) return;
  const btn = document.getElementById("ride-ask");
  btn.disabled = true;
  try {
    const data = await fetchJson("/api/requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const message = data.drivers_told
      ? t("ride.askSent", { count: data.drivers_told })
      : t("ride.askSentNone");
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
  try {
    const requests = await fetchJson(`/api/requests?passenger_name=${encodeURIComponent(fullName(currentUser))}`);
    if (!requests.length) {
      list.innerHTML = `<div class="empty-state">${esc(t("ride.requestsEmpty"))}</div>`;
      return;
    }
    list.innerHTML = "";
    requests.forEach((r) => {
      const card = document.createElement("div");
      card.className = "drive-card clickable";
      card.innerHTML = `
        <div class="route">${esc(r.origin_name)} → ${esc(r.destination_name)}</div>
        <div class="meta">${fmtTime(r.requested_time)}</div>
        <button type="button" class="link-btn">${esc(t("ride.cancelRequest"))}</button>
      `;
      card.addEventListener("click", () => searchForRequest(r));
      card.querySelector(".link-btn").addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await fetchJson(`/api/requests/${r.id}?passenger_name=${encodeURIComponent(fullName(currentUser))}`, {
            method: "DELETE",
          });
          loadMyRequests();
        } catch (err) {
          showStatus("ride-ask-status", err.message, false);
        }
      });
      list.appendChild(card);
    });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("ride.requestsLoadFailed", { error: err.message }))}</div>`;
  }
}

// Fill the passenger form with a request (its trip, time, circles and
// flexibility) and search.
function searchForRequest(r) {
  goTo("ride");
  document.getElementById("ride-pickup-radius").value = r.pickup_radius_km;
  document.getElementById("ride-dropoff-radius").value = r.dropoff_radius_km;
  document.querySelectorAll(".radius-slider").forEach((slider) => slider.dispatchEvent(new Event("input")));
  const tolerance = document.getElementById("ride-tolerance");
  if ([...tolerance.options].some((o) => Number(o.value) === r.time_tolerance_min)) tolerance.value = r.time_tolerance_min;
  setDateTime("ride", new Date(r.requested_time));
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
  if (!currentUser) return showUnread(0);
  try {
    const data = await fetchJson(`/api/notifications?name=${encodeURIComponent(fullName(currentUser))}`);
    showUnread(data.unread);
  } catch {
    // keep the last count
  }
}

setInterval(refreshUnread, UNREAD_REFRESH_MS);

function userQuery() {
  return `name=${encodeURIComponent(fullName(currentUser))}`;
}

async function loadInbox() {
  const list = document.getElementById("inbox-list");
  try {
    const data = await fetchJson(`/api/notifications?${userQuery()}`);
    renderInbox(data);
    if (data.unread) {
      await fetchJson(`/api/notifications/read?${userQuery()}`, { method: "POST" });
      showUnread(0);
    }
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("inbox.loadFailed", { error: err.message }))}</div>`;
  }
}

function renderInbox(data) {
  document.getElementById("inbox-notify-requests").checked = data.notify_requests;
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

function notificationCard(n) {
  const card = document.createElement("div");
  card.className = `drive-card notification${n.read_at ? "" : " unread"}`;
  const r = n.request;
  const trip = r ? `<div class="meta">${esc(r.origin_name)} → ${esc(r.destination_name)} · ${fmtTime(r.requested_time)}</div>` : "";
  const open = r && r.status === "open" && new Date(r.requested_time) > new Date();

  if (n.kind === "request_for_driver") {
    const usual = n.details?.trip;
    const title = n.passengers > 1
      ? t("inbox.requestTitleMany", { count: n.passengers })
      : t("inbox.requestTitle", { name: r?.passenger_name || "?" });
    card.innerHTML = `
      <div class="route">${esc(title)}</div>
      ${trip}
      ${usual ? `<div class="meta">${esc(t("inbox.requestWhy", { from: usual.origin.name, to: usual.destination.name, time: usual.time, count: usual.times }))}</div>` : ""}
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
    await fetchJson(`/api/notifications/${n.id}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: fullName(currentUser), action }),
    });
    if (reload) loadInbox();
  } catch (err) {
    alert(err.message);
  }
}

// "Offer this drive": the Driver form, filled with the driver's usual trip
// and a departure that reaches the passenger at their time. The driver
// still checks it and clicks "Offer drive".
async function offerDrive(n) {
  await actOn(n, "offer", false);
  const { trip } = n.details;
  goTo("drive");
  [["drive-origin", trip.origin], ["drive-destination", trip.destination]].forEach(([role, place]) => {
    setPointCoords(role, place.lat, place.lon, place.name);
    document.getElementById(`name-${role}`).value = place.name;
  });
  setDateTime("drive", new Date(n.details.suggested_departure));
  showStatus("drive-status", t("inbox.offerFilled", { name: n.request?.passenger_name || "" }), true);
  document.getElementById("drive-submit").scrollIntoView({ block: "center", behavior: "smooth" });
}

function fmtDate(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(I18N.locale(), { day: "numeric", month: "long" });
}

document.getElementById("inbox-notify-requests").addEventListener("change", (e) => updateNotificationSettings({ notify_requests: e.target.checked }));
document.getElementById("inbox-resume").addEventListener("click", () => updateNotificationSettings({ resume: true }));

async function updateNotificationSettings(changes) {
  try {
    const data = await fetchJson("/api/notification_settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: fullName(currentUser), ...changes }),
    });
    renderInbox(data);
  } catch (err) {
    alert(err.message);
  }
}

// ---------------------------------------------------------------------
// Browse rides (public)
// ---------------------------------------------------------------------
document.getElementById("browse-refresh").addEventListener("click", loadBrowse);

// The soonest rides only: with the demo network there are hundreds a week.
const BROWSE_LIMIT = 100;

async function loadBrowse() {
  const list = document.getElementById("browse-list");
  list.innerHTML = `<div class="empty-state">${esc(t("common.loading"))}</div>`;
  resultsLayer.clearLayers();
  try {
    const drives = await fetchJson(`/api/drives?upcoming_only=true&limit=${BROWSE_LIMIT}`);
    if (!drives.length) {
      list.innerHTML = `<div class="empty-state">${esc(t("browse.empty"))}</div>`;
      return;
    }
    list.innerHTML =
      drives.length === BROWSE_LIMIT
        ? `<div class="hint browse-limited">${esc(t("browse.limited", { count: BROWSE_LIMIT }))}</div>`
        : "";
    drives.forEach((d) => {
      const card = document.createElement("div");
      card.className = "drive-card clickable";
      card.innerHTML = `${driveCardHtml(d)}<div class="meta">${esc(t("common.driver", { name: d.driver_name }))}</div>`;
      card.addEventListener("click", () => showDriveOnMap(d));
      list.appendChild(card);

      L.circleMarker([d.origin_lat, d.origin_lon], { radius: 5, color: "#1a7f37" })
        .addTo(resultsLayer)
        .bindPopup(`${esc(d.driver_name)}: ${esc(d.origin_name)}`);
      L.circleMarker([d.destination_lat, d.destination_lon], { radius: 5, color: "#d8232a" })
        .addTo(resultsLayer)
        .bindPopup(`${esc(d.driver_name)}: ${esc(d.destination_name)}`);
    });
    fitToResults();
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("browse.loadFailed", { error: err.message }))}</div>`;
  }
}

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

// Default to "in 2 hours" so the demo has a sensible starting point.
["drive", "ride"].forEach((prefix) => {
  setupDateTime(prefix);
  setDateTime(prefix, new Date(Date.now() + 2 * 60 * 60 * 1000));
});

function translateUnsetReadouts() {
  Object.keys(PICK_COLORS).forEach((role) => {
    if (!points[role]) document.getElementById(`readout-${role}`).textContent = t("form.notSet");
  });
}

// Another language picked: redo everything built in JavaScript.
window.addEventListener("i18n:change", () => {
  renderAccount();
  ["signin-status", "drive-status", "ride-status"].forEach(hideStatus);
  translateUnsetReadouts();
  route();
  if (lastResults && location.hash === "#/ride") renderResults();
});

I18N.ready.then(() => {
  translateUnsetReadouts();
  renderAccount();
  route();
});
