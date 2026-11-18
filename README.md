# Ride and Rail

A demo of a ride-sharing app for Switzerland that combines car rides with public transport, live at
<https://rideandrail.ch>. Drivers post drives; passengers either browse them or ask for a match based
on route corridor matching (drivers whose route passes within the passenger's chosen pickup and
drop-off radii, around their time), with a public-transport comparison and last-mile-transit
suggestion via [transport.opendata.ch](https://transport.opendata.ch).

See [RIDESHARE_IMPLEMENTATION_BRIEF.md](RIDESHARE_IMPLEMENTATION_BRIEF.md) for the full design rationale.

**Ride requests:** a passenger can ask for a ride nobody has posted. A few drivers who made that
trip before are told in their inbox, with strict limits so drivers don't get too many
notifications, and the passenger is told when a drive that fits is posted.

**Events:** Christmas markets and other events are ride destinations with a page of their own and
a short link to share (`https://rideandrail.ch/e/<slug>`, also as a QR code): rides there, rides
back from closing time until late, and drivers going there are offered to post their drive back at
once. See [Events](#events).

**Planned next: ride predictions.** The app spots people's recurring trips and offers to publish
their next drives, or request their next rides, after they confirm. Designed, not built yet.

See [docs/ride-predictions.md](docs/ride-predictions.md) for the design of both, the rules and the
reasons behind them.

## Stack

- Backend: Python + FastAPI, plain `sqlite3` (no ORM)
- Frontend: plain HTML/JS + Leaflet (OpenStreetMap tiles)
- Routing: [OSRM demo server](https://router.project-osrm.org) (falls back to haversine if unreachable)
- Public transport: [transport.opendata.ch](https://transport.opendata.ch) (all Swiss operators)
- Address search: [geo.admin.ch Search API](https://api3.geo.admin.ch) (Swiss addresses/places)
- Sign-in: a one-time link sent by email (Amazon SES), server-side sessions in an HttpOnly cookie; see [Sign-in and accounts](#sign-in-and-accounts)
- Languages: German, French, Italian and English, see [Translations](#translations)

## Run it

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) (it fetches the right Python
version automatically if needed).

```bash
uv sync
uv run uvicorn main:app --reload --app-dir backend --port 8000
# open http://localhost:8000
```

Run the tests:

```bash
uv run pytest
```

Add a dependency with `uv add <pkg>` (or `uv add --dev <pkg>` for dev-only tools); this updates
`pyproject.toml` and `uv.lock`, both of which are committed.

### Demo drives and data retention

At startup and then every hour, the app runs [backend/maintenance.py](backend/maintenance.py):

- **Demo drives**: [backend/demo_drives.py](backend/demo_drives.py) keeps a rolling week of fictional
  drives (about 33,000 by some 4,000 drivers) so "Browse rides" and the Passenger page always have
  something to show. They form a network over the whole country, from the cities down to small
  towns, denser in Ticino and Graubünden: weekday commutes, midday
  drives on the busy corridors and weekend trips, with departures spread over the day so a search
  at most times finds something. "View as a demo driver" on the sign-in page (e.g. Jean Dupont)
  shows their Driver page, read-only. Demo drives that have departed or no longer match the list are
  deleted, and missing ones for the next 7 days are added. They're flagged `is_demo` in the
  database, and so are demo drivers in `users`. Each trip's road
  route is fetched from OSRM once (one call per second, its usage limit) and stored once in
  `demo_routes`, shared by all the trip's drives.
- **Users' data is kept for 30 days**: drives are deleted 30 days after their departure, searches
  30 days after they were made, ride requests 30 days after their time, notifications 30 days after
  they were sent. Nothing in the UI shows any of this by then. Accounts stay until their owner
  deletes them; names without an account are deleted once nothing references them. Sign-in links
  are deleted after a day, expired sessions at once. On the server, deleted data also remains in
  the daily disk snapshots for 7 more days.

### Sign-in and accounts

[backend/auth.py](backend/auth.py) has the details; in short:

- **Sign-in link**: the user enters their email address; the server emails a link with a random
  token, stored only as its SHA-256, valid once for 15 minutes. The link opens `#/signin/<token>`;
  the page then POSTs the token (so it never appears in server logs, and mail scanners that open
  links can't use it up), and the server starts a session. The answer to "send me a link" is the same
  whether the address has an account or not.
- **First sign-in**: first and last name, "I am 18 or older" and the
  [code of conduct](frontend/conduct.html) (a draft for a lawyer to review), whose acceptance is
  stored with its date and version (`CONDUCT_VERSION`; changing it asks everyone to accept the new
  one). Other users see the first name and last initial ("Marie M.", "Marie M. 2" if taken); it
  can't be changed later, as drives and requests store it.
- **Sessions**: a random token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie named
  `__Host-session`, stored as its hash in `sessions`; 90 days from the last visit (extended at most
  once a day), until sign-out, a ban or the account's deletion. `COOKIE_SECURE=0` drops `Secure`, only
  for testing over plain HTTP on another host than `localhost`.
- **Cross-site request forgery**: browsers don't send a `SameSite=Lax` cookie with cross-site
  POST/PUT/DELETE requests, and on top of that the app refuses those requests to `/api/` unless their
  `Origin` (or, without it, `Sec-Fetch-Site`) says they come from the site itself. Requests with
  neither header don't come from a browser, so they carry no victim's cookie. No CSRF tokens are
  needed this way, and the frontend stays plain `fetch` calls.
- **Who may do what**: browsing drives and searching are public (a signed-in user's searches are
  kept for their "recent searches"). Posting, cancelling and reading one's own drives, searches,
  requests and inbox need a signed-in user with a complete profile, and use only the session's
  user: the API takes no names.
- **View as a demo driver**: a read-only session (1 day) as one of the fictional drivers, without
  email, for investors and testers. It can read that driver's pages but not post, cancel, answer
  notifications or change settings, and its inbox leaves out real passengers' requests. Demo drivers
  have no email address, so they never receive email.
- **Limits**: per IP, 10 links per 15 minutes and 50 a day (then 429); per address, 3 per
  15 minutes and 10 a day (then no email is sent, with the same answer). Caddy adds its own per-IP
  limit (see below).
- **Account deletion** (the account page, reached by clicking one's name): deletes the account and
  everything about it at once. Passengers who were told about the user's upcoming drives get a
  "drive cancelled" notification; drivers' notifications about the user's requests disappear.
- **Bans**: `users.banned_at`, set with `docker compose exec app python backend/admin.py ban
  <email>` (or `unban`). It ends the account's sessions; no link is sent and earlier links are
  refused.
- **Users from before sign-in** (a typed name, no email) can't sign in. Their drives, searches and
  requests expire under the 30-day retention above, then their names are deleted. Nothing is
  migrated to accounts.

Email ([backend/mailer.py](backend/mailer.py)) goes through a backend chosen by `EMAIL_BACKEND`:
`console` (the default) logs the email, link included, instead of sending it, so in development
you copy the link from the server's output; `ses` sends through Amazon SES in `SES_REGION`
(default `eu-north-1`) from `EMAIL_FROM`. `PUBLIC_BASE_URL` (default `http://localhost:8000`) is the
site's address for the links; it's never taken from the request. The email is in the language the
page was in, from the `email.*` keys of the dictionaries.

To run it by hand:

```bash
uv run --directory backend python maintenance.py
```

### Events

Events (Christmas markets, festivals, matches...) are data in
[backend/events.json](backend/events.json), loaded and checked by
[backend/events.py](backend/events.py). There is no admin UI: adding an event is a pull request.

- **Event page**: `#/event/<slug>`, public. The event's name, dates and opening hours, a day to
  choose, then *Find a ride there* (the Passenger form with the event as the destination, the
  visitor enters their start), times to *ride back* from closing time until 1.5 hours after it and
  when the bars close (the Passenger form with the event as the start), *Offer a drive there*, a
  *Copy link* / *Share* button and a QR code (also downloadable as SVG for printing).
- **Shareable link**: `/e/<slug>` (e.g. <https://rideandrail.ch/e/montreux-noel-2026>), a tiny page
  with the event's name and dates for link previews (WhatsApp etc.) that redirects to
  `/#/event/<slug>`. A `?lang=de|fr|it|en` on it is passed on to the app, for printed codes.
- **Drive back**: after a driver posts a drive ending within 1 km of an event on one of its days,
  the Driver page offers to post the drive back at once, at one of the suggested times.
- **Discoverability**: current and upcoming events are listed as one row of links on the Passenger
  and Browse pages (on phones, a row that scrolls sideways).
- **Demo drives**: on each day an event is open, drivers from the 8 nearest demo places 10 to 70 km
  away go there in the afternoon or evening and back between closing time and 1.5 hours after it
  (two of them on Fridays and Saturdays); see `EVENT_DRIVES` in
  [backend/demo_drives.py](backend/demo_drives.py). New events get them automatically.

#### Adding an event

Add an object to `events` in [backend/events.json](backend/events.json):

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

### Translations

The UI is in German, French, Italian and English, with no library and no build step:

- [frontend/i18n/](frontend/i18n/) holds one dictionary per language (`en.json` is the reference and
  the fallback for missing keys). [frontend/i18n.js](frontend/i18n.js) picks the language: `?lang=xx`
  in the URL (also saved), then the visitor's saved choice, then the browser's languages, then
  English. The DE · FR · IT · EN picker in the header switches it.
- In HTML, `data-i18n="key"` (or `data-i18n-html`, `data-i18n-placeholder`) marks translated text;
  the English stays in the HTML as a fallback. In JavaScript, `t("key", { name })`; with a `count`
  param, plurals come from `key.one` / `key.other`. Dates and numbers use Swiss formats (`de-CH`
  etc.).
- The privacy notice is translated as whole texts: one `<article lang="xx" data-i18n-block>` per
  language in [frontend/privacy.html](frontend/privacy.html).
- API errors carry a code (`{"detail": {"code": "drive_not_found", ...}}`); the frontend shows
  `errors.<code>` from the dictionary.

Visitors are addressed formally in every language ("Sie", "vous", "Lei"); Italian buttons use the
infinitive ("Trovare un passaggio"). To add a string, add its key to all four JSON files.
[backend/tests/test_i18n.py](backend/tests/test_i18n.py) fails if a key or placeholder is missing in
any language, if the HTML or app.js uses an unknown key, if the English in the HTML drifts from
`en.json`, or if an informal form ("du", "tu") slips in. The translations were written without native review; have native speakers check them,
the privacy notice above all, before showing the site widely.

## Deploying to AWS

Infrastructure is Terraform in [infra/](infra/): one Graviton EC2 server (`t4g.micro`, Ubuntu 24.04)
in `eu-north-1` (Stockholm, the AWS project's only allowed Region), running Docker Compose
([deploy/compose.yaml](deploy/compose.yaml)):

- **proxy**: Caddy ([deploy/caddy/Caddyfile](deploy/caddy/Caddyfile)), which handles HTTPS via
  Let's Encrypt once a domain is set, plus per-IP rate limits. `POST /api/match` is limited to
  10/min, since each match calls the free OSRM and transport.opendata.ch APIs. `POST
  /api/auth/link` (each one emails a sign-in link) is limited to 10 per 10 min. Other writes are
  limited to 30/min and all of `/api/*` to 300/min. Over-limit requests get a 429 response.
- **app**: the FastAPI image built from the root [Dockerfile](Dockerfile).

The SQLite database lives on a separate EBS volume mounted at `/data`, with daily snapshots kept for
7 days, so it survives redeploys and even replacing the server. There's no load balancer and no SSH:
the server has a fixed Elastic IP that accepts only ports 80/443, and you get a shell through SSM
Session Manager. Expect roughly $11/month: the instance (~$6.30), the IPv4 address (~$3.65), the
disks and snapshots (~$1), the DNS zone ($0.50), and the uptime alarm ($0.10). The domain itself is billed by Hostpoint.

Pushes to `main` are tested, built and deployed by
[.github/workflows/deploy.yml](.github/workflows/deploy.yml):

1. Build arm64 images natively.
2. Push them to ECR.
3. Run [deploy/deploy.sh](deploy/deploy.sh) on the server via SSM Run Command.

CI signs in to AWS as the `ride-sharing-github-deploy` IAM user, whose access key is stored in GitHub
secrets. Keyless OIDC would be preferable, but the project's managed SCP denies `iam:*Provider*`.

Requires Terraform >= 1.10, the AWS CLI logged in to the target account (`aws login --remote`), and
`gh` logged in with the `workflow` scope.

1. **State bucket** (once per account):
   ```bash
   cd infra/bootstrap && terraform init && terraform apply
   ```
2. **Infrastructure**:
   ```bash
   cd infra
   terraform init -backend-config="bucket=<state_bucket output from step 1>"
   terraform apply
   ```
   The server finishes its first-boot setup (Docker, data volume) a few minutes after `apply`
   returns. The first deploy waits for it.
3. **GitHub settings**: copy `terraform output github_actions_variables` into repo *variables*, and
   create the deploy user's access key directly into repo *secrets*, without printing it:
   ```bash
   aws iam create-access-key --user-name ride-sharing-github-deploy --output json | python3 -c '
   import json, subprocess, sys
   k = json.load(sys.stdin)["AccessKey"]
   subprocess.run(["gh", "secret", "set", "AWS_ACCESS_KEY_ID", "--body", k["AccessKeyId"]], check=True)
   subprocess.run(["gh", "secret", "set", "AWS_SECRET_ACCESS_KEY", "--body", k["SecretAccessKey"]], check=True)'
   ```
4. **First deploy**: push to `main`, or run the *Test and deploy* workflow from the Actions tab. The
   app is then at `terraform output app_url`, with the demo drives created at startup.

**Domain + HTTPS**: the site runs at <https://rideandrail.ch>. The domain is registered at Hostpoint,
and its DNS is a Route 53 hosted zone managed by Terraform ($0.50/month). To set up a domain:
1. Set `domain_name` in `infra/terraform.tfvars` and run `terraform apply`. This creates the hosted
   zone, plus A records for the apex and `www` pointing at the server, and what sign-in emails need
   (below).
2. At the registrar (Hostpoint control panel → the domain → nameservers), replace the nameservers
   with `terraform output name_servers`.
3. Set the GitHub variables `SITE_ADDRESS`, `EMAIL_FROM` and `PUBLIC_BASE_URL` from
   `terraform output github_actions_variables` and re-run the workflow.

Caddy then obtains Let's Encrypt certificates, redirects HTTP to HTTPS, and redirects `www` to the
apex.

**Sign-in emails** ([infra/email.tf](infra/email.tf)): with a domain, Terraform creates an Amazon SES
domain identity with Easy DKIM (three CNAME records), a custom MAIL FROM domain `mail.<domain>`
(MX and SPF records), SPF (`include:amazonses.com -all`) and DMARC (`p=reject`) records for the
domain, and lets the server's instance role send from `<email_from_local_part>@<domain>` (default
`no-reply`) and nothing else. The app container reaches the instance role through the metadata
service (hop limit 2, [infra/server.tf](infra/server.tf)). SES costs $0.10 per 1,000 emails.

SES starts in **sandbox mode**: it only delivers to verified addresses, at most 200 a day. To test
before then, list testers' addresses in `ses_sandbox_test_recipients`; AWS emails each a
confirmation link. For real users, request production access once in the SES console (*Account
dashboard* → *Request production access*), describing the use (transactional sign-in emails only,
low volume); AWS usually answers within a day. Without `EMAIL_FROM` and `PUBLIC_BASE_URL`, the app
sends no links (the API answers 503); viewing as a demo driver still works.

Notes:
- **Downtime per deploy:** the app container is replaced in a few seconds of downtime. The proxy only
  restarts when `deploy/caddy/` changes.
- **Logs:** CloudWatch log group `/ride-sharing`, with streams `app` and `proxy`.
- **Uptime alerts:** with `alert_email` set in `infra/terraform.tfvars`, a Lambda function
  ([infra/health_check/](infra/health_check/handler.py)) checks `/api/health` every 5 minutes. After
  two failed checks in a row, a CloudWatch alarm emails that address, and emails again on recovery.
  AWS first sends a confirmation link, which must be clicked. Route 53 health checks aren't used
  because their alarms must live in us-east-1, which the AWS project can't use. See
  [infra/monitoring.tf](infra/monitoring.tf).
- **Shell:** EC2 console → the `ride-sharing` instance → *Connect* → *Session Manager*. Then run
  `sudo -i; cd /opt/ride-sharing; docker compose ps` (or `logs`).
- **OS patches:** Ubuntu installs security updates automatically through unattended-upgrades. Changes
  to [infra/user_data.sh.tftpl](infra/user_data.sh.tftpl) replace the server on the next apply; the
  data volume is kept.
- **Restoring data:** create a volume from one of the daily snapshots, under EC2 → *Snapshots*.

## Project layout

```
backend/
  main.py         FastAPI app + routes, sessions and CSRF check, mounts frontend/ as static files
  auth.py         sign-in by email link, sessions, profile (18+, code of conduct), demo-driver view
  mailer.py       email: console backend (development, tests) or Amazon SES
  admin.py        command line: ban / unban an account
  database.py     SQLite schema (created and migrated at startup) + access layer
  matching.py     route corridor matching with search radii (OSRM, haversine fallback)
  search.py       upcoming drives that fit a trip (used by searches and ride requests)
  notify.py       ride requests: which drivers are told, limits, the inbox
  events.py       events as destinations: loads and validates events.json, hours, return times, /e/ page
  events.json     the events (Christmas markets...): adding one is a pull request, see Events
  pricing.py      demo prices (a placeholder until pricing is designed)
  transit.py      transport.opendata.ch integration
  maintenance.py  hourly upkeep: demo drives refresh, 30-day data retention
  demo_drives.py  rolling week of fictional demo drives
frontend/
  index.html      pages: #/ride (Passenger, the default), #/browse (public), #/drive (Driver), #/inbox,
                  #/account, #/event/<slug> (an event, public); sign-in, #/signin/<token> (opening a
                  link) and first-sign-in profile
  privacy.html    privacy notice, linked from the demo banner
  conduct.html    code of conduct (draft for legal review), accepted at first sign-in
  app.js          Leaflet map, point picking, address autocomplete, API calls
  i18n.js         language choice and translation of the page (see Translations)
  i18n/           en/de/fr/it dictionaries
  style.css       teal/white theme, text wordmark (no logo yet)
infra/            Terraform for AWS (VPC, EC2 server, EBS data volume + snapshots, ECR, CI deploy user,
                  DNS, SES for sign-in emails)
  bootstrap/      one-time S3 bucket for Terraform state
deploy/           what runs on the server: compose.yaml, deploy.sh, caddy/ (proxy image)
docs/             design documents for planned features (ride-predictions.md)
Dockerfile        app image (uvicorn, non-root, DB on /data)
.github/workflows/deploy.yml   test → build arm64 images → push → deploy via SSM on push to main
```

## API

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
- `POST /api/drives` — may change; posts a drive as the signed-in driver
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
  returns the request and how many drivers were told
- `GET  /api/requests` — signed in; one's open ride requests. `DELETE /api/requests/{id}` — may
  change; cancels one's own (403 `not_your_request`)
- `GET  /api/notifications` — signed in; one's inbox and settings (a demo-driver view leaves out
  real passengers' requests). `POST /api/notifications/read` — signed in.
  `POST /api/notifications/{id}/action` (`offer` / `decline` / `mute` / `view`) and
  `PUT /api/notification_settings` — may change

Events:

- `GET  /api/events?lang=de|fr|it|en` — public; current and upcoming events, the soonest first, with
  the name in that language (`name`, and all of them in `names`), place, first and last day,
  `status` (`current` / `upcoming`)
- `GET  /api/events/{slug}?lang=…` — public; one event, also with its `hours`, `exceptions` and
  `schedule`: one entry per open day with `open`, `close`, `late_close` and the suggested departures
  back (`return_times`); 404 `event_not_found` for an unknown slug
- `GET  /e/{slug}` — public; the shareable link: an HTML page with a link preview that redirects to
  `/#/event/{slug}` (404 for an unknown slug, still redirecting)

Full request/response shapes of the ride endpoints are documented in the implementation brief (it
predates sign-in: ignore the names in its request bodies).
