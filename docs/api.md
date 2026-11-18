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

Sign-in and account:

- `POST /api/auth/link` `{email, language}` — public; emails a sign-in link. Always `{"ok": true}`
  (429 `too_many_requests` over the per-IP limit, 503 `email_unavailable` if sending fails)
- `POST /api/auth/verify` `{token}` — public; uses the link's token, sets the session cookie,
  returns `{user}` (400 `invalid_link`, 403 `account_banned`)
- `GET  /api/auth/me` — public; `{user}` or `{user: null}`: name, email, `is_demo`,
  `profile_complete`, `needs_conduct`
- `POST /api/auth/profile` `{first_name, last_name, adult, accept_conduct}` — a session, profile
  complete or not; the name only counts the first time
- `POST /api/auth/sign_out` — ends the session, clears the cookie
- `GET  /api/auth/demo_drivers`, `POST /api/auth/demo` `{driver_name}` — public; the demo drivers
  offered, and a read-only session as one of them
- `DELETE /api/account` — a session (not a demo-driver view); deletes the account and its data

Rides:

- `GET  /api/health`
- `GET  /api/price_band?origin_lat=…&origin_lon=…&destination_lat=…&destination_lon=…` — public; the
  band for the driver's price on that trip: `min_chf`, `max_chf`, `default_chf`, `warn_above_chf`,
  `train_fare`, and `ceilings_chf` (each passenger's ceiling with 1…8 passengers)
- `POST /api/drives` — may change; posts a drive as the signed-in driver, with `price_chf`: their
  price per passenger, for one passenger and the whole drive (optional: the default suggestion).
  Outside the band: 400 with code `price_too_high` / `price_too_low`
- `GET  /api/drives?upcoming_only=true[&limit=…]` — public; browse drives.
  `?mine=true` — signed in; the driver's own
- `GET  /api/drives/{id}` — public; one drive with full route geometry
- `DELETE /api/drives/{id}` — may change; cancels one's own drive (403 `not_your_drive`), telling
  passengers who were told about it
- `GET  /api/ride_requests` — signed in; one's recent searches (deduplicated)
- `POST /api/match` — public; finds rides (optional pickup/drop-off radii and time tolerance); a
  signed-in user's search is kept. Returns ranked driver matches, a last-mile transit suggestion for
  the best ones, up to 5 rides just outside the selection (`outside_matches`, each saying what it's
  outside of), and a pure public-transport alternative
- `POST /api/requests` — may change; asks for a ride nobody has posted (same body as `/api/match`);
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
fictional). Among matches that fit equally well, real drives come first.

Drives (`/api/drives`) and matches (`/api/match`) carry their prices: `price_chf` is what the next
passenger pays (for a match, for the part they ride), `price_by_passengers` what each pays with 1, 2,
… passengers up to the seats, `driver_price_chf` the driver's price, and `train_fare`
(`full_chf`, `half_chf`, `approximate`, `source`: `t601` or `ojp`) the train for the same distance.
The match's `transit_option` has a `fare` of the same shape, between its two stations.

Installable app:

- `GET  /sw.js` — public; the service worker, versioned by a hash of the frontend files, with the
  list of the app's files, `no-cache`
- `GET  /.well-known/assetlinks.json` — public; Digital Asset Links for the Android app

Translations:

- `GET  /i18n/<lang>.json` — public; a language's dictionary, merged from
  `frontend/i18n/<lang>/*.json` (see [Translations](translations.md)), `no-cache`

Full request/response shapes of the ride endpoints are also documented in the
[implementation brief](../RIDESHARE_IMPLEMENTATION_BRIEF.md) (it predates sign-in: ignore the names
in its request bodies).
