"use strict";

// ---------------------------------------------------------------------
// A trip that doesn't keep the time the passenger asked for says so on its
// card, right under its times (docs/search-limits.md): one that leaves
// before a time to leave at, or arrives after a time to arrive by. A search
// shows such trips on purpose (a pickup up to 4 hours before the time, a
// walk to the car that starts before the car is there), and a passenger who
// takes the card's first time for the one they gave misses it. The other
// way round (leaving later, arriving earlier) is what they asked for: the
// card says nothing.
// Loaded after app.js, whose search (lastResults) and cards (CARD_EXTRAS)
// it uses.
// ---------------------------------------------------------------------

const ASKED_TIME_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>';

// What a card says about the time asked for, or "". `asked`: the search's
// {time, kind}; `starts` and `arrives`: the two times on the card's first
// line, so the note is about what the passenger reads there. Less than
// MINUTE_STEP apart is on time, as in a card's details (app.js:
// timeDiffBadge).
function askedTimeNote(asked, starts, arrives) {
  if (!asked || !asked.time) return "";
  const arrive = asked.kind === "arrive";
  const off = Math.round(arrive ? minutesBetween(asked.time, arrives) : minutesBetween(starts, asked.time));
  if (!(off >= MINUTE_STEP)) return "";
  const words = { duration: fmtMinutes(off), time: fmtClock(asked.time) };
  return arrive ? t("asked.arrivesLater", words) : t("asked.leavesEarlier", words);
}

// On a drive for the whole trip (the car's pickup and drop-off) and on a
// ride with public transport (the passenger's own start and arrival, the
// walk to or from the car counted: combined.js).
CARD_EXTRAS.push((kind, item, card) => {
  if (!["match", "combined"].includes(kind) || !lastResults) return;
  const [starts, arrives] = kind === "combined" ? [item.starts, item.arrives] : [item.pickup.time, item.dropoff.time];
  const note = askedTimeNote(lastResults.asked, starts, arrives);
  const line = card.querySelector(".trip-line");
  if (!note || !line) return;
  line.insertAdjacentHTML("afterend", `<div class="asked-time-note">${ASKED_TIME_ICON}<span>${esc(note)}</span></div>`);
});
