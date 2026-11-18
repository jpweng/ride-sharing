"use strict";

// ---------------------------------------------------------------------
// Events (backend/events.json, see docs/events.md): the event page
// #/event/<slug> (shared as /e/<slug>), the list of events on the Passenger
// and Browse pages, and the drive back offered to drivers who post a drive
// to an event. Loaded after app.js, whose map, pages and helpers it uses;
// what it shows is built here, not in index.html.
// ---------------------------------------------------------------------

// A drive ending this close to an event's place goes to the event.
const EVENT_NEAR_KM = 1;

// "2026-11-20T18:00", Swiss local time, like the API's times.
function localIso(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const rad = (deg) => (deg * Math.PI) / 180;
  const a = Math.sin(rad(lat2 - lat1) / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}

// Current and upcoming events, fetched once per language.
const eventLists = {};

function eventList() {
  const lang = I18N.lang();
  if (!eventLists[lang]) {
    eventLists[lang] = fetchJson(`/api/events?lang=${lang}`).catch((err) => {
      delete eventLists[lang];
      throw err;
    });
  }
  return eventLists[lang];
}

function fetchEvent(slug) {
  return fetchJson(`/api/events/${encodeURIComponent(slug)}?lang=${I18N.lang()}`);
}

// "2026-11-20" in the visitor's language.
function fmtDay(day, options) {
  return new Date(`${day}T12:00`).toLocaleDateString(I18N.locale(), options);
}

function fmtEventDates(ev) {
  const first = fmtDay(ev.first_day, { day: "numeric", month: "short" });
  const last = fmtDay(ev.last_day, { day: "numeric", month: "short", year: "numeric" });
  return `${first} – ${last}`;
}

// --- The list of events on the Passenger and Browse pages ---------------

// One line of links to the events' pages, under the page's title; nothing
// when there are none.
async function showEventStrip(page) {
  let el = document.getElementById(`${page}-events`);
  if (!el) {
    el = document.createElement("div");
    el.id = `${page}-events`;
    el.className = "event-strip";
    el.hidden = true;
    document.querySelector(`#page-${page} .page-title`).after(el);
  }
  try {
    const list = await eventList();
    el.hidden = !list.length;
    el.innerHTML = `
      <div class="event-strip-title">${esc(t("event.listTitle"))}</div>
      <div class="event-chips">${list
        .map(
          (ev) => `<a class="event-chip" href="#/event/${encodeURIComponent(ev.slug)}">
            <span class="event-chip-name">${esc(ev.name)}</span>
            <span class="event-chip-dates">${esc(fmtEventDates(ev))}</span>
          </a>`
        )
        .join("")}</div>`;
  } catch {
    el.hidden = true; // a bonus: the page works without it
  }
}

// --- The event page ----------------------------------------------------

// The page's section, next to the other pages.
function eventBody() {
  let body = document.getElementById("event-body");
  if (!body) {
    const section = document.createElement("section");
    section.id = "page-event";
    section.className = "page";
    section.innerHTML = '<div id="event-body"></div>';
    document.getElementById("page-drive").after(section);
    body = section.firstElementChild;
  }
  return body;
}

let eventLoads = 0;

async function loadEventPage(slug) {
  const body = eventBody();
  const load = ++eventLoads;
  if (!slug) {
    body.innerHTML = `<div class="empty-state">${esc(t("event.notFound"))}</div>`;
    return;
  }
  body.innerHTML = `<div class="empty-state">${esc(t("common.loading"))}</div>`;
  try {
    const ev = await fetchEvent(slug);
    if (load === eventLoads) renderEventPage(ev);
  } catch (err) {
    if (load !== eventLoads) return;
    const notFound = err.message === t("errors.event_not_found");
    body.innerHTML = `<div class="empty-state">${esc(notFound ? t("event.notFound") : t("event.loadFailed", { error: err.message }))}</div>`;
  }
}

function eventDayLabel(day) {
  const date = fmtDay(day.date, { weekday: "short", day: "numeric", month: "short" });
  return `${date} · ${fmtClock(day.open)}–${fmtClock(day.close)}`;
}

// The weekly hours, then the days with other hours.
function eventHoursTable(ev) {
  const monday = new Date(2026, 10, 23, 12); // any Monday
  const hours = (h) =>
    h ? `${h.open}–${h.close}${h.late_close ? ` (${t("event.lateClose", { time: h.late_close })})` : ""}` : t("event.closed");
  const rows = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((key, i) => {
    const day = new Date(monday.getTime() + i * 24 * 60 * 60 * 1000);
    return [day.toLocaleDateString(I18N.locale(), { weekday: "long" }), hours(ev.hours[key])];
  });
  Object.entries(ev.exceptions).forEach(([date, h]) => {
    rows.push([fmtDay(date, { weekday: "short", day: "numeric", month: "long" }), hours(h)]);
  });
  return `<table class="event-hours-table">${rows
    .map(([label, value]) => `<tr><th>${esc(label)}</th><td>${esc(value)}</td></tr>`)
    .join("")}</table>`;
}

function renderEventPage(ev) {
  const now = new Date();
  // Days still to come, today included until it closes.
  const days = ev.schedule.filter((d) => new Date(d.late_close || d.close) > now);
  const link = `${location.origin}/e/${encodeURIComponent(ev.slug)}`;
  const website = ev.url
    ? ` · <a href="${esc(ev.url)}" target="_blank" rel="noopener">${esc(t("event.website"))}</a>`
    : "";
  const body = eventBody();
  body.innerHTML = `
    <div class="page-title">${esc(ev.name)}</div>
    <div class="event-when">${esc(fmtEventDates(ev))}</div>
    <div class="hint">${esc(ev.place.name)} · ${esc(t("event.station", { station: ev.place.station }))}${website}</div>
    ${ev.verified ? "" : `<div class="hint event-unverified">${esc(t("event.unverified"))}</div>`}
    ${
      days.length
        ? `<label for="event-day">${esc(t("event.dayLabel"))}</label>
      <select id="event-day">${days.map((d, i) => `<option value="${i}">${esc(eventDayLabel(d))}</option>`).join("")}</select>
      <div class="hint" id="event-day-note"></div>

      <div class="section-title">${esc(t("event.toTitle"))}</div>
      <div class="hint">${esc(t("event.toHint"))}</div>
      <button type="button" class="submit-btn" id="event-to">${esc(t("event.toButton"))}</button>

      <div class="section-title">${esc(t("event.backTitle"))}</div>
      <div class="hint" id="event-back-hint"></div>
      <div class="time-chips" id="event-back-times"></div>

      <div class="section-title">${esc(t("event.offerTitle"))}</div>
      <div class="hint">${esc(t("event.offerHint"))}</div>
      <button type="button" class="submit-btn secondary" id="event-offer">${esc(t("event.offerButton"))}</button>`
        : `<div class="empty-state">${esc(t("event.over"))}</div>`
    }

    <div class="section-title">${esc(t("event.shareTitle"))}</div>
    <div class="share-row">
      <input type="text" id="event-link" readonly value="${esc(link)}" aria-label="${esc(t("event.shareTitle"))}" />
      <button type="button" class="submit-btn secondary" id="event-copy">${esc(t("event.copyLink"))}</button>
      ${navigator.share ? `<button type="button" class="submit-btn secondary" id="event-share">${esc(t("event.share"))}</button>` : ""}
    </div>
    <div class="status-msg" id="event-share-status"></div>
    <div class="event-qr-box" id="event-qr-box">
      <div class="event-qr" id="event-qr"></div>
      <div class="hint">${esc(t("event.qrHint"))}
        <a id="event-qr-download" download="${esc(ev.slug)}-qr.svg">${esc(t("event.downloadQr"))}</a></div>
    </div>

    <details class="event-hours">
      <summary>${esc(t("event.hoursTitle"))}</summary>
      ${eventHoursTable(ev)}
    </details>
  `;

  const place = { name: ev.name, lat: ev.place.lat, lon: ev.place.lon, kind: "address" };
  resultsLayer.clearLayers();
  L.marker([place.lat, place.lon], { title: ev.name }).addTo(resultsLayer).bindPopup(esc(ev.name));
  map.setView([place.lat, place.lon], 14);

  if (days.length) {
    const select = document.getElementById("event-day");
    const day = () => days[Number(select.value)];
    const showDay = () => {
      const d = day();
      const late = d.late_close ? t("event.lateClose", { time: fmtClock(d.late_close) }) : "";
      document.getElementById("event-day-note").textContent = late.charAt(0).toUpperCase() + late.slice(1);
      document.getElementById("event-back-hint").textContent = t("event.backHint", { time: fmtClock(d.close) });
      const chips = document.getElementById("event-back-times");
      chips.innerHTML = "";
      d.return_times.forEach((iso) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "time-chip";
        chip.textContent = fmtClock(iso);
        chip.disabled = new Date(iso) <= new Date();
        // A search around this time: the flexibility keeps it after closing.
        chip.addEventListener("click", () => prefillTrip("ride", place, null, new Date(iso), "event.enterDestination", 60));
        chips.appendChild(chip);
      });
    };
    select.addEventListener("change", showDay);
    showDay();
    document.getElementById("event-to").addEventListener("click", () =>
      prefillTrip("ride", null, place, arrivalTime(day()), "event.enterStart", 120)
    );
    document.getElementById("event-offer").addEventListener("click", () =>
      prefillTrip("drive", null, place, arrivalTime(day()), "event.enterDriveStart")
    );
  }

  document.getElementById("event-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(link);
      showStatus("event-share-status", t("event.copied"), true);
    } catch {
      document.getElementById("event-link").select();
      showStatus("event-share-status", t("event.copyFailed"), false);
    }
  });
  document.getElementById("event-share")?.addEventListener("click", () => {
    navigator.share({ title: ev.name, text: `${ev.name}, ${fmtEventDates(ev)}`, url: link }).catch(() => {});
  });
  renderQr(link);
}

// A suggested time to get there: three hours before closing, or at
// opening; never in the past.
function arrivalTime(day) {
  const opens = new Date(day.open);
  const closes = new Date(day.close);
  const at = new Date(Math.max(opens.getTime(), closes.getTime() - 3 * 60 * 60 * 1000));
  const soon = new Date(Date.now() + 10 * 60 * 1000);
  return at > soon ? at : soon;
}

// The QR code (vendor/qrcode.min.js, loaded by index.html), and an SVG of
// it to download and print. Hidden if the library didn't load.
function renderQr(text) {
  const box = document.getElementById("event-qr-box");
  if (typeof qrcode !== "function") {
    box.hidden = true;
    return;
  }
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  document.getElementById("event-qr").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  const printable = qr.createSvgTag({ cellSize: 10, margin: 4 });
  document.getElementById("event-qr-download").href = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(printable)}`;
}

// The Passenger or Driver form with one end of the trip at the event, the
// other left for the visitor to enter (a message says so), and a time.
function prefillTrip(page, from, to, when, messageKey, toleranceMin) {
  goTo(page);
  [["origin", from], ["destination", to]].forEach(([end, place]) => {
    const role = `${page}-${end}`;
    const input = document.getElementById(`name-${role}`);
    if (place) {
      setPointCoords(role, place.lat, place.lon, place.name, place.kind);
      input.value = place.name;
    } else {
      clearPoint(role);
      input.value = "";
    }
  });
  setDateTime(page, when);
  if (page === "ride") {
    const tolerance = document.getElementById("ride-tolerance");
    if (toleranceMin && [...tolerance.options].some((o) => Number(o.value) === toleranceMin)) tolerance.value = toleranceMin;
    document.getElementById("ride-results").hidden = true;
    lastResults = null;
  }
  showStatus(`${page}-status`, t(messageKey), true);
  if (currentUser) {
    const empty = document.getElementById(`name-${page}-${from ? "destination" : "origin"}`);
    empty.focus({ preventScroll: true });
    empty.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

// --- The drive back, offered after posting a drive to an event ----------

let returnOffer = null; // { drive, ev, day, times }

// The box under the Driver form's status message.
function returnBox() {
  let box = document.getElementById("drive-return");
  if (!box) {
    box = document.createElement("div");
    box.id = "drive-return";
    box.className = "return-box";
    box.hidden = true;
    box.innerHTML = `
      <div class="section-title" id="drive-return-title"></div>
      <div class="hint" id="drive-return-hint"></div>
      <label for="drive-return-time" id="drive-return-time-label"></label>
      <select id="drive-return-time" class="auto-width"></select>
      <button type="button" class="submit-btn" id="drive-return-submit"></button>
      <button type="button" class="link-btn" id="drive-return-dismiss"></button>
      <div class="status-msg" id="drive-return-status"></div>`;
    document.getElementById("drive-status").after(box);
    box.querySelector("#drive-return-dismiss").addEventListener("click", hideReturnOffer);
    box.querySelector("#drive-return-submit").addEventListener("click", postReturnDrive);
  }
  return box;
}

async function offerReturnDrive(drive) {
  hideReturnOffer();
  try {
    const near = (await eventList()).find(
      (ev) => haversineKm(ev.place.lat, ev.place.lon, drive.destination_lat, drive.destination_lon) <= EVENT_NEAR_KM
    );
    if (!near) return;
    const ev = await fetchEvent(near.slug);
    const arrival = new Date(new Date(drive.departure_time).getTime() + (drive.route_duration_s || 0) * 1000);
    const day = ev.schedule.find((d) => d.date === localIso(arrival).slice(0, 10));
    const times = (day?.return_times || []).filter((iso) => new Date(iso) > arrival);
    if (!times.length) return;
    returnOffer = { drive, ev, day, times };
    renderReturnOffer();
  } catch {
    // a bonus: the drive itself is posted
  }
}

function renderReturnOffer() {
  const box = returnBox();
  box.hidden = !returnOffer;
  if (!returnOffer) return;
  const { drive, ev, day, times } = returnOffer;
  document.getElementById("drive-return-title").textContent = t("event.returnTitle", {
    event: ev.names[I18N.lang()] || ev.name,
  });
  document.getElementById("drive-return-hint").textContent = t("event.returnHint", {
    time: fmtClock(day.close),
    to: drive.origin_name,
  });
  document.getElementById("drive-return-time-label").textContent = t("event.returnTime");
  document.getElementById("drive-return-submit").textContent = t("event.returnSubmit");
  document.getElementById("drive-return-dismiss").textContent = t("event.returnDismiss");
  const select = document.getElementById("drive-return-time");
  const chosen = select.value;
  select.innerHTML = times.map((iso) => `<option value="${iso}">${esc(fmtClock(iso))}</option>`).join("");
  if (times.includes(chosen)) select.value = chosen;
}

function hideReturnOffer() {
  returnOffer = null;
  renderReturnOffer();
  hideStatus("drive-return-status");
}

async function postReturnDrive() {
  if (!returnOffer) return;
  const { drive } = returnOffer;
  const btn = document.getElementById("drive-return-submit");
  btn.disabled = true;
  try {
    const origin = { name: drive.destination_name, lat: drive.destination_lat, lon: drive.destination_lon, kind: "address" };
    const destination = { name: drive.origin_name, lat: drive.origin_lat, lon: drive.origin_lon, kind: "address" };
    await postJson("/api/drives", {
      origin,
      destination,
      departure_time: document.getElementById("drive-return-time").value,
      seats_available: drive.seats_available,
    });
    hideReturnOffer();
    showStatus("drive-status", t("event.returnPosted", { from: origin.name, to: destination.name }), true);
    loadMyDrives();
  } catch (err) {
    showStatus("drive-return-status", err.message, false);
  } finally {
    btn.disabled = false;
  }
}

// app.js announces each drive posted from the Driver form.
window.addEventListener("drive:posted", (e) => offerReturnDrive(e.detail.drive));
window.addEventListener("i18n:change", renderReturnOffer);

// --- Hooking into app.js's pages ----------------------------------------

// #/event/<slug>: public, so a shared link works before signing in.
eventBody();
PAGES.event = { needsUser: false, onShow: (slug) => loadEventPage(slug) };
LINKED_PAGES.push("event");
["ride", "browse"].forEach((page) => {
  const show = PAGES[page].onShow;
  PAGES[page].onShow = (...args) => {
    show(...args);
    showEventStrip(page);
  };
});

// app.js may have routed before this script ran (it routes as soon as it
// knows who is signed in): an event link then showed the default page, and
// the page shown has no list of events yet.
if (document.querySelector(".page.active") && location.hash.startsWith("#/event")) route();
else ["ride", "browse"].forEach((page) => {
  if (document.getElementById(`page-${page}`).classList.contains("active")) showEventStrip(page);
});
