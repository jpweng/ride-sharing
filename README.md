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
- Auth: none — a name field auto-registers a user

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

Populates a handful of posted drives across Swiss towns so "Browse Drives" and "Request a Ride"
have something to show immediately:

```bash
uv run --directory backend python seed_data.py
```

## Project layout

```
backend/
  main.py         FastAPI app + routes, mounts frontend/ as static files
  database.py     SQLite schema + access layer
  matching.py     detour-cost route matching (OSRM, haversine fallback)
  transit.py      transport.opendata.ch integration
  seed_data.py    demo data seeder
frontend/
  index.html      tabs: Post a Drive / Request a Ride / Browse Drives
  app.js          Leaflet map, point picking, address autocomplete, API calls
  style.css       SBB-inspired red/white theme
```

## API

- `GET  /api/health`
- `POST /api/drives` — driver posts a drive
- `GET  /api/drives?upcoming_only=true` — browse drives
- `GET  /api/drives/{id}` — one drive with full route geometry
- `POST /api/match` — passenger requests a ride; returns ranked driver matches (detour cost),
  a last-mile transit suggestion per match, and a pure public-transport alternative

Full request/response shapes are documented in the implementation brief.
