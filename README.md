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

### Seed demo data

Populates a handful of posted drives across Swiss towns so "Browse rides" and the Passenger page
have something to show immediately (sign in as e.g. "Jean Dupont" to see his Driver page):

```bash
uv run --directory backend python seed_data.py
```

## Deploying to AWS

Infrastructure is Terraform in [infra/](infra/): a load balancer in front of a single ECS Fargate
task in `eu-north-1` (Stockholm, the AWS project's only allowed Region), with the SQLite file on EFS
so it survives redeploys. Pushes to `main` are tested, built and deployed by
[.github/workflows/deploy.yml](.github/workflows/deploy.yml). Expect roughly $40/month, mostly the
load balancer.

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
   The ECS service keeps failing to start until step 4 pushes the first image.
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
   app is then at `terraform output app_url`.
5. **Seed demo data** (optional, once only; running it twice duplicates the drives):
   ```bash
   aws ecs run-task --cluster ride-sharing --task-definition ride-sharing --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[<public_subnet_ids>],securityGroups=[<task_security_group_id>],assignPublicIp=ENABLED}" \
     --overrides '{"containerOverrides":[{"name":"app","command":["python","backend/seed_data.py"]}]}'
   ```

**Custom domain + HTTPS** (optional): set `domain_name` in `infra/terraform.tfvars`, run
`terraform apply -target=aws_acm_certificate.app`, add the `acm_validation_records` output as CNAMEs
at your DNS provider, run `terraform apply` (it waits for validation), then CNAME the domain to
`alb_dns_name`. HTTP then redirects to HTTPS.

Notes:
- Each deploy stops the old task before starting the new one (SQLite allows one writer), so the app is
  down for up to about a minute.
- A Terraform change to the task definition (env vars, CPU/memory, ...) goes live on the next CI deploy.
  Re-run the workflow to apply it right away.
- Logs: CloudWatch log group `/ecs/ride-sharing`. EFS is backed up daily by AWS Backup.

## Project layout

```
backend/
  main.py         FastAPI app + routes, mounts frontend/ as static files
  database.py     SQLite schema + access layer
  matching.py     detour-cost route matching (OSRM, haversine fallback)
  transit.py      transport.opendata.ch integration
  seed_data.py    demo data seeder
frontend/
  index.html      pages: #/browse (public), #/ride (Passenger), #/drive (Driver)
  app.js          Leaflet map, point picking, address autocomplete, API calls
  style.css       SBB-inspired red/white theme
infra/            Terraform for AWS (VPC, ALB, ECS Fargate, EFS, ECR, optional ACM, CI deploy user)
  bootstrap/      one-time S3 bucket for Terraform state
Dockerfile        production image (uvicorn, non-root, DB on /data)
.github/workflows/deploy.yml   test → build → push → deploy on push to main
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
