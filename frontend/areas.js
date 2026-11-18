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
