# Plan: demo drives only on demo.rideandrail.ch

Status: **plan only, nothing implemented yet.** Written against `origin/main` as of 2026-11-18,
after the pull request that separated demo drivers (#2), email sign-in (#6) and the restructure
(#11).

## Goal

- **rideandrail.ch** (and www) is the real service: real accounts and real drives only. It has
  no fictional drives, no "view as a demo driver", and no demo banner.
- **demo.rideandrail.ch** shows the product with the fictional network: today's site, with its
  demo drives, "Demo" badges and read-only "view as a demo driver".

## What already exists

- **`DEMO_DRIVES=off`** (`backend/demo_drives.py`, `enabled()`) hides demo drives everywhere
  at once. The next maintenance run deletes them and their stored routes, and creates no new
  ones (`backend/maintenance.py`). In production this is the optional GitHub variable
  `DEMO_DRIVES`.
- **Real ride requests never reach demo drivers**, and demo drives are never bookable.
- **Accounts:** sign-in by an emailed link. Demo drivers are flagged in `users.is_demo`, and
  their sessions are read-only.

So switching off the real site's demo drives is a single variable. What's missing is somewhere
for the demo to keep running.

## Approach: a second app container on the same server

Run the same image twice, side by side, with separate databases. Both apps use the same OSRM
container.

| | rideandrail.ch | demo.rideandrail.ch |
|---|---|---|
| Container | `app` | `app-demo` (new) |
| `DEMO_DRIVES` | `off` | `on` |
| Database | `/data/app/rideshare.db` | `/data/app-demo/rideshare.db` (new) |
| Email sign-in | yes (SES) | no: only "view as a demo driver" (see open question 1) |

**Why not one app that switches on the hostname?** It would mean one database for both
sites. Real accounts, searches and notifications would sit next to fictional ones, and every
query would need a filter by site. With two databases, the two sites can't leak into each
other, and the demo can be wiped at any time without touching real data.

**Memory:** the server has 2 GiB. Current limits are OSRM 768 MB (mostly page cache, which
shrinks under pressure), app 512 MB and proxy 128 MB, and the OS takes about 350 MB. A second
app with a 384–512 MB limit fits if OSRM's cache gives way. Measure with `docker stats` under
the load test (`loadtest/`) before deciding on a bigger instance.

**Demo database:** it starts empty. The first maintenance run creates the week of drives and
fetches about 1,500 routes from our own OSRM in about a minute. Nothing needs to be copied
from production.

## Changes

### 1. Infrastructure: its own pull request, merged and applied first

- `infra/dns.tf`: an A record for `demo.${var.domain_name}`, pointing at the existing Elastic IP.
- `infra/outputs.tf`: add `DEMO_SITE_ADDRESS = "demo.${var.domain_name}"` to
  `github_actions_variables`.
- `infra/user_data.sh.tftpl`: create `/data/app-demo`, owned by 1000:1000, for future servers.
- `infra/monitoring.tf`: a second health check on `https://demo.<domain>/api/health`, created
  only when demo hosting is enabled.
- **Before merging:** the owner runs `terraform apply`. Nothing changes on the live site.

### 2. Demo container and proxy: switched off until `DEMO_SITE_ADDRESS` is set

- `deploy/compose.yaml`: a new `app-demo` service.
  - It uses the same image, healthcheck and OSRM as `app`, plus `DEMO_DRIVES: "on"`, a bind
    mount of `/data/app-demo` and `awslogs-stream: app-demo`.
  - It has no `EMAIL_FROM`, so it never sends email.
  - It goes in a Compose profile (`profiles: [demo]`) and only runs when `.env` has
    `COMPOSE_PROFILES=demo`.
- `.github/workflows/deploy.yml`: pass `DEMO_SITE_ADDRESS`, and set `COMPOSE_PROFILES=demo`
  only when that variable is set.
- `deploy/deploy.sh`: create `/data/app-demo` (owner 1000:1000) if it is missing.
  `user_data` only runs at a server's first boot.
- `deploy/caddy/Caddyfile`:
  - Move the rate-limit zones into a snippet, and import it in two site blocks:
    `{$SITE_ADDRESS}` → `app:8000`, and `{$DEMO_SITE_ADDRESS}` → `app-demo:8000`.
  - While the variable is empty, the demo block falls back to an address nothing reaches
    (e.g. `:8081`, not published), so the Caddyfile stays valid.
  - The `proxy` service must not wait for `app-demo` when the demo isn't running.
- **Safe on merge:** with the variable unset, production runs exactly as today.

### 3. The page knows which site it is

- A new `backend/routes/site.py`: `GET /api/site` returns `{"demo": <DEMO_DRIVES enabled>,
  "sign_in": <false when SITE_ROLE=demo>, "other_site_url": ...}`. The link target comes from a new optional `OTHER_SITE_URL` variable;
  when it is missing, no link is shown.
- `frontend/app.js` and `frontend/index.html`, on the real site (`demo: false`):
  - Hide the demo banner and the "Just looking? View as a demo driver" box.
  - Show a short note linking to the demo.
- On the demo site (`demo: true`):
  - Keep everything that's there today.
  - Hide email sign-in.
  - Add a link to the real site in the banner.
- `privacy.html` has the same banner. Show it there only on the demo.
- New texts go in new prefix files `frontend/i18n/<lang>/site.json`, in all four languages
  with formal address.
- The backend refuses what the page hides: on the demo, `POST /api/auth/link` answers 403.
  This needs a flag of its own, `SITE_ROLE=demo`, set only on `app-demo`, because local
  development runs with demo drives on and still needs sign-in. The real site needs no extra
  check: with demo drives off, `/api/auth/demo_drivers` offers nobody to view as.
- Tests: `/api/site` in both modes, and `/api/auth/link` refused when `SITE_ROLE=demo`.

### 4. Switch the real site over: no code, done by the owner

1. Set the GitHub variables `DEMO_SITE_ADDRESS=demo.rideandrail.ch` and `OTHER_SITE_URL`, then
   redeploy. Check that demo.rideandrail.ch works, and that browsing and "view as a demo driver"
   are there.
2. Set `DEMO_DRIVES=off` for the real site, then redeploy. Its next maintenance run deletes the
   fictional drives and routes. To undo, set it back to `on`.
3. Point every shared link meant for showing the product to demo.rideandrail.ch.

### 5. Docs

- `docs/demo-drives-and-data-retention.md`: the two sites, and which one runs demo drives.
- `docs/deploying-to-aws.md`: the new variables and the `terraform apply` step.
- `Suggestions.md`: move "Demo drives" to Done.

## Pull requests, in merge order

1. **Infra** (DNS, outputs, user data, monitoring). Before merging: `terraform apply` by the
   owner.
2. **Demo container and proxy.** Shared files: `deploy/compose.yaml` and `deploy.yml`. Safe while
   `DEMO_SITE_ADDRESS` is unset.
3. **Site awareness in the page** (`/api/site`, banner, sign-in on the demo). Shared files:
   `frontend/index.html` and `app.js`.
4. **Owner steps** in section 4. They need no pull request, but they go in PR 3's
   "After merging" section.

PRs 2 and 3 touch different shared files and could be opened together. Merge them in this
order anyway.

## Open questions

1. **Sign-in on the demo:** only "view as a demo driver" (recommended), or real email sign-in
   too? Real sign-in would create accounts that exist only on the demo, with its own data.
2. **Search engines:** both pages have `noindex` today. Remove it from rideandrail.ch when it
   launches, and keep it on the demo for good?
3. **"Demo" in the real site's wording:** the privacy notice and the code of conduct mention the
   demo. Review them once the real site has no demo content.
4. **Server size:** keep the current instance if `docker stats` shows the second app fits.
   Otherwise, size up.

## Effort

About a day: mostly wiring (Caddy, Compose, the workflow, Terraform) plus a small route and the
banner changes, each with tests.
