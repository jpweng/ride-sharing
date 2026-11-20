# API

Every endpoint, with its parameters and request and response shapes, is listed by FastAPI itself:
<http://localhost:8000/docs> on a running app (the OpenAPI description is at `/openapi.json`). The
routes are in [backend/routes/](../backend/routes/), one module per feature area. This page adds
what the generated description doesn't say: who may call each endpoint, the error codes, and what
some fields mean.

Who is asking comes only from the session cookie; no endpoint takes a user's name. Marked below:
**public**; **signed in** (a session, profile complete; a demo-driver view counts); **may change**
(signed in, not a demo-driver view). Without a session: 401 `not_signed_in`; profile not complete:
403 `profile_incomplete`; demo-driver view: 403 `demo_read_only`. POST/PUT/DELETE requests from
another site: 403 `cross_site_request`.

A feature describes its own endpoints in its `docs/<topic>.md`, with these access levels and error
codes: sign-in and account in [Sign-in and accounts](sign-in.md#endpoints), the service worker and
the Android app's asset links in [Installable app](pwa.md#endpoints), the dictionaries in
[Translations](translations.md#endpoint). The rides endpoints are below.

Rides:

- `GET  /api/health`
- `GET  /api/price_band?origin_lat=…&origin_lon=…&destination_lat=…&destination_lon=…` — public; the
  band for the driver's price on that trip: `min_chf`, `max_chf`, `default_chf`, `warn_above_chf`,
  `train_fare`, and `ceilings_chf` (each passenger's ceiling with 1…8 passengers)
- `POST /api/drives` — may change; posts a drive as the signed-in driver, with `price_chf`: their
  price per passenger, for one passenger and the whole drive (optional: the default suggestion).
  Outside the band: 400 with code `price_too_high` / `price_too_low`
- `GET  /api/drives?upcoming_only=true[&limit=…]` — public; the upcoming drives. Without a signed-in
  user, `driver_name` and `driver_id` are `null`, here and in the two below.
  `?mine=true` — signed in; the driver's own
- `GET  /api/drives/{id}` — public; one drive with full route geometry
- `DELETE /api/drives/{id}` — may change; cancels one's own drive (403 `not_your_drive`), telling
  passengers who were told about it
- `GET  /api/ride_requests` — signed in; one's recent searches (deduplicated)
- `POST /api/match` `{origin, destination, requested_time[, time_kind]}` — public; finds rides; a
  signed-in user's search is kept. `time_kind` says what the time is: `depart` (the default, when to
  leave) or `arrive` (when to be there: the drop-off is not after it). The passenger chooses no distance and no flexibility: a search covers
  drives passing within 10 km of both ends, with a pickup within 4 hours of the time
  (`backend/search.py`); when none of those can be booked, it also takes the bookable drives
  passing within 30 km (`wider: true`). Returns the driver matches as one list, the closest in
  time and place first (`driver_matches`; `driver_photo` is set when the signed-in passenger may
  see the driver's photo, see [Profiles](profiles.md)), public transport to the pickup and from the drop-off
  for the best ones where it makes sense (`first_mile_transit`, `last_mile_transit`; see
  [Search limits](search-limits.md)), and a pure public-transport alternative
- `POST /api/requests` — may change; asks for a ride nobody has posted (same body as `/api/match`),
  at least 15 minutes and at most 14 days ahead (400 `request_too_soon`, `request_too_far`). To
  arrive by a time, the request keeps `leave_by`, when the passenger's own trip has to start for
  that (400 `arrival_too_soon` when that is less than 15 minutes ahead);
  returns the request and how many drivers were told (`drivers_told`: real drivers only, for a real
  passenger), and `demo_request`; a demo request (under a demo driver's name) also gets
  `demo_drivers_told`, the demo drivers' names
- `GET  /api/requests` — signed in; one's open ride requests. `DELETE /api/requests/{id}` — may
  change; cancels one's own (403 `not_your_request`)
- `GET  /api/notifications` — signed in; one's inbox and settings (a demo-driver view leaves out
  real passengers' requests). `POST /api/notifications/read` — signed in.
  `POST /api/notifications/{id}/action` (`offer` / `decline` / `mute` / `view`) and
  `PUT /api/notification_settings` — may change

Every drive and match carries `is_demo` and `bookable` (`false` for demo drives, which are
fictional), and `seats_available` (its free seats) and `seats_booked` (see [Booking](booking.md)). Among matches that fit equally well, real drives come first.

Drives (`/api/drives`) and matches (`/api/match`) carry their prices: `price_chf` is what the next
passenger pays (for a match, for the part they ride), `price_by_passengers` what each pays with 1, 2,
… passengers up to the seats, `driver_price_chf` the driver's price, and `train_fare`
(`full_chf`, `half_chf`, `approximate`, `source`: `t601` or `ojp`) the train for the same distance.
The match's `transit_option` has a `fare` of the same shape, between its two stations.

Full request/response shapes of the ride endpoints are also documented in the
[implementation brief](../RIDESHARE_IMPLEMENTATION_BRIEF.md) (it predates sign-in: ignore the names
in its request bodies).
