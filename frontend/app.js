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

  points[role] = { lat, lon, name, kind, marker };

  const readout = document.getElementById(`readout-${role}`);
  if (readout) readout.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;

  zoomToTrip(role.split("-")[0]);
}

// Once a page has both its start and arrival set, frame the trip on the map.
function zoomToTrip(page) {
  const origin = points[`${page}-origin`];
  const destination = points[`${page}-destination`];
  if (!origin || !destination) return;
  map.fitBounds(
    [[origin.lat, origin.lon], [destination.lat, destination.lon]],
    { padding: [50, 50], maxZoom: 14 }
  );
}

function clearPoint(role) {
  if (points[role] && points[role].marker) pickerLayer.removeLayer(points[role].marker);
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
// Address autocomplete (geo.admin.ch Search API — Swiss addresses/places)
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

async function searchGeoAdmin(query) {
  try {
    const resp = await fetch(
      `${GEOADMIN_SEARCH_URL}?searchText=${encodeURIComponent(query)}&type=locations&origins=address,gg25&limit=8&sr=4326&lang=${I18N.lang()}`
    );
    if (!resp.ok) throw new Error("geocoding failed");
    const data = await resp.json();
    return (data.results || [])
      .map((r) => r.attrs || {})
      .filter((a) => typeof a.lat === "number" && typeof a.lon === "number")
      // origin "gg25" = a municipality, "address" = a street address.
      .map((a) => ({
        label: stripHtml(a.label),
        lat: a.lat,
        lon: a.lon,
        kind: a.origin === "gg25" ? "place" : "address",
      }));
  } catch {
    return []; // fail soft -- "Pick on map" is always a working fallback
  }
}

function setupAutocomplete(role) {
  const input = document.getElementById(`name-${role}`);
  const list = document.getElementById(`suggest-${role}`);
  if (!input || !list) return;

  let items = [];
  let activeIndex = -1;

  function closeList() {
    list.classList.remove("show");
    list.innerHTML = "";
    items = [];
    activeIndex = -1;
  }

  function renderList() {
    list.innerHTML = items
      .map((it, i) => `<li data-index="${i}" class="${i === activeIndex ? "active" : ""}">${it.label}</li>`)
      .join("");
    list.classList.toggle("show", items.length > 0);
  }

  function selectItem(it) {
    input.value = it.label;
    setPointCoords(role, it.lat, it.lon, it.label, it.kind);
    closeList();
  }

  const runSearch = debounce(async (query) => {
    if (query.trim().length < 3) {
      closeList();
      return;
    }
    items = await searchGeoAdmin(query);
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
// Pages: #/ (home, full width), #/browse (public), #/ride (passenger),
// #/drive (driver)
// ---------------------------------------------------------------------
const PAGES = {
  home: { needsUser: false, onShow: null },
  browse: { needsUser: false, onShow: loadBrowse },
  ride: { needsUser: true, onShow: loadRecentSearches },
  drive: { needsUser: true, onShow: loadMyDrives },
};

function route() {
  const requested = location.hash.replace(/^#\/?/, "");
  const page = PAGES[requested] ? requested : "home";
  const shown = PAGES[page].needsUser && !currentUser ? "signin" : page;

  // The home page replaces the panel + map layout; Leaflet needs to
  // re-measure the map once it's visible again.
  document.body.classList.toggle("home-active", shown === "home");
  if (shown !== "home") map.invalidateSize();

  document.querySelectorAll(".tab-link").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("active", s.id === `page-${shown}`));
  document.getElementById("picker-hint").hidden = !(shown === "ride" || shown === "drive");

  // Only show the picked origin/destination pins that belong to this page.
  activePickRole = null;
  setPicking(null);
  pickerLayer.clearLayers();
  Object.entries(points).forEach(([role, p]) => {
    if (role.startsWith(`${shown}-`) && p.marker) pickerLayer.addLayer(p.marker);
  });
  resultsLayer.clearLayers();

  if (PAGES[shown]?.onShow) PAGES[shown].onShow();
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

function driveCardHtml(d) {
  const km = d.route_distance_m ? (d.route_distance_m / 1000).toFixed(1) : "?";
  return `
    <div class="route">${esc(d.origin_name)} → ${esc(d.destination_name)}${demoBadge(d)}</div>
    <div class="meta">${fmtTime(d.departure_time)} · ${esc(t("common.seats", { count: d.seats_available }))} · ${km} km</div>
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
  const departure = document.getElementById("drive-departure").value;
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

async function findRide() {
  const origin = getPoint("ride-origin", "name-ride-origin");
  const destination = getPoint("ride-destination", "name-ride-destination");
  const requestedTime = document.getElementById("ride-time").value;

  if (!origin) return showStatus("ride-status", t("form.needOrigin"), false);
  if (!destination) return showStatus("ride-status", t("form.needDestination"), false);
  if (!requestedTime) return showStatus("ride-status", t("ride.needTime"), false);

  const btn = document.getElementById("ride-submit");
  btn.disabled = true;
  btn.textContent = t("ride.searching");
  hideStatus("ride-status");
  document.getElementById("ride-results").hidden = false;
  const searching = `<div class="empty-state">${esc(t("ride.searching"))}</div>`;
  document.getElementById("ride-matches").innerHTML = searching;
  document.getElementById("ride-transit").innerHTML = searching;
  resultsLayer.clearLayers();
  lastResults = null;

  try {
    const data = await fetchJson("/api/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        passenger_name: fullName(currentUser),
        origin,
        destination,
        requested_time: requestedTime,
      }),
    });
    lastResults = { data, origin, destination };
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
  const { data, origin, destination } = lastResults;
  renderMatches(data.driver_matches, origin, destination);
  renderTransitOption(data.transit_option);
}

function renderMatches(matches, passengerOrigin, passengerDestination) {
  const container = document.getElementById("ride-matches");
  if (!matches.length) {
    container.innerHTML = `<div class="empty-state">${esc(t("ride.noMatches"))}</div>`;
    return;
  }
  container.innerHTML = "";

  L.marker([passengerOrigin.lat, passengerOrigin.lon], { title: t("map.pickupTitle") })
    .addTo(resultsLayer)
    .bindPopup(esc(t("map.pickupPopup", { name: passengerOrigin.name })));
  L.marker([passengerDestination.lat, passengerDestination.lon], { title: t("map.destinationTitle") })
    .addTo(resultsLayer)
    .bindPopup(esc(t("map.destinationPopup", { name: passengerDestination.name })));

  const routeColors = ["#1a73e8", "#8e24aa", "#00897b", "#f9a825", "#6d4c41"];

  matches.forEach((m, idx) => {
    const card = document.createElement("div");
    card.className = "match-card";
    const warn = m.detour_km > 4 || m.detour_pct > 25;
    card.innerHTML = `
      <div class="route">${esc(m.origin_name)} → ${esc(m.destination_name)}${demoBadge(m)}</div>
      <div class="meta">${esc(t("common.driver", { name: m.driver_name }))} · ${fmtTime(m.departure_time)} · ${esc(t("common.seats", { count: m.seats_available }))}</div>
      <div class="meta">${esc(t("match.distances", { base: fmtNumber(m.base_distance_km), via: fmtNumber(m.via_distance_km) }))}</div>
      <span class="score ${warn ? "warn" : ""}">${esc(t("match.detour", { km: fmtNumber(m.detour_km), pct: fmtNumber(m.detour_pct) }))}</span>
      <span class="badge">${m.route_source}</span>
      ${
        m.recommended_dropoff
          ? `<div class="meta">${t("match.suggestedDropoff", { station: esc(m.recommended_dropoff.station_name), km: fmtNumber(m.dropoff_gap_km) })}</div>`
          : m.dropoff_gap_km > 1
          ? `<div class="meta">${esc(t("match.dropoffGap", { km: fmtNumber(m.dropoff_gap_km) }))}</div>`
          : ""
      }
      ${renderLastMile(m.last_mile_transit)}
    `;
    container.appendChild(card);

    if (m.route_geometry && m.route_geometry.length) {
      const latlngs = m.route_geometry.map(([lon, lat]) => [lat, lon]);
      L.polyline(latlngs, { color: routeColors[idx % routeColors.length], weight: 4, opacity: 0.7 })
        .addTo(resultsLayer)
        .bindPopup(`${esc(m.driver_name)}: ${esc(m.origin_name)} → ${esc(m.destination_name)}`);
    }
  });

  fitToResults();
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

// Default time inputs to "in 2 hours" so the demo has a sensible starting point.
(function setDefaultTimes() {
  const in2h = new Date(Date.now() + 2 * 60 * 60 * 1000);
  in2h.setMinutes(in2h.getMinutes() - in2h.getTimezoneOffset());
  const iso = in2h.toISOString().slice(0, 16);
  document.getElementById("drive-departure").value = iso;
  document.getElementById("ride-time").value = iso;
})();

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
