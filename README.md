# Swiss Ride-Share Prototype

A web-based prototype ride-sharing app for Switzerland: drivers post drives, passengers either
browse them or ask for a match based on route corridor (detour-cost) matching, with a public-transport
comparison and last-mile-transit suggestion via [transport.opendata.ch](https://transport.opendata.ch).

See [RIDESHARE_IMPLEMENTATION_BRIEF.md](RIDESHARE_IMPLEMENTATION_BRIEF.md) for the full design rationale.

## Stack

- Backend: Python + FastAPI, plain `sqlite3` (no ORM)
- Frontend: plain HTML/JS + Leaflet (OpenStreetMap tiles)
- Routing: [OSRM demo server](https://router.project-osrm.org) (falls back to haversine if unreachable)
- Public transport: [transport.opendata.ch](https://transport.opendata.ch) (all Swiss operators)
- Address search: [geo.admin.ch Search API](https://api3.geo.admin.ch) (Swiss addresses/places)
- Auth: none — you enter a first + last name once (remembered in the browser), which auto-registers a user

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
  drives between Swiss towns, so "Browse rides" and the Passenger page always have something to
  show. The drives are commutes and weekend trips by Jean Dupont, Sophie Meier, and others; sign in
  as one of them to see their Driver page. Demo drives that have departed are deleted, and missing
  ones for the next 7 days are added. They're flagged `is_demo` in the database.
- **Visitors' data is kept for 30 days**: drives are deleted 30 days after their departure, searches
  30 days after they were made, and then names with no drive or search left. Nothing in the UI shows
  any of this by then. On the server, deleted data also remains in the daily disk snapshots for 7
  more days.

To run it by hand:

```bash
uv run --directory backend python maintenance.py
```

## Deploying to AWS

Infrastructure is Terraform in [infra/](infra/): one Graviton EC2 server (`t4g.micro`, Ubuntu 24.04)
in `eu-north-1` (Stockholm, the AWS project's only allowed Region), running Docker Compose
([deploy/compose.yaml](deploy/compose.yaml)):

- **proxy**: Caddy ([deploy/caddy/Caddyfile](deploy/caddy/Caddyfile)), which handles HTTPS via
  Let's Encrypt once a domain is set, plus per-IP rate limits. `POST /api/match` is limited to
  10/min, since each match calls the free OSRM and transport.opendata.ch APIs. Other writes are
  limited to 30/min and all of `/api/*` to 300/min. Over-limit requests get a 429 response.
- **app**: the FastAPI image built from the root [Dockerfile](Dockerfile).

The SQLite database lives on a separate EBS volume mounted at `/data`, with daily snapshots kept for
7 days, so it survives redeploys and even replacing the server. There's no load balancer and no SSH:
the server has a fixed Elastic IP that accepts only ports 80/443, and you get a shell through SSM
Session Manager. Expect roughly $11/month: the instance (~$6.30), the IPv4 address (~$3.65), the
disks and snapshots (~$1), and the DNS zone ($0.50). The domain itself is billed by Hostpoint.

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
- **Shell:** EC2 console → the `ride-sharing` instance → *Connect* → *Session Manager*. Then run
  `sudo -i; cd /opt/ride-sharing; docker compose ps` (or `logs`).
- **OS patches:** Ubuntu installs security updates automatically through unattended-upgrades. Changes
  to [infra/user_data.sh.tftpl](infra/user_data.sh.tftpl) replace the server on the next apply; the
  data volume is kept.
- **Restoring data:** create a volume from one of the daily snapshots, under EC2 → *Snapshots*.

## Project layout

```
backend/
  main.py         FastAPI app + routes, mounts frontend/ as static files
  database.py     SQLite schema + access layer
  matching.py     detour-cost route matching (OSRM, haversine fallback)
  transit.py      transport.opendata.ch integration
  maintenance.py  hourly upkeep: demo drives refresh, 30-day data retention
  demo_drives.py  rolling week of fictional demo drives
frontend/
  index.html      pages: #/browse (public), #/ride (Passenger), #/drive (Driver)
  app.js          Leaflet map, point picking, address autocomplete, API calls
  style.css       SBB-inspired red/white theme
infra/            Terraform for AWS (VPC, EC2 server, EBS data volume + snapshots, ECR, CI deploy user)
  bootstrap/      one-time S3 bucket for Terraform state
deploy/           what runs on the server: compose.yaml, deploy.sh, caddy/ (proxy image)
Dockerfile        app image (uvicorn, non-root, DB on /data)
.github/workflows/deploy.yml   test → build arm64 images → push → deploy via SSM on push to main
```

## API

- `GET  /api/health`
- `POST /api/drives` — driver posts a drive
- `GET  /api/drives?upcoming_only=true[&driver_name=…]` — browse drives, or one driver's own
- `GET  /api/drives/{id}` — one drive with full route geometry
- `DELETE /api/drives/{id}?driver_name=…` — driver cancels their own drive
- `GET  /api/ride_requests?passenger_name=…` — a passenger's recent searches (deduplicated)
- `POST /api/match` — passenger requests a ride; returns ranked driver matches (detour cost),
  a last-mile transit suggestion per match, and a pure public-transport alternative

Full request/response shapes are documented in the implementation brief.
