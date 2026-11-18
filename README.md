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

**Planned next: ride predictions.** The app spots people's recurring trips and offers to publish
their next drives, or request their next rides, after they confirm. Designed, not built yet.

See [docs/ride-predictions.md](docs/ride-predictions.md) for the design of both, the rules and the
reasons behind them.

## Stack

- Backend: Python + FastAPI, plain `sqlite3` (no ORM)
- Frontend: plain HTML/JS + Leaflet (OpenStreetMap tiles)
- Routing: our own [OSRM](https://project-osrm.org) server with the Switzerland
  [OpenStreetMap](https://www.openstreetmap.org/copyright) extract, refreshed monthly (see
  [Routing](#routing)); falls back to straight lines (haversine) if unreachable
- Public transport: [transport.opendata.ch](https://transport.opendata.ch) (all Swiss operators)
- Address search: [geo.admin.ch Search API](https://api3.geo.admin.ch) (Swiss addresses/places)
- Auth: none — you enter a first + last name once (remembered in the browser), which auto-registers a user
- Languages: German, French, Italian and English, see [Translations](#translations)

## Run it

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) (it fetches the right Python
version automatically if needed).

```bash
uv sync
uv run uvicorn main:app --reload --app-dir backend --port 8000
# open http://localhost:8000
```

Road routes come from the OSRM server at `OSRM_URL` (default `http://osrm:5000`, the server's own,
see [Routing](#routing)). Without one, drives get straight-line routes. For real routes locally,
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

### Demo drives and data retention

At startup and then every hour, the app runs [backend/maintenance.py](backend/maintenance.py):

- **Demo drives**: [backend/demo_drives.py](backend/demo_drives.py) keeps a rolling week of fictional
  drives (about 33,000 by some 4,000 drivers) so "Browse rides" and the Passenger page always have
  something to show. They form a network over the whole country, from the cities down to small
  towns, denser in Ticino and Graubünden: weekday commutes, midday
  drives on the busy corridors and weekend trips, with departures spread over the day so a search
  at most times finds something. Sign in as one of the drivers (e.g. Jean Dupont) to see their
  Driver page. Demo drives that have departed or no longer match the list are deleted, and missing
  ones for the next 7 days are added. They're flagged `is_demo` in the database. Each trip's road
  route is fetched from OSRM once and stored once in `demo_routes`, shared by all the trip's
  drives: about 1,500 routes, a minute or so from our own OSRM server. Against the public OSRM
  server (`OSRM_URL=https://router.project-osrm.org`), there's a one-second pause after each,
  its usage limit (`OSRM_PAUSE_S` overrides the pause). Demo drives made while routing was down
  get their road route at a later refresh.
- **Visitors' data is kept for 30 days**: drives are deleted 30 days after their departure, searches
  30 days after they were made, and then names with no drive or search left. Nothing in the UI shows
  any of this by then. On the server, deleted data also remains in the daily disk snapshots for 7
  more days.

To run it by hand:

```bash
uv run --directory backend python maintenance.py
```

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

Infrastructure is Terraform in [infra/](infra/): one Graviton EC2 server (`t4g.small`: 2 vCPUs,
2 GiB, Ubuntu 24.04) in `eu-north-1` (Stockholm, the AWS project's only allowed Region), running
Docker Compose ([deploy/compose.yaml](deploy/compose.yaml)), each container with a memory limit:

- **proxy**: Caddy ([deploy/caddy/Caddyfile](deploy/caddy/Caddyfile)), which handles HTTPS via
  Let's Encrypt once a domain is set, plus rate limits. Searches (`POST /api/match` and
  `/api/requests`) are limited to 30/min per IP and 60/min for all clients together, about what the
  server's one app process can serve (see [Load test](#load-test)); other writes to 120/min per IP,
  and all of `/api/*` to 1200/min per IP. The per-IP limits are high because phones on mobile
  networks share public IPv4 addresses (carrier-grade NAT): at an event, many people can come from
  one address. Over-limit requests get a 429 response.
- **app**: the FastAPI image built from the root [Dockerfile](Dockerfile).
- **osrm**: the routing server, see [Routing](#routing).

The SQLite database lives on a separate EBS volume mounted at `/data`, with daily snapshots kept for
7 days, so it survives redeploys and even replacing the server. There's no load balancer and no SSH:
the server has a fixed Elastic IP that accepts only ports 80/443, and you get a shell through SSM
Session Manager. Expect roughly $18/month: the instance (~$12.60), the IPv4 address (~$3.65), the
disks and snapshots (~$1), the DNS zone ($0.50), the routing images in ECR (~$0.15), and the
uptime alarm ($0.10). The domain itself is billed by Hostpoint. The routing data is built on
GitHub's free arm64 runners (public repository).

Pushes to `main` are tested, built and deployed by
[.github/workflows/deploy.yml](.github/workflows/deploy.yml):

1. Build arm64 images natively.
2. Push them to ECR.
3. Run [deploy/deploy.sh](deploy/deploy.sh) on the server via SSM Run Command, with the newest
   routing image in ECR (built separately, see [Routing](#routing)).

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
4. **Routing data**: run the *Routing data* workflow from the Actions tab (~10 minutes). It pushes
   the first routing image to ECR, then starts the first deploy. Deploys fail until there is one.
5. **First deploy**: push to `main`, or run the *Test and deploy* workflow from the Actions tab. The
   app is then at `terraform output app_url`, with the demo drives created at startup.

**Domain + HTTPS**: the site runs at <https://rideandrail.ch>. The domain is registered at Hostpoint,
and its DNS is a Route 53 hosted zone managed by Terraform ($0.50/month). To set up a domain:
1. Set `domain_name` in `infra/terraform.tfvars` and run `terraform apply`. This creates the hosted
   zone, plus A records for the apex and `www` pointing at the server. It also adds SPF/DMARC records
   saying the domain sends no mail.
2. At the registrar (Hostpoint control panel → the domain → nameservers), replace the nameservers
   with `terraform output name_servers`.
3. Set the GitHub variable `SITE_ADDRESS` from `terraform output github_actions_variables` and re-run
   the workflow.

Caddy then obtains Let's Encrypt certificates, redirects HTTP to HTTPS, and redirects `www` to the
apex.

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

## Routing

Road routes (when a drive is posted, and once per demo trip) come from our own
[OSRM](https://project-osrm.org) server, the `osrm` service in [deploy/compose.yaml](deploy/compose.yaml),
reachable only by the app. Its image holds car routes for Switzerland, built from Geofabrik's
[Switzerland extract](https://download.geofabrik.de/europe/switzerland.html) of OpenStreetMap
(© OpenStreetMap contributors, ODbL) with OSRM's multi-level Dijkstra (MLD) algorithm:

- [routing/build.sh](routing/build.sh) downloads the extract (checking its MD5), preprocesses it
  in the pinned OSRM image (`osrm-extract` with the car profile, `osrm-partition`,
  `osrm-customize`) and builds the image from [routing/Dockerfile](routing/Dockerfile), tagged with
  the extract's date, e.g. `261117-mld`. Preprocessing needs ~4 GB of RAM (`osrm-extract`: ~3 min,
  3.5 GB peak on a 4-CPU arm64 runner), far more than the server has, so it never runs there.
- [routing/measure.sh](routing/measure.sh) starts the image three ways (data loaded in RAM,
  memory-mapped, memory-mapped in 256 MB) and reports start-up time, memory and latency of the
  app's queries.
- [.github/workflows/routing-data.yml](.github/workflows/routing-data.yml) runs both on a native
  arm64 runner on the 2nd of each month, when `routing/` changes on `main`, or by hand (*Routing
  data* → *Run workflow*). On `main` it pushes the image to ECR (`ride-sharing-routing`, the last
  3 kept) and starts a deploy, which runs the newest routing image. Pull requests build and
  measure both MLD and CH, without pushing.

The data is ~1 GB (image ~1.1 GB, ~475 MB compressed). The server maps it (`--mmap`) instead of
loading it into memory: the kernel keeps the parts in use in its page cache and drops them under
memory pressure. To refresh the data now, run the *Routing data* workflow on `main`. To move to a
new OSRM release, change the pinned image in `routing/build.sh` (tag and digest; the data must be
built by the release that serves it); the pull request's workflow run shows the new measurements.

If OSRM is down, the app retries twice, then uses straight lines for a minute (a warning in the
`app` log) before asking again; demo drives made meanwhile get their road route at a later hourly
refresh.

## Load test

[loadtest/](loadtest/) simulates an event evening with [Locust](https://locust.io) (pinned): many
passengers searching for a ride to and from an event in Bern, browsing drives and their inbox,
some asking for rides, and a few drivers posting drives. Run it against a local instance only,
never against the live site (the locustfile refuses any other host):

```bash
loadtest/run.sh 300 5m 10   # users, duration, users started per second
```

It starts [loadtest/stub_upstreams.py](loadtest/stub_upstreams.py), a stand-in for OSRM and
transport.opendata.ch with realistic response times (so no one else's server is loaded), fills a
fresh database with the demo drives, starts the app on 2 CPUs (`APP_CPUS`), runs Locust, and
prints the results with the app's memory and CPU use. Results are in `loadtest/.work/`.

## Project layout

```
backend/
  main.py         FastAPI app + routes, mounts frontend/ as static files
  database.py     SQLite schema + access layer
  matching.py     route corridor matching with search radii (OSRM, haversine fallback)
  search.py       upcoming drives that fit a trip (used by searches and ride requests)
  notify.py       ride requests: which drivers are told, limits, the inbox
  pricing.py      demo prices (a placeholder until pricing is designed)
  transit.py      transport.opendata.ch integration
  maintenance.py  hourly upkeep: demo drives refresh, 30-day data retention
  demo_drives.py  rolling week of fictional demo drives
frontend/
  index.html      pages: #/ride (Passenger, the default), #/browse (public), #/drive (Driver), #/inbox
  privacy.html    privacy notice, linked from the demo banner
  app.js          Leaflet map, point picking, address autocomplete, API calls
  i18n.js         language choice and translation of the page (see Translations)
  i18n/           en/de/fr/it dictionaries
  style.css       teal/white theme, text wordmark (no logo yet)
infra/            Terraform for AWS (VPC, EC2 server, EBS data volume + snapshots, ECR, CI deploy user)
  bootstrap/      one-time S3 bucket for Terraform state
deploy/           what runs on the server: compose.yaml, deploy.sh, caddy/ (proxy image)
routing/          OSRM image with Switzerland's car routes: build.sh, measure.sh, Dockerfile
loadtest/         event-evening load test (Locust) against a local instance, upstream stand-ins
docs/             design documents for planned features (ride-predictions.md)
Dockerfile        app image (uvicorn, non-root, DB on /data)
.github/workflows/deploy.yml   test → build arm64 images → push → deploy via SSM on push to main
.github/workflows/routing-data.yml   monthly: build + measure the routing image → ECR → deploy
```

## API

- `GET  /api/health`
- `POST /api/drives` — driver posts a drive
- `GET  /api/drives?upcoming_only=true[&driver_name=…]` — browse drives, or one driver's own
- `GET  /api/drives/{id}` — one drive with full route geometry
- `DELETE /api/drives/{id}?driver_name=…` — driver cancels their own drive
- `GET  /api/ride_requests?passenger_name=…` — a passenger's recent searches (deduplicated)
- `POST /api/requests` — passenger asks for a ride nobody has posted (same body as `/api/match`);
  returns the request and how many drivers were told
- `GET  /api/requests?passenger_name=…`, `DELETE /api/requests/{id}?passenger_name=…` — a
  passenger's open ride requests; cancel one
- `GET  /api/notifications?name=…` — a user's inbox and settings; `POST /api/notifications/read?name=…`,
  `POST /api/notifications/{id}/action` (`offer` / `decline` / `mute` / `view`),
  `PUT /api/notification_settings`
- `POST /api/match` — passenger requests a ride (optional pickup/drop-off radii and time tolerance);
  returns ranked driver matches, a last-mile transit suggestion for the best ones, up to 5 rides just
  outside the selection (`outside_matches`, each saying what it's outside of), and a pure
  public-transport alternative

Full request/response shapes are documented in the implementation brief.
