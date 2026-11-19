# Ride and Rail

A demo of a ride-sharing app for Switzerland that combines car rides with public transport, live at
<https://rideandrail.ch>. Drivers post drives; passengers either browse them or ask for a match based
on route corridor matching (drivers whose route passes near the passenger's start and destination,
around their time; the passenger gives only those three, and the closest come first), with a
public-transport comparison, and public transport to the pickup and from the drop-off where they are
too far to walk, via [transport.opendata.ch](https://transport.opendata.ch).

See [RIDESHARE_IMPLEMENTATION_BRIEF.md](RIDESHARE_IMPLEMENTATION_BRIEF.md) for the full design rationale.

**Ride requests:** a passenger can ask for a ride nobody has posted. A few drivers who made that
trip before, and a few who saved an [area](docs/driver-areas.md) it starts or ends in, are told in
their inbox, with strict limits so drivers don't get too many notifications, and the passenger is
told when a drive that fits is posted.

**Prices:** drivers only share their costs, as Swiss law requires of carpooling. The driver sets a
price per passenger inside a band: at least about the fuel, at most the car's real cost
(CHF 0.75/km) shared with one passenger, and never more than the train's 2nd class full fare. Each
passenger pays the lowest of the driver's price, the car's cost shared by everyone in the car, and
the train fare, so the price per passenger falls when more people join. Searches show the train
fare next to each ride and on the public-transport option. Nothing is charged in the demo. See
[backend/pricing.py](backend/pricing.py) for the rules and [backend/fares.py](backend/fares.py) for
the train fares: the official national distance tariff (T601), or the real fare from
opentransportdata.swiss when `OJP_API_KEY` is set (see [Train fares](docs/train-fares.md)).

**Booking:** a passenger asks for seats on a drive, from a search result or the list of drives,
with a short message; the driver accepts or declines, and either can cancel afterwards. Accepting
takes the seats from the drive, and each step lands in the other one's inbox. A driver can also
accept a passenger's ride request directly: the drive is posted and the passenger booked in one
step, and the first driver to accept gets it. See [Booking](docs/booking.md).

**Two modes:** the app is either in passenger mode (find a ride, browse rides) or in driver mode
(your drives), each with its own tabs; a button switches between them, the inbox is in both, and
the browser remembers the mode.

**Planned next: ride predictions.** The app spots people's recurring trips and offers to publish
their next drives, or request their next rides, after they confirm. Designed, not built yet.

See [docs/ride-predictions.md](docs/ride-predictions.md) for the design of both, the rules and the
reasons behind them.

## Stack

- Backend: Python + FastAPI, plain `sqlite3` (no ORM)
- Frontend: plain HTML/JS + Leaflet (OpenStreetMap tiles)
- Routing: our own [OSRM](https://project-osrm.org) server with the Switzerland
  [OpenStreetMap](https://www.openstreetmap.org/copyright) extract, refreshed monthly (see
  [Routing](docs/routing.md)); falls back to straight lines (haversine) if unreachable
- Public transport: [transport.opendata.ch](https://transport.opendata.ch) (all Swiss operators)
- Address search: [geo.admin.ch Search API](https://api3.geo.admin.ch) (Swiss addresses/places)
- Sign-in: a one-time link sent by email (Amazon SES), server-side sessions in an HttpOnly cookie; see [Sign-in and accounts](docs/sign-in.md). Optionally also Google and Apple, see [Sign-in with Google or Apple](docs/social-login.md)
- Languages: German, French, Italian and English, see [Translations](docs/translations.md)

## Run it

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) (it fetches the right Python
version automatically if needed).

```bash
uv sync
uv run uvicorn main:app --reload --app-dir backend --port 8000
# open http://localhost:8000
```

Road routes come from the OSRM server at `OSRM_URL` (default `http://osrm:5000`, the server's own,
see [Routing](docs/routing.md)). Without one, drives get straight-line routes. For real routes locally,
run the routing image (`docker run -p 5000:5000 <routing image>`, then
`OSRM_URL=http://localhost:5000`), or for a few drives by hand use the public demo server,
`OSRM_URL=https://router.project-osrm.org` (not for production use, by its usage policy). The
first demo-drive fill against it takes ~26 minutes (1,500 routes, one per second).

Run the tests:

```bash
uv run pytest
```

Add a dependency with `uv add <pkg>` (or `uv add --dev <pkg>` for dev-only tools); this updates
`pyproject.toml` and `uv.lock`, both of which are committed.

## Documentation

- [Demo drives and data retention](docs/demo-drives-and-data-retention.md): the rolling week of
  fictional drives, switching them off, how long users' data is kept
- [Sign-in and accounts](docs/sign-in.md): email links, sessions, who may do what, limits, bans,
  account deletion, email sending
- [Driver areas](docs/driver-areas.md): the places a driver usually drives from or to, each with a
  radius they choose
- [Sign-in with Google or Apple](docs/social-login.md): the optional buttons next to the email
  link, how accounts are matched, how to set the providers up
- [Train fares](docs/train-fares.md): the T601 distance tariff and the optional OJP fare lookup
- [Translations](docs/translations.md): the four languages, the dictionaries and their checks
- [Installable app (PWA) and Android app](docs/pwa.md): manifest, service worker, updates, the
  Trusted Web Activity
- [Deploying to AWS](docs/deploying-to-aws.md): the server, Terraform, CI deploys, domain, emails,
  operations
- [Routing](docs/routing.md): our own OSRM server and its monthly data
- [Notifications by email](docs/email-notifications.md): what is emailed and when, quiet hours,
  turning them off
- [Ratings and reports](docs/ratings-and-reports.md): stars after a ride, what is shown,
  reporting a problem, how the team handles reports
- [Profiles](docs/profiles.md): the optional photo, description, habits and car; who may see a
  profile; photos on S3
- [Search limits](docs/search-limits.md): how far a search looks, and when public transport to
  the pickup and from the drop-off is proposed
- [Booking](docs/booking.md): asking for a seat, the driver's answer, cancelling, seats and prices
- [Load test](docs/load-test.md): the event-evening simulation and its results
- [API](docs/api.md): who may call what, error codes, prices in responses; every endpoint is also
  listed by FastAPI at `/docs` on a running app
- [Ride predictions and ride requests](docs/ride-predictions.md): design, rules and reasons

## Project layout

```
backend/
  main.py         FastAPI app: startup, CSRF check, includes every routes/ module, mounts frontend/
  api.py          what the routes share: errors, sessions (who is asking), common request bodies
  routes/         the API's routes, one module per feature area, each with its own `router`
  translations.py the UI's dictionaries, merged from frontend/i18n/<lang>/<prefix>.json
  auth.py         sign-in by email link, sessions, profile (18+, code of conduct), demo-driver view
  social.py       sign-in with Google or Apple (OpenID Connect), off until their keys are set
  mailer.py       email: console backend (development, tests) or Amazon SES
  admin.py        command line: ban / unban an account, list and close reports
  database.py     SQLite access layer; init_db() applies migrations/ at startup
  migrations/     schema changes, one idempotent file each (YYYYMMDDHHMM_name.py), in name order
  matching.py     route corridor matching with search radii (OSRM, haversine fallback)
  search.py       upcoming drives that fit a trip, within fixed limits (searches, ride requests)
  transit_legs.py public transport to a match's pickup and from its drop-off, when it makes sense
  notify.py       ride requests: which drivers are told, limits, the inbox
  notify_email.py notifications also sent by email: quiet hours, switches, the unsubscribe link
  areas.py        driver areas: the places a driver usually drives from or to, with a radius
  pricing.py      ride prices: the driver's price band, what each passenger pays
  ratings.py      ratings after a ride: who rates whom, the averages shown
  reports.py      reports of a problem with a ride, for the team (see admin.py)
  bookings.py     bookings: asking for seats on a drive, the driver's answer, cancelling
  profiles.py     optional profiles (about, habits, car, photo) and who may see whose
  photos.py       where profile photos are kept: a private S3 bucket (a folder in development)
  fares.py        train fares: T601 distance tariff, optional OJP fare lookup
  transit.py      transport.opendata.ch integration
  operators.py    Swiss public transport operators and their logos, for each transit leg
  maintenance.py  hourly upkeep: demo drives refresh, 30-day data retention
  demo_drives.py  rolling week of fictional demo drives
frontend/
  index.html      pages: #/ride and #/browse (public) in passenger mode, #/drive in driver mode, #/inbox,
                  #/account; sign-in, #/signin/<token> (opening a link) and first-sign-in profile
  privacy.html    privacy notice, linked from the demo banner
  conduct.html    code of conduct (draft for legal review), accepted at first sign-in
  offline.html    shown by the service worker for an uncached page while offline
  app.js          Leaflet map, point picking, address autocomplete, API calls
  areas.js        the Driver page's "Your areas" section (see docs/driver-areas.md)
  i18n.js         language choice and translation of the page (see docs/translations.md)
  bookings.js     bookings: asking for a seat, one's bookings, a drive's passengers (docs/booking.md)
  profiles.js     the account page's profile form and the "See profile" popup (docs/profiles.md)
  i18n/           en/de/fr/it dictionaries, one file per key prefix
  style.css       teal/white theme, logo and text wordmark: base, layout, shared components
  styles/         one stylesheet per feature, linked right after style.css by the pages using it
  social.js       the sign-in page's Google and Apple buttons (see docs/social-login.md)
  manifest.webmanifest, sw.js, pwa.js   installable app (see docs/pwa.md)
  icons/          the logo and the app icons + make_icons.py that generates them
mobile/android/   Android app (Trusted Web Activity): Bubblewrap config + publishing guide
infra/            Terraform for AWS (VPC, EC2 server, EBS data volume + snapshots, ECR, CI deploy user,
                  DNS, SES for sign-in emails)
  bootstrap/      one-time S3 bucket for Terraform state
deploy/           what runs on the server: compose.yaml, deploy.sh, caddy/ (proxy image)
routing/          OSRM image with Switzerland's car routes: build.sh, measure.sh, Dockerfile
loadtest/         event-evening load test (Locust) against a local instance, upstream stand-ins
docs/             documentation by topic (listed above) and design documents
Dockerfile        app image (uvicorn, non-root, DB on /data)
.github/workflows/deploy.yml   test → build arm64 images → push → deploy via SSM on push to main
.github/workflows/routing-data.yml   monthly: build + measure the routing image → ECR → deploy
```
