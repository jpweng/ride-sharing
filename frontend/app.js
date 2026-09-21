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
  "post-origin": "#1a7f37",
  "post-destination": "#d8232a",
  "req-origin": "#1a7f37",
  "req-destination": "#d8232a",
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

function setPointCoords(role, lat, lon, name) {
  if (points[role] && points[role].marker) {
    pickerLayer.removeLayer(points[role].marker);
  }
  const marker = L.circleMarker([lat, lon], {
    radius: 8,
    color: PICK_COLORS[role] || "#333",
    fillColor: PICK_COLORS[role] || "#333",
    fillOpacity: 0.9,
  }).addTo(pickerLayer);

  points[role] = { lat, lon, name, marker };

  const readout = document.getElementById(`readout-${role}`);
  if (readout) readout.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

async function reverseGeocode(lat, lon) {
  try {
    const resp = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}&zoom=14`,
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
  return { name: typedName || p.name || `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`, lat: p.lat, lon: p.lon };
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
      `${GEOADMIN_SEARCH_URL}?searchText=${encodeURIComponent(query)}&type=locations&origins=address&limit=8&sr=4326`
    );
    if (!resp.ok) throw new Error("geocoding failed");
    const data = await resp.json();
    return (data.results || [])
      .map((r) => r.attrs || {})
      .filter((a) => typeof a.lat === "number" && typeof a.lon === "number")
      .map((a) => ({ label: stripHtml(a.label), lat: a.lat, lon: a.lon }));
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
    setPointCoords(role, it.lat, it.lon, it.label);
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

["post-origin", "post-destination", "req-origin", "req-destination"].forEach(setupAutocomplete);

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.view}`).classList.add("active");
    resultsLayer.clearLayers();
    if (btn.dataset.view === "browse") loadDrives();
  });
});

// ---------------------------------------------------------------------
// Status helper
// ---------------------------------------------------------------------
function showStatus(elId, message, ok) {
  const el = document.getElementById(elId);
  el.textContent = message;
  el.className = `status-msg show ${ok ? "ok" : "err"}`;
}

// ---------------------------------------------------------------------
// Post a Drive
// ---------------------------------------------------------------------
document.getElementById("post-submit").addEventListener("click", async () => {
  const driverName = document.getElementById("post-driver-name").value.trim();
  const origin = getPoint("post-origin", "name-post-origin");
  const destination = getPoint("post-destination", "name-post-destination");
  const departure = document.getElementById("post-departure").value;
  const seats = parseInt(document.getElementById("post-seats").value, 10) || 1;

  if (!driverName) return showStatus("post-status", "Enter your name.", false);
  if (!origin) return showStatus("post-status", "Pick an origin on the map.", false);
  if (!destination) return showStatus("post-status", "Pick a destination on the map.", false);
  if (!departure) return showStatus("post-status", "Choose a departure time.", false);

  const btn = document.getElementById("post-submit");
  btn.disabled = true;
  btn.textContent = "Posting…";
  try {
    const resp = await fetch("/api/drives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        driver_name: driverName,
        origin,
        destination,
        departure_time: departure,
        seats_available: seats,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Failed to post drive");
    const km = data.route_distance_m ? (data.route_distance_m / 1000).toFixed(1) : "?";
    showStatus(
      "post-status",
      `Posted! Drive #${data.id}: ${origin.name} → ${destination.name}, ${km} km (${data.route_source}).`,
      true
    );
  } catch (err) {
    showStatus("post-status", err.message, false);
  } finally {
    btn.disabled = false;
    btn.textContent = "Post drive";
  }
});

// ---------------------------------------------------------------------
// Request a Ride
// ---------------------------------------------------------------------
document.getElementById("req-submit").addEventListener("click", async () => {
  const passengerName = document.getElementById("req-passenger-name").value.trim();
  const origin = getPoint("req-origin", "name-req-origin");
  const destination = getPoint("req-destination", "name-req-destination");
  const requestedTime = document.getElementById("req-time").value;

  if (!passengerName) return showStatus("req-status", "Enter your name.", false);
  if (!origin) return showStatus("req-status", "Pick an origin on the map.", false);
  if (!destination) return showStatus("req-status", "Pick a destination on the map.", false);
  if (!requestedTime) return showStatus("req-status", "Choose a requested time.", false);

  const btn = document.getElementById("req-submit");
  btn.disabled = true;
  btn.textContent = "Searching…";
  document.getElementById("req-matches").innerHTML = '<div class="empty-state">Searching…</div>';
  document.getElementById("req-transit").innerHTML = '<div class="empty-state">Searching…</div>';
  resultsLayer.clearLayers();

  try {
    const resp = await fetch("/api/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        passenger_name: passengerName,
        origin,
        destination,
        requested_time: requestedTime,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Match request failed");
    showStatus("req-status", `Found ${data.driver_matches.length} match(es).`, true);
    renderMatches(data.driver_matches, origin, destination);
    renderTransitOption(data.transit_option);
  } catch (err) {
    showStatus("req-status", err.message, false);
    document.getElementById("req-matches").innerHTML = `<div class="empty-state">${err.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Find a ride";
  }
});

function renderMatches(matches, passengerOrigin, passengerDestination) {
  const container = document.getElementById("req-matches");
  if (!matches.length) {
    container.innerHTML = '<div class="empty-state">No drivers found within the detour/time window.</div>';
    return;
  }
  container.innerHTML = "";

  L.marker([passengerOrigin.lat, passengerOrigin.lon], { title: "Your pickup" })
    .addTo(resultsLayer)
    .bindPopup(`Pickup: ${passengerOrigin.name}`);
  L.marker([passengerDestination.lat, passengerDestination.lon], { title: "Your destination" })
    .addTo(resultsLayer)
    .bindPopup(`Destination: ${passengerDestination.name}`);

  const routeColors = ["#1a73e8", "#8e24aa", "#00897b", "#f9a825", "#6d4c41"];

  matches.forEach((m, idx) => {
    const card = document.createElement("div");
    card.className = "match-card";
    const warn = m.detour_km > 4 || m.detour_pct > 25;
    card.innerHTML = `
      <div class="route">${m.origin_name} → ${m.destination_name}</div>
      <div class="meta">Driver: ${m.driver_name} · ${fmtTime(m.departure_time)} · ${m.seats_available} seat(s)</div>
      <div class="meta">Base route: ${m.base_distance_km} km · via you: ${m.via_distance_km} km</div>
      <span class="score ${warn ? "warn" : ""}">detour +${m.detour_km} km (${m.detour_pct}%)</span>
      <span class="badge">${m.route_source}</span>
      ${m.dropoff_gap_km > 1 ? `<div class="meta">Drop-off is ${m.dropoff_gap_km} km from your destination.</div>` : ""}
      ${renderLastMile(m.last_mile_transit)}
    `;
    container.appendChild(card);

    if (m.route_geometry && m.route_geometry.length) {
      const latlngs = m.route_geometry.map(([lon, lat]) => [lat, lon]);
      L.polyline(latlngs, { color: routeColors[idx % routeColors.length], weight: 4, opacity: 0.7 })
        .addTo(resultsLayer)
        .bindPopup(`${m.driver_name}: ${m.origin_name} → ${m.destination_name}`);
    }
  });

  const group = L.featureGroup(resultsLayer.getLayers());
  if (group.getBounds().isValid()) map.fitBounds(group.getBounds(), { padding: [30, 30] });
}

function renderLastMile(lastMile) {
  if (!lastMile) return "";
  const conns = lastMile.connections
    .map(
      (c) =>
        `<div class="conn">${fmtTime(c.departure)} → ${fmtTime(c.arrival)} · ${c.transfers} transfer(s) · ${c.products.join(
          ", "
        )}</div>`
    )
    .join("");
  return `<div class="last-mile"><strong>Last mile:</strong> ${lastMile.from_station} → ${lastMile.to_station}${conns}</div>`;
}

function renderTransitOption(transit) {
  const container = document.getElementById("req-transit");
  if (!transit) {
    container.innerHTML = '<div class="empty-state">No direct public transport option found.</div>';
    return;
  }
  const conns = transit.connections
    .map(
      (c) =>
        `<div class="conn">${fmtTime(c.departure)} → ${fmtTime(c.arrival)} · ${c.transfers} transfer(s) · ${c.products.join(
          ", "
        )}</div>`
    )
    .join("");
  container.innerHTML = `<div class="transit-box alt"><strong>${transit.from_station} → ${transit.to_station}</strong>${conns}</div>`;
}

function fmtTime(iso) {
  if (!iso) return "?";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------
// Browse Drives
// ---------------------------------------------------------------------
document.getElementById("browse-refresh").addEventListener("click", loadDrives);

async function loadDrives() {
  const list = document.getElementById("browse-list");
  list.innerHTML = '<div class="empty-state">Loading…</div>';
  resultsLayer.clearLayers();
  try {
    const resp = await fetch("/api/drives?upcoming_only=true");
    const drives = await resp.json();
    if (!drives.length) {
      list.innerHTML = '<div class="empty-state">No upcoming drives posted yet.</div>';
      return;
    }
    list.innerHTML = "";
    drives.forEach((d) => {
      const card = document.createElement("div");
      card.className = "drive-card";
      const km = d.route_distance_m ? (d.route_distance_m / 1000).toFixed(1) : "?";
      card.innerHTML = `
        <div class="route">${d.origin_name} → ${d.destination_name}</div>
        <div class="meta">Driver: ${d.driver_name} · ${fmtTime(d.departure_time)} · ${d.seats_available} seat(s)</div>
        <div class="meta">${km} km <span class="badge">${d.route_source || "?"}</span></div>
      `;
      card.addEventListener("click", () => showDriveOnMap(d));
      list.appendChild(card);

      if (d.origin_lat && d.destination_lat) {
        L.circleMarker([d.origin_lat, d.origin_lon], { radius: 5, color: "#1a7f37" })
          .addTo(resultsLayer)
          .bindPopup(`${d.driver_name}: ${d.origin_name}`);
        L.circleMarker([d.destination_lat, d.destination_lon], { radius: 5, color: "#d8232a" })
          .addTo(resultsLayer)
          .bindPopup(`${d.driver_name}: ${d.destination_name}`);
      }
    });
    const group = L.featureGroup(resultsLayer.getLayers());
    if (group.getBounds().isValid()) map.fitBounds(group.getBounds(), { padding: [30, 30] });
  } catch (err) {
    list.innerHTML = `<div class="empty-state">Failed to load drives: ${err.message}</div>`;
  }
}

function showDriveOnMap(drive) {
  resultsLayer.clearLayers();
  if (drive.route_geometry && drive.route_geometry.length) {
    const latlngs = drive.route_geometry.map(([lon, lat]) => [lat, lon]);
    const line = L.polyline(latlngs, { color: "#1a73e8", weight: 4 }).addTo(resultsLayer);
    map.fitBounds(line.getBounds(), { padding: [30, 30] });
  } else {
    map.setView([drive.origin_lat, drive.origin_lon], 10);
  }
  L.marker([drive.origin_lat, drive.origin_lon]).addTo(resultsLayer).bindPopup(drive.origin_name).openPopup();
  L.marker([drive.destination_lat, drive.destination_lon]).addTo(resultsLayer).bindPopup(drive.destination_name);
}

// Default time inputs to "in 2 hours" so the demo has a sensible starting point.
(function setDefaultTimes() {
  const in2h = new Date(Date.now() + 2 * 60 * 60 * 1000);
  in2h.setMinutes(in2h.getMinutes() - in2h.getTimezoneOffset());
  const iso = in2h.toISOString().slice(0, 16);
  document.getElementById("post-departure").value = iso;
  document.getElementById("req-time").value = iso;
})();
