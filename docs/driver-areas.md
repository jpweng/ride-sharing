# Driver areas

A driver saves the places they usually drive from or to (home, work, ...), each with a radius, in
the **Your areas** section of their profile: the account page, in driver mode (see
[Profiles](profiles.md)). [backend/areas.py](../backend/areas.py) has the
rules; in short:

- **An area** is a place (typed or picked on the map, like a drive's start) and a radius.
- **Radius:** the driver chooses 5, 10, 20 or 30 km for each area (`RADIUS_OPTIONS_KM`), 10 km by
  default (`DEFAULT_RADIUS_KM`), and can change it later. Car commutes in Switzerland average about
  15 km, so 10 km covers the usual pickups around a home or a workplace without taking in a whole
  canton.
- **At most 5 areas** per driver (`MAX_AREAS`): home, work and a few more, without turning into
  "everywhere".
- **Only their owner sees them.** They stay until the driver removes them or deletes their
  account; names without an account lose theirs when they're deleted
  ([data retention](demo-drives-and-data-retention.md)).

**What they're for:** a driver is told, in their inbox, about the ride requests that start or end
in one of their areas, on top of the drivers who made the trip before, and within the same limits
(one notification a day, three a week). Who is chosen and in what order is in
[Ride predictions and ride requests](ride-predictions.md#who-is-told); the matching itself is
`areas.drivers_near()`. A request fits an area when its start or its destination is inside the
circle; a driver whose areas cover both ends comes first. Turning request notifications off in the
inbox turns these off too.

**Browsing the requests:** the Driver page lists every open ride request that starts or ends
in one of the driver's areas, the soonest first, to filter by area and by day
(`notify.requests_in_areas()`). Unlike notifications, the list has no budget: the driver comes to
it. Each request shows the passenger's first name and initial, their trip, its time, and which
area it fits; *Accept this ride* takes it in one step ([Booking](booking.md)), and *Offer this drive*
fills the Driver form with the passenger's trip and time, to change first; posting the drive tells
the passenger. The list leaves out the driver's own
requests and demo requests, and shows at most 200.

In the browser, the section is [frontend/areas.js](../frontend/areas.js), a script of its own
loaded after `app.js`, whose map, point picking and helpers it uses. `app.js` announces each point
set on the map with a `point:set` event (`detail.role`).

## Endpoints

Access levels and error codes as in [API](api.md).

- `GET  /api/areas` — signed in; `{areas, max_areas, radius_options_km, default_radius_km}`, the
  areas oldest first, each with `id`, `name`, `lat`, `lon`, `radius_km`
- `POST /api/areas` `{place: {name, lat, lon}, radius_km}` — may change; saves an area
  (`radius_km` optional: the default). 400 `too_many_areas` with 5 already; 422 for a radius that
  isn't one of the options
- `PUT  /api/areas/{id}` `{radius_km}` — may change; changes the radius (404 `area_not_found`,
  also for someone else's area)
- `DELETE /api/areas/{id}` — may change; removes it (404 `area_not_found`)
- `GET  /api/areas/requests` — signed in; the open ride requests that start or end in one of the
  user's areas, the soonest first: each as in the inbox (`passenger_name`, both ends with their
  coordinates, `requested_time`), with `area` (the one of the
  user's areas it fits best), `ends` (`origin`, `destination` or both: the ends the user's areas
  cover) and `area_ids` (every area of the user's it touches)
