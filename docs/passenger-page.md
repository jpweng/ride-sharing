# The passenger page

"Find a ride" (`#/ride`) is one card with two fields, From and To, straight on the page, above a
centred column of results. On a wide screen (980 px and more) the card is one bar, wider than the
column: From, To and when, in a row. There is no search button, no map on the page and nothing to
read first. The code is
[frontend/passenger.js](../frontend/passenger.js) and
[frontend/styles/passenger.css](../frontend/styles/passenger.css), on top of the search and the
map in [frontend/app.js](../frontend/app.js).

## The two fields

- Typing finds addresses and places, as before (`setupAutocomplete` in `app.js`).
  Words that find nothing (a shop neither search knows) get a line saying so, and under it the
  town they name, if they name one. See [address auto complete.md](../address%20auto%20complete.md).
- A field that is still empty offers the places of the passenger's recent searches (the same
  `GET /api/ride_requests` that used to fill the "recent searches" list), for From their starts
  first, for To their destinations.
- The last entry of every list is "Pick on map": it opens the map. A click there proposes what is
  at that spot in a small card (a restaurant, a shop, a stop, a park, a company..., otherwise the
  address, with the other places right next to it); "Use this place" takes it and closes the map.
  Zoomed out too far to tell places apart, a click proposes the town. The places come from
  Photon's reverse search on OpenStreetMap data (`placesAt` in `app.js`), and the driver's form
  and the driver's areas pick the same way.
- The round button swaps the two.
- A field always shows the place the search uses: left with other words than that place's name
  (emptied, half typed, the map closed without picking), it shows the name again.

Scripts add entries to a field's list through `FIELD_SUGGESTIONS` in `app.js` (role -> a function
of what is typed, returning places or things to do).

## Searching

The search starts by itself as soon as both ends are set, and again when one of them or the time
changes (`searchSoon` in `passenger.js`). It is for now, rounded up to five minutes, unless the
passenger chose a time that is still ahead; the time shows with the results (on a wide screen it
is in the bar from the start), where it can be changed. Next to it, "Leave at" can be turned into "Arrive by" (see
[Search limits](search-limits.md)); without a time chosen, that searches for arriving in an hour.
A search opened from a ride request keeps the request's time and what it stands for. Only the
latest search's answer is shown.

Asking drivers for a ride ("Ask drivers for this ride") needs a time at least 15 minutes ahead
(`notify.REQUEST_MIN_AHEAD`): for a search made for now, the API's `request_too_soon` message says so.

## Results

A card starts with who drives: their photo if they have one (the match's `driver_photo`, see
[Profiles](profiles.md)), else the first letter of their name, then their name and rating, and
their drive below it. Then when and where the passenger gets in and out, and the price. A click
opens its details: the departure, the way to the pickup and from the drop-off (see
[Search limits](search-limits.md)), public transport legs, the driver's profile and "Show on map".
Demo rides open their details and show on the map like any ride; they can't be booked, and an
opened one says so where the button would be.

A card looks like something to choose: an arrow at its top right says it opens (it turns when the
card is open), pointing at the card outlines it, and "Request a seat" ("Sign in to request a seat"
without an account) is a filled button.

Under the rides, asking the drivers is a tinted panel of its own: the question "Nothing that suits
you?", what asking does, and the filled button "Ask drivers for this ride". When no ride is found,
the page says so with a small picture ([icons/no-rides.svg](../frontend/icons/no-rides.svg)), and
the same text and button follow straight on the page, without the question.

Public transport for the same trip is one line under that ("3 connections from 12:27 · 3 h 55
min"), opened on a click: the stations, the ticket's price, and a row per connection, as plain
text (times, transfers, the lines and who runs them) with a link to its tickets. The page shows no
operator's logo.

The passenger's bookings and ride requests are listed below the search only when there are any.

### In steps

Finding the drives waits for nothing; public transport and the rides combined with it wait for
the timetable service, a second or more. So the page asks for a search in three parts and shows
each as it arrives:

1. `POST /api/match` with `"drives_only": true`: the drives, shown at once. Where the other two
   parts will be, the page says it is still searching: "Searching…" for public transport, "Looking
   for rides combined with public transport…" under the drives. Without a drive, it says
   "Searching…" and not yet that nobody is driving that way.
2. `POST /api/match/transit`, sent with the first: `{"transit_option": ...}`, the line of public
   transport for the same trip.
3. `POST /api/match/combined`, sent once the drives are there, with their ids as `exclude_ids`:
   `{"combined_matches": [...]}` ([A ride and public transport](combined-trips.md)). When they
   arrive, the drives' cards stay as they are (one may be open); only without a drive does the
   page now say what it found, or that nobody is driving that way.

The two endpoints ([backend/routes/match_parts.py](../backend/routes/match_parts.py)) take the body
of `/api/match` and give what it gives for their part; like it they are public (who drives is for
signed-in users) and answer 400 `invalid_time` for a time that isn't one. A part that fails shows
as nothing found. Asked for at the same time, they ask the timetable service once for what they
share: its cache answers a question that is already being asked with the same answer. `/api/match`
without `drives_only` still answers with everything at once.

## Without an account

The page is public: a visitor searches and sees the rides, their prices and the drivers' ratings.
The drivers' names, photos and profiles come with signing in (see [Sign-in and accounts](sign-in.md)); on a
card, "Sign in to request a seat" replaces "Request a seat", and it and "Ask drivers for this ride"
lead to the sign-in page. The recent places, bookings and ride requests are a signed-in user's.

## The last trip is remembered

When a search starts, its start, destination, "Leave at" or "Arrive by" and the time (when the
passenger chose one) are kept in the browser's local storage (`rideandrail.trip`), on the device
only, for 24 hours. The search page that shows with empty fields takes the trip back and searches
again: after a reload, on the next visit that day, and in the tab the emailed sign-in link opens,
where the rides then show with what a signed-in user sees. Signing out, deleting the account or a
session that has ended clear the page and forget the trip (a shared computer doesn't show the last
person's search). A time that has passed isn't restored: the search is for now. The
[privacy page](../frontend/privacy.html) says what is kept. `rememberTrip`, `restoreTrip` and
`clearTrip` in `passenger.js`.

## The map

In passenger mode (every page: the search, the inbox, the account) the map is
not on the page. It keeps its size while hidden (`visibility: hidden`, not `display: none`), so
routes are drawn and framed on it as before, and it opens over the page when something is shown
on it: "Show on map" on a match, a click on a booking or a ride request, "Pick on
map" in a field. `revealMap()` in `app.js` does that (`openMap`); the button, a click beside the
map and Escape close it (`closeMap`), and so does a change of page. On phones it takes the whole
screen. Its tiles are only loaded while it can be seen.

In driver mode nothing changes: the map is beside the form, above it on phones. Leaflet draws its
tiles and controls from the nearest positioned element, and only positions the map itself when it
is created unpositioned, which it isn't (the page starts in passenger mode); so `#map` is
`position: relative` in `style.css`, for the map to hold its tiles in driver mode too.
