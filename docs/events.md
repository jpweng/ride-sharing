# Events

Christmas markets and other events (festivals, matches...) are ride destinations with a page of
their own and a short link to share (`https://rideandrail.ch/e/<slug>`, also as a QR code): rides
there, rides back from closing time until late, and drivers going there are offered to post their
drive back at once.

Events are data in [backend/events.json](../backend/events.json), loaded and checked by
[backend/events.py](../backend/events.py). There is no admin UI: adding an event is a pull request.

- **Event page**: `#/event/<slug>`, public. The event's name, dates and opening hours, a day to
  choose, then *Find a ride there* (the Passenger form with the event as the destination, the
  visitor enters their start), times to *ride back* from closing time until 1.5 hours after it and
  when the bars close (the Passenger form with the event as the start), *Offer a drive there*, a
  *Copy link* / *Share* button and a QR code (also downloadable as SVG for printing).
- **Shareable link**: `/e/<slug>` (e.g. <https://rideandrail.ch/e/montreux-noel-2026>), a tiny page
  with the event's name and dates for link previews (in messaging apps) that redirects to
  `/#/event/<slug>`. A `?lang=de|fr|it|en` on it is passed on to the app, for printed codes.
- **Drive back**: after a driver posts a drive ending within 1 km of an event on one of its days,
  the Driver page offers to post the drive back at once, at one of the suggested times.
- **Discoverability**: current and upcoming events are listed as one row of links on the Passenger
  and Browse pages (on phones, a row that scrolls sideways).
- **Demo drives**: on each day an event is open, drivers from the 8 nearest demo places 10 to 70 km
  away go there in the afternoon or evening and back between closing time and 1.5 hours after it
  (two of them on Fridays and Saturdays); see `EVENT_DRIVES` in
  [backend/demo_drives.py](../backend/demo_drives.py). New events get them automatically. Like the
  other [demo drives](demo-drives-and-data-retention.md), they're labelled "Demo" and go away with
  `DEMO_DRIVES=off`.

In the browser, all of it is [frontend/events.js](../frontend/events.js), a script of its own
loaded after `app.js`: it adds the `event` page to `app.js`'s pages, builds what it shows itself
(nothing of it is in `index.html`), and hears about a posted drive through `app.js`'s
`drive:posted` event. The QR codes are drawn by a small library served from this site
([frontend/vendor/](../frontend/vendor/README.md)), so no other service is involved.

## Adding an event

Add an object to `events` in [backend/events.json](../backend/events.json):

```json
{
  "slug": "montreux-noel-2026",
  "name": {"de": "Montreux Noël", "fr": "Montreux Noël", "it": "Montreux Noël", "en": "Montreux Noël"},
  "place": {"name": "Montreux Noël (Place du Marché)", "lat": 46.43176, "lon": 6.90950, "station": "Montreux"},
  "first_day": "2026-11-20",
  "last_day": "2026-12-24",
  "hours": {
    "mon": {"open": "11:00", "close": "21:00", "late_close": "23:00"},
    "...": "one entry for each of mon tue wed thu fri sat sun, null when closed that weekday"
  },
  "exceptions": {"2026-12-24": {"open": "11:00", "close": "17:00"}, "2026-12-25": null},
  "url": "https://www.montreuxnoel.com/",
  "verified": true,
  "notes": "Where the hours come from, and when they were checked.",
  "todo": "Required when something isn't verified: what to check."
}
```

- `slug`: lowercase letters, digits and hyphens; it's the link, so keep it short and include the
  year (`basel-christmas-market-2026`). Never change it once shared.
- `name`: in all four languages. `place`: the drop-off point (the market's main square or entrance,
  where a car can stop near it), as `lat`/`lon`, and the nearest station (`station`). Its `name` is
  what drives there are called.
- `hours`: Swiss local time. `close` is when the stalls close; the optional `late_close` when the
  last of it (restaurants, bars) closes. A closing time at or before the opening time is after
  midnight. `exceptions` override given dates (other hours, or `null` for closed); an exception can
  carry `"verified": false`.
- Take dates and hours from the organiser's official website. If you can't confirm them, set
  `"verified": false` (or `"verified": false` on the exception) and say in `todo` what to check: the
  event page then tells visitors to check the organiser's website.

`uv run pytest` validates the file (as the app does at startup: it won't start with an invalid
one).

## Endpoints

Access levels and error codes as in [API](api.md).

- `GET  /api/events?lang=de|fr|it|en` — public; current and upcoming events, the soonest first, with
  the name in that language (`name`, and all of them in `names`), place, first and last day,
  `status` (`current` / `upcoming`)
- `GET  /api/events/{slug}?lang=…` — public; one event, also with its `hours`, `exceptions` and
  `schedule`: one entry per open day with `open`, `close`, `late_close` and the suggested departures
  back (`return_times`); 404 `event_not_found` for an unknown slug
- `GET  /e/{slug}` — public; the shareable link: an HTML page with a link preview that redirects to
  `/#/event/{slug}` (404 for an unknown slug, still redirecting)
