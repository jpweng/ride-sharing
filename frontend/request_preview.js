// A ride request in the driver's inbox, on the map: click its card to see
// the passenger's trip and, when the driver was told because they usually
// make a trip that fits, their usual route with the pickup and drop-off
// points on it. The route comes from the server (it has the road
// geometry): GET /api/notifications/{id}/preview, see
// docs/request-preview.md. Hooks into app.js through REQUEST_EXTRAS;
// drawing the passenger's trip is areas.js's showAreaRequestOnMap.

function previewPoint(point, label, role) {
  L.circleMarker([point.lat, point.lon], {
    radius: 7, color: PICK_COLORS[role], fillColor: "#fff", fillOpacity: 1, weight: 3,
  })
    .addTo(resultsLayer)
    .bindTooltip(esc(label), { permanent: true, direction: "top" });
}

async function showNotificationOnMap(n, card) {
  try {
    const preview = await fetchJson(`/api/notifications/${n.id}/preview`);
    showAreaRequestOnMap(preview.request);
    const usual = preview.usual_trip;
    if (!usual) return;
    const line = L.polyline(usual.route, { color: AREA_COLOR, weight: 4, opacity: 0.8 }).addTo(resultsLayer);
    previewPoint(usual.pickup, t("map.pickupPoint", { time: fmtClock(usual.pickup.time) }), "ride-origin");
    previewPoint(usual.dropoff, t("map.dropoffPoint", { time: fmtClock(usual.dropoff.time) }), "ride-destination");
    const request = preview.request;
    map.fitBounds(
      line.getBounds().extend([request.origin_lat, request.origin_lon]).extend([request.destination_lat, request.destination_lon]),
      { padding: [40, 40] },
    );
    revealMap();
  } catch (err) {
    let hint = card.querySelector(".preview-error");
    if (!hint) {
      hint = document.createElement("div");
      hint.className = "hint preview-error";
      card.appendChild(hint);
    }
    hint.textContent = err.message;
  }
}

REQUEST_EXTRAS.push((request, actions, { notification }) => {
  // Only the inbox's cards (the areas list has its own click handler).
  if (!notification) return;
  const card = actions.closest(".drive-card");
  if (!card) return;
  card.classList.add("clickable");
  const hint = document.createElement("div");
  hint.className = "hint preview-hint";
  hint.textContent = t("preview.hint");
  card.insertBefore(hint, actions);
  // Clicking the card shows the request on the map; clicking its buttons,
  // or what they open (bookings.js's panel), doesn't.
  card.addEventListener("click", (e) => {
    if (!e.target.closest(".notification-actions, .booking-box")) showNotificationOnMap(notification, card);
  });
});
