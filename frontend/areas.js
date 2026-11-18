"use strict";

// ---------------------------------------------------------------------
// Driver areas (see backend/areas.py): on the Driver page, the places the
// driver usually drives from or to, each with a radius they choose.
// Loaded after app.js, whose map, point picking and helpers it uses.
// ---------------------------------------------------------------------
const AREA_ROLE = "drive-area"; // "drive-": its pin shows on the Driver page
const AREA_COLOR = "#0f6e5f";

// The place being added is picked like a drive's start: typed or clicked
// on the map, with a circle sized by the radius chosen next to it.
PICK_COLORS[AREA_ROLE] = AREA_COLOR;
RADIUS_SLIDERS[AREA_ROLE] = "areas-radius";
setupAutocomplete(AREA_ROLE);

// GET /api/areas: the driver's areas, how many they can have, the radii
// to choose from.
let areasData = null;

function radiusOptionsHtml(selectedKm) {
  return areasData.radius_options_km
    .map((km) => `<option value="${km}"${km === selectedKm ? " selected" : ""}>${km} km</option>`)
    .join("");
}

async function loadAreas() {
  try {
    areasData = await fetchJson("/api/areas");
  } catch (err) {
    document.getElementById("areas-list").innerHTML =
      `<div class="empty-state">${esc(t("areas.loadFailed", { error: err.message }))}</div>`;
    return;
  }
  renderAreas();
  loadAreaRequests();
}

function renderAreas() {
  const { areas, max_areas: max, default_radius_km: defaultKm } = areasData;
  const radius = document.getElementById("areas-radius");
  if (!radius.options.length) radius.innerHTML = radiusOptionsHtml(defaultKm);

  // With the most areas one can have, the form makes way for a note.
  const full = areas.length >= max;
  document.getElementById("areas-form").hidden = full;
  const fullNote = document.getElementById("areas-full");
  fullNote.hidden = !full;
  fullNote.textContent = t("areas.full", { max });

  const list = document.getElementById("areas-list");
  if (!areas.length) {
    list.innerHTML = `<div class="empty-state">${esc(t("areas.empty"))}</div>`;
    return;
  }
  list.innerHTML = "";
  areas.forEach((area) => list.appendChild(areaCard(area)));
}

function areaCard(area) {
  const card = document.createElement("div");
  card.className = "drive-card clickable area-card";
  card.innerHTML = `
    <div class="route">${esc(area.name)}</div>
    <div class="area-radius">
      <span class="meta">${esc(t("areas.radius"))}</span>
      <select class="auto-width" aria-label="${esc(t("areas.radius"))}">${radiusOptionsHtml(area.radius_km)}</select>
    </div>
    <button type="button" class="link-btn">${esc(t("areas.remove"))}</button>
  `;
  card.addEventListener("click", (e) => {
    if (!e.target.closest("select, button")) showAreaOnMap(area);
  });
  card.querySelector("select").addEventListener("change", async (e) => {
    try {
      Object.assign(area, await postJson(`/api/areas/${area.id}`, { radius_km: Number(e.target.value) }, "PUT"));
      hideStatus("areas-status");
      showAreaOnMap(area);
      loadAreaRequests(); // a wider or narrower circle takes in other requests
    } catch (err) {
      showStatus("areas-status", err.message, false);
      loadAreas();
    }
  });
  card.querySelector("button").addEventListener("click", async () => {
    if (!confirm(t("areas.removeConfirm", { name: area.name }))) return;
    try {
      await fetchJson(`/api/areas/${area.id}`, { method: "DELETE" });
      hideStatus("areas-status");
      resultsLayer.clearLayers();
    } catch (err) {
      showStatus("areas-status", err.message, false);
    }
    loadAreas();
  });
  return card;
}

function showAreaOnMap(area) {
  resultsLayer.clearLayers();
  const circle = L.circle([area.lat, area.lon], {
    radius: area.radius_km * 1000, color: AREA_COLOR, weight: 2, fillOpacity: 0.08,
  }).addTo(resultsLayer);
  L.circleMarker([area.lat, area.lon], { radius: 6, color: AREA_COLOR, fillColor: AREA_COLOR, fillOpacity: 0.9 })
    .addTo(resultsLayer)
    .bindTooltip(`${esc(area.name)} · ${area.radius_km} km`, { permanent: true, direction: "top" });
  map.fitBounds(circle.getBounds(), { padding: [30, 30] });
  revealMap();
}

// ---------------------------------------------------------------------
// The open ride requests in the driver's areas (GET /api/areas/requests):
// every request that starts or ends there, the soonest first, to browse by
// area and by day, each with "Offer this drive".
// ---------------------------------------------------------------------
let areaRequests = [];

async function loadAreaRequests() {
  const list = document.getElementById("areas-requests");
  document.getElementById("areas-requests-filters").hidden = true;
  if (!areasData.areas.length) {
    list.innerHTML = `<div class="empty-state">${esc(t("areas.requestsNoAreas"))}</div>`;
    return;
  }
  try {
    areaRequests = await fetchJson("/api/areas/requests");
  } catch (err) {
    list.innerHTML = `<div class="empty-state">${esc(t("areas.requestsLoadFailed", { error: err.message }))}</div>`;
    return;
  }
  if (!areaRequests.length) {
    list.innerHTML = `<div class="empty-state">${esc(t("areas.requestsEmpty"))}</div>`;
    return;
  }
  renderAreaRequestFilters();
  renderAreaRequests();
}

// Choices: the areas and the days that have requests. Kept when the list
// reloads, if they're still there.
function renderAreaRequestFilters() {
  const options = (select, first, values) => {
    const chosen = select.value;
    select.innerHTML = [`<option value="">${esc(first)}</option>`]
      .concat(values.map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`))
      .join("");
    if (values.some(([value]) => String(value) === chosen)) select.value = chosen;
  };
  // A request can touch several of the driver's areas: it's found under each.
  const withRequests = areasData.areas.filter((area) => areaRequests.some((r) => r.area_ids.includes(area.id)));
  const days = [...new Set(areaRequests.map((r) => r.requested_time.slice(0, 10)))];
  options(
    document.getElementById("areas-requests-area"), t("areas.requestsAllAreas"),
    withRequests.map((area) => [area.id, area.name]),
  );
  options(
    document.getElementById("areas-requests-day"), t("areas.requestsAnyDay"),
    days.map((day) => [day, new Date(`${day}T12:00`).toLocaleDateString(I18N.locale(), { weekday: "short", day: "numeric", month: "short" })]),
  );
  // Nothing to choose between with one request.
  document.getElementById("areas-requests-filters").hidden = areaRequests.length < 2;
}

function renderAreaRequests() {
  const areaId = document.getElementById("areas-requests-area").value;
  const day = document.getElementById("areas-requests-day").value;
  const shown = areaRequests.filter(
    (r) => (!areaId || r.area_ids.includes(Number(areaId))) && (!day || r.requested_time.startsWith(day))
  );
  const list = document.getElementById("areas-requests");
  if (!shown.length) {
    list.innerHTML = `<div class="empty-state">${esc(t("areas.requestsNoneFiltered"))}</div>`;
    return;
  }
  list.innerHTML = "";
  shown.forEach((r) => list.appendChild(areaRequestCard(r)));
}

function areaRequestCard(r) {
  const card = document.createElement("div");
  card.className = "drive-card clickable area-request-card";
  card.innerHTML = `
    <div class="route">${esc(r.origin_name)} → ${esc(r.destination_name)}</div>
    <div class="meta">${esc(r.passenger_name)} · ${fmtTime(r.requested_time)} · ± ${esc(flexibilityText(r.time_tolerance_min))}</div>
    <div class="meta">${esc(requestInAreaText(r.area, r.ends))}</div>
  `;
  card.addEventListener("click", () => showAreaRequestOnMap(r));
  const offer = actionButton("inbox.offer", "submit-btn secondary", (e) => {
    e.stopPropagation();
    fillDriveFormForRequest(r);
    showStatus("drive-status", t("inbox.offerFilledArea", { name: r.passenger_name }), true);
  });
  card.appendChild(offer);
  return card;
}

// "2 h", "30 min", "1 h 30": how far from their time the passenger can go.
function flexibilityText(minutes) {
  return minutes && minutes % 60 === 0 ? `${minutes / 60} h` : fmtMinutes(minutes);
}

// The passenger's start and destination, joined by a dashed line (it's a
// wish, not a route).
function showAreaRequestOnMap(r) {
  resultsLayer.clearLayers();
  const from = [r.origin_lat, r.origin_lon];
  const to = [r.destination_lat, r.destination_lon];
  L.circleMarker(from, { radius: 7, color: PICK_COLORS["ride-origin"], fillOpacity: 0.9 })
    .addTo(resultsLayer)
    .bindTooltip(esc(r.origin_name), { permanent: true, direction: "top" });
  L.circleMarker(to, { radius: 7, color: PICK_COLORS["ride-destination"], fillOpacity: 0.9 })
    .addTo(resultsLayer)
    .bindTooltip(esc(r.destination_name), { permanent: true, direction: "top" });
  L.polyline([from, to], { color: AREA_COLOR, weight: 3, dashArray: "6 8" }).addTo(resultsLayer);
  map.fitBounds(L.latLngBounds([from, to]), { padding: [40, 40] });
  revealMap();
}

["areas-requests-area", "areas-requests-day"].forEach((id) => {
  document.getElementById(id).addEventListener("change", renderAreaRequests);
});

// The place being added: frame its circle when it's picked, and resize the
// circle with the radius.
function frameAreaBeingAdded() {
  if (points[AREA_ROLE]) map.fitBounds(pointBounds(AREA_ROLE), { padding: [30, 30] });
}

window.addEventListener("point:set", (e) => {
  if (e.detail.role === AREA_ROLE) frameAreaBeingAdded();
});

document.getElementById("areas-radius").addEventListener("change", () => {
  const circle = points[AREA_ROLE]?.circle;
  if (circle) circle.setRadius(radiusKm(AREA_ROLE) * 1000);
  frameAreaBeingAdded();
});

document.getElementById("areas-add").addEventListener("click", async () => {
  const place = getPoint(AREA_ROLE, `name-${AREA_ROLE}`);
  if (!place) return showStatus("areas-status", t("areas.needPlace"), false);
  const btn = document.getElementById("areas-add");
  btn.disabled = true;
  try {
    const area = await postJson("/api/areas", {
      place: { name: place.name, lat: place.lat, lon: place.lon },
      radius_km: radiusKm(AREA_ROLE),
    });
    showStatus("areas-status", t("areas.added", { name: area.name, km: area.radius_km }), true);
    clearPoint(AREA_ROLE);
    document.getElementById(`name-${AREA_ROLE}`).value = "";
    await loadAreas();
    showAreaOnMap(area);
  } catch (err) {
    showStatus("areas-status", err.message, false);
  } finally {
    btn.disabled = false;
  }
});

window.addEventListener("i18n:change", () => hideStatus("areas-status"));

// The Driver page loads the areas with the drives. app.js may have shown
// the page before this script ran (it routes as soon as it knows who is
// signed in), hence the check.
{
  const showDrivePage = PAGES.drive.onShow;
  PAGES.drive.onShow = () => {
    showDrivePage();
    loadAreas();
  };
}
if (document.getElementById("page-drive").classList.contains("active")) loadAreas();
