# Plan: demo drives only on demo.rideandrail.ch

Status: **plan only, nothing implemented yet.**

## Goal

- **demo.rideandrail.ch**: today's site, as it is now. Fictional drives, the "Demo" banner and badges,
  and a reminder to use a made-up name. This is the link for investors and pitch decks.
- **rideandrail.ch** (and www): "the real thing". No fictional drives. A passenger only sees drives
  that real people posted, and only real drivers are told about ride requests.

## Approach: same server and image, two app containers

Run the image we already build as two containers on the same EC2 server. They differ only in
one environment variable and one database file:

| | rideandrail.ch | demo.rideandrail.ch |
|---|---|---|
| Container | `app` | `app-demo` (new) |
| `DEMO_MODE` | `0` | `1` |
| Database | `/data/app/rideshare.db` | `/data/app-demo/rideshare.db` (new) |

Caddy sends each hostname to its own container.

**Why not one app that checks the hostname?** The two sites would share one database. Real
users' names, searches and notifications would sit next to fictional ones. `notify.py` would need
to filter by site wherever it touches drivers, and one missing filter would show real users'
data on the demo or fictional drivers on the real site. With two databases, mixing them up is
not possible. The cost is one more container on the server, plus a little extra wiring.

## Changes

### Backend (small)

1. **`DEMO_MODE` flag**, read once from the environment, for example in a new `config.py` or in
   `database.py`. It defaults to on, so local development, tests and the first deploy behave
   exactly as they do today.
2. **`maintenance.run()`**: when demo mode is off, skip `demo_drives.refresh()` and instead
   delete every `is_demo = 1` drive and empty `demo_routes`. This runs on every start and is
   idempotent, so on the first start without demo mode the real site cleans itself up. No
   manual SQL is needed.
3. **`notify.py`**: when demo mode is off, don't build candidates from
   `demo_drives.DEMO_DRIVES`. Today, fictional drivers' templates take part in choosing who
   hears about a ride request, even though they have no drives in the database.
4. **`GET /api/config`** returns `{"demo": true|false}`, so the frontend knows which site it is on.
   It could also be a field added to `/api/health`.

### Frontend (small)

The frontend calls `/api/config` at startup:

- **Demo site:** no change. Add one line to the banner pointing to the real site.
- **Real site:** hide the "Demo" banner and the made-up-name hint (`signin.demoHint`). Show a
  short "early access" note with a link to the demo instead. The "Demo" badge disappears on its
  own, because no drive is `is_demo`.
- New banner texts need translations in de/fr/it/en, in the formal tone that
  `test_formal_tone` checks.
- `noindex` is static HTML in `index.html` and `privacy.html`. Keep it on both sites for now
  (see the open questions).

### Proxy: `deploy/caddy/Caddyfile`

- Move the rate-limit block into a snippet (`(common) { ... }`) and `import` it in two site blocks:
  - `{$SITE_ADDRESS}` sends traffic to `app:8000`, as it does today.
  - `{$DEMO_SITE_ADDRESS}` sends traffic to `app-demo:8000`.
- Caddy gets the Let's Encrypt certificate for the subdomain automatically.

### Compose and deploy: `deploy/compose.yaml`, `deploy/deploy.sh`, `.github/workflows/deploy.yml`

- Add an `app-demo` service, with the same image and healthcheck as `app`, plus
  `DEMO_MODE=1`, a bind mount of `/data/app-demo`, and its own `awslogs-stream: app-demo`.
- Set `DEMO_MODE=0` on `app`. This is the switch: it is flipped last (see Rollout).
- `proxy` depends on both apps being healthy, and gets `DEMO_SITE_ADDRESS`.
- `deploy.sh` creates `/data/app-demo` with owner 1000:1000 if it is missing. `user_data` only
  runs on a server's first boot, so the current server needs this step.
- The workflow writes `DEMO_SITE_ADDRESS` into `.env`, using a new GitHub repository variable.

### Infrastructure: `infra/`

- `dns.tf`: add an A record for `demo.${var.domain_name}` pointing to the same Elastic IP.
- `outputs.tf`: add `DEMO_SITE_ADDRESS = "demo.${var.domain_name}"` to
  `github_actions_variables`.
- `user_data.sh.tftpl`: create `/data/app-demo`, for future servers.
- `monitoring.tf`: optionally, a second health check on `https://demo.rideandrail.ch/api/health`.
  The demo site is what investors open, so it is worth an alert too.
- **Memory:** the server is a `t4g.micro` with 1 GiB of RAM. Check `docker stats` after adding the
  second app. Switch to `t4g.small` (2 GiB, a few CHF more per month) only if the server is short
  of memory. The demo database is about 10 MB, so disk space (4 GB) is not a concern.

### Tests

- With demo mode off: maintenance creates no demo drives and deletes leftover ones.
- With demo mode off: `notify` offers no demo drivers.
- `/api/config` reflects the flag.
- Existing tests run with demo mode on, so they don't change.

### Docs

- README: describe the two sites and the `DEMO_MODE` flag, and update the domain and HTTPS
  section.
- `Suggestions.md`: move "Demo drives: decide whether to switch them off" to Done.

## Rollout (each step can be undone on its own)

1. **Ship the code with `DEMO_MODE` defaulting to on.** The live site doesn't change.
2. **Run `terraform apply`** to add the DNS record, then set the `DEMO_SITE_ADDRESS` GitHub
   variable.
3. **Seed the demo database:** on the server, copy `/data/app/rideshare.db` to
   `/data/app-demo/`. The copy already contains `demo_routes`, so the demo doesn't have to fetch
   thousands of routes from OSRM at one request per second.
4. **Deploy with `app-demo` running and `app` still in demo mode.** Check that
   demo.rideandrail.ch works.
5. **Flip `app` to `DEMO_MODE=0` and deploy.** On startup, the real site deletes its fictional
   drives. Undo: set it back to `1`, and the next hourly refresh refills them.
6. **Update every link given to investors** (the MBA pitch deck, emails) to demo.rideandrail.ch.

## Open questions (decide before step 5)

1. **Testers' existing drives and names:** they stay on the real site (recommended). The copy
   from step 3 also carries them to the demo, where they age out after 30 days. Should they be
   deleted from the demo copy right away instead?
2. **What the real site says before accounts exist:** it still signs people in by a typed name
   (see "Before real drivers" in `Suggestions.md`). Is an "early access" banner on the real site
   enough until accounts arrive, or should the real site stay invite-only or hidden until then?
3. **Search engines:** keep `noindex` on the real site until launch? Keep it on the demo for
   good?
4. **Prices:** the real site would still show the demo price estimate (`pricing.py`). Is that
   acceptable, or should it be labelled differently there?

## Effort

About a day of work, mostly wiring (Caddy, Compose, the workflow, Terraform). The backend and
frontend changes are a few dozen lines plus tests.
