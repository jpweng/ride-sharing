# The passenger page

"Find a ride" (`#/ride`) is one card with two fields, From and To, in a centred column. There is
no search button, no map on the page and nothing to read first. The code is
[frontend/passenger.js](../frontend/passenger.js) and
[frontend/styles/passenger.css](../frontend/styles/passenger.css), on top of the search and the
map in [frontend/app.js](../frontend/app.js).

## The two fields

- Typing finds addresses and places, as before (`setupAutocomplete` in `app.js`).
- A field that is still empty offers the places of the passenger's recent searches (the same
  `GET /api/ride_requests` that used to fill the "recent searches" list), for From their starts
  first, for To their destinations.
- The last entry of every list is "Pick on map": it opens the map, the next click on it sets the
  place, and the map closes again.
- The round button swaps the two.

Scripts add entries to a field's list through `FIELD_SUGGESTIONS` in `app.js` (role -> a function
of what is typed, returning places or things to do).

## Searching

The search starts by itself as soon as both ends are set, and again when one of them or the time
changes (`searchSoon` in `passenger.js`). It is for now, rounded up to five minutes, unless the
passenger chose a time that is still ahead; the time shows with the results, where it can be
changed. Next to it, "Leave at" can be turned into "Arrive by" (see
[Search limits](search-limits.md)); without a time chosen, that searches for arriving in an hour.
A search opened from a ride request keeps the request's time and what it stands for. Only the
latest search's answer is shown.

Asking drivers for a ride ("Ask drivers for this ride") needs a time at least 15 minutes ahead
(`notify.REQUEST_MIN_AHEAD`): for a search made for now, the API's `request_too_soon` message says so.

## Results

A card says when and where the passenger gets in and out, and the price. A click opens its
details: the driver and the departure, the way to the pickup and from the drop-off (see
[Search limits](search-limits.md)), public transport legs, the driver's profile and "Show on map".
Demo rides don't open: a click says they are fictional, as before.

The passenger's bookings and ride requests are listed below the search only when there are any.

## Without an account

The page is public: a visitor searches and sees the rides, their prices and the drivers' ratings.
The drivers' names and profiles come with signing in (see [Sign-in and accounts](sign-in.md)); on a
card, "Sign in to request a seat" replaces "Request a seat", and it and "Ask drivers for this ride"
lead to the sign-in page. The recent places, bookings and ride requests are a signed-in user's.

## The map

In passenger mode (every page: the search, the inbox, the account) the map is
not on the page. It keeps its size while hidden (`visibility: hidden`, not `display: none`), so
routes are drawn and framed on it as before, and it opens over the page when something is shown
on it: "Show on map" on a match, a click on a booking or a ride request, "Pick on
map" in a field. `revealMap()` in `app.js` does that (`openMap`); the button, a click beside the
map and Escape close it (`closeMap`), and so does a change of page. On phones it takes the whole
screen. Its tiles are only loaded while it can be seen.

In driver mode nothing changes: the map is beside the form, above it on phones.
