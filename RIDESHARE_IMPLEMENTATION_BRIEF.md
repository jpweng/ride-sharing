# Swiss Ride-Share Prototype — Implementation Brief

This is a handoff brief for building/finishing a **web-based prototype** of a ride-sharing app for
Switzerland, meant to be built and run locally (e.g. in WSL) where normal internet access is available.
A working backend already exists (see "What's already built" below, included in
`rideshare-prototype-starter.zip`) — this brief documents the decisions behind it and what's left to do,
so it can be picked up and finished directly.

## Project context

Ride-sharing app for Switzerland. Drivers and passengers are the two roles. It's meant to eventually
combine private rides with public transportation (a ride might only cover part of a trip, with public
transport completing it), not just point-to-point like Uber/BlaBlaCar.

**Scope for this prototype**: web-based only, as simple as possible, SQLite for storage (this is a demo,
not a production system — no need to plan for scale, migrations, etc.).

## The two core use cases

1. **A driver proposes a drive.** Origin, destination, departure time, seats available. Posted for
   passengers to see.
2. **A passenger asks for a ride.** The app finds the most suitable driver(s) based on trajectory (not
   just "same city", but whether the passenger's trip reasonably fits along a driver's route). A
   passenger can also just browse all posted drives directly instead of asking for a match.

## Confirmed decisions (from clarifying questions already asked)

| Decision | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI | Lightweight, pairs well with SQLite, minimal moving parts |
| Database | SQLite, plain `sqlite3` (no ORM) | Prototype only; keeps things transparent and dependency-light |
| Frontend | Plain HTML/JS + Leaflet map | No build step, fastest to demo |
| Location input | Interactive map (Leaflet + OpenStreetMap tiles) | Users click points on a map rather than typing addresses |
| Matching algorithm | **Route corridor matching** with passenger-chosen pickup/drop-off radii (see below) | User asked for corridor/trajectory-based matching, not simple point proximity |
| Public transport | Real integration with **transport.opendata.ch** | Free, no API key, covers all Swiss operators including SBB (the official SBB API requires a business partnership and is overkill for a demo) |
| Auth | None — a name field auto-registers a user row | Prototype; no login flow needed |

## What's already built (in the zip)

A working FastAPI backend, in `backend/`:

- **`database.py`** — SQLite schema and access layer. Tables: `users`, `drives`, `ride_requests`. Plain
  `sqlite3`, `contextmanager`-based connections, no ORM.
- **`matching.py`** — the matching engine (route corridor — see "Matching algorithm" below). Calls the
  public OSRM demo routing server (`router.project-osrm.org`, no API key) for real driving routes/
  distances, and **falls back to haversine (straight-line) distance if OSRM is unreachable**, so the app
  degrades gracefully rather than breaking.
- **`transit.py`** — transport.opendata.ch integration: resolves a lat/lon to the nearest public transport
  stop, and fetches journey connections between two stations. Also fails soft (returns `None`/`[]`) on
  network errors.
- **`main.py`** — FastAPI app wiring it together. Endpoints below. Also mounts `frontend/` as static files.
- **`requirements.txt`** — `fastapi`, `uvicorn[standard]`, `requests`.

**Not yet built**: the frontend (`frontend/index.html` + `frontend/app.js`). Only `frontend/style.css` is
started (a red/white/dark-text theme, tab-based layout, map on the right, form panel on the left — this
was designed to loosely evoke SBB's visual language without literally copying it. Feel free to keep or
replace it).

⚠️ **Important — this backend was written in a sandboxed build environment with no outbound access to
`router.project-osrm.org`, `transport.opendata.ch`, or any map tile/geocoding host**, so the OSRM and
transit integration code has **not been live-tested against the real APIs**. It's written carefully
against my best knowledge of both APIs' request/response shapes, but treat it as needing a first real run
to shake out any bugs (wrong param name, response shape drift, etc.) — that's exactly the kind of thing a
normal internet connection (like WSL has) will surface immediately. Test both integrations early.

## Data model

```
users            id, name (unique)

drives           id, driver_id, driver_name,
                 origin_name, origin_lat, origin_lon,
                 destination_name, destination_lat, destination_lon,
                 departure_time (ISO 8601), seats_available,
                 route_geometry (JSON [[lon,lat], ...] or NULL),
                 route_distance_m, route_duration_s,
                 route_source ('osrm' | 'haversine'),
                 created_at

ride_requests    id, passenger_id, passenger_name,
                 origin_name, origin_lat, origin_lon,
                 destination_name, destination_lat, destination_lon,
                 requested_time (ISO 8601), created_at
```

`ride_requests` rows are logged for history/demo purposes but matching itself is computed live, not
stored.

## API contract (already implemented in `main.py`)

- `GET /api/health` → `{status, time}`
- `POST /api/drives` — driver posts a drive.
  ```json
  {
    "driver_name": "Jean",
    "origin": {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323},
    "destination": {"name": "Genève", "lat": 46.2044, "lon": 6.1432},
    "departure_time": "2026-09-22T08:30:00",
    "seats_available": 3
  }
  ```
  Server calls OSRM for the route, stores distance/duration/geometry, returns the created drive.
- `GET /api/drives?upcoming_only=true` — list drives (for the "browse" use case).
- `GET /api/drives/{id}` — one drive with full route geometry (for drawing it on the map).
- `POST /api/match` — passenger asks for a ride.
  ```json
  {
    "passenger_name": "Marie",
    "origin": {"name": "Morges", "lat": 46.5089, "lon": 6.4979},
    "destination": {"name": "Nyon", "lat": 46.3833, "lon": 6.2333},
    "requested_time": "2026-09-22T08:00:00",
    "pickup_radius_km": 5,
    "dropoff_radius_km": 5,
    "time_tolerance_min": 120
  }
  ```
  The last three are optional (defaults shown). Returns:
  ```json
  {
    "driver_matches": [
      {
        "...drive fields...": "...",
        "route_geometry": [[6.4979, 46.5089], "...simplified [lon, lat] pairs..."],
        "pickup": {"lat": 46.5091, "lon": 6.4982, "gap_km": 0.1, "time": "2026-09-22T08:05"},
        "dropoff": {"lat": 46.3841, "lon": 6.2350, "gap_km": 0.2, "time": "2026-09-22T08:31"},
        "ride_km": 31.6,
        "time_diff_min": 5,
        "score": 8.6,
        "last_mile_transit": null
      }
    ],
    "transit_option": {
      "from_station": "Morges",
      "to_station": "Nyon",
      "connections": [{"departure": "...", "arrival": "...", "duration": "...", "transfers": 0, "products": ["S"]}]
    }
  }
  ```

## Matching algorithm — how "trajectory-based" is implemented

**Route corridor matching** with the passenger's own search radii (since 2026-11; it replaced the earlier
detour-cost matching, which made the driver detour to the passenger's exact start and destination):

- The passenger picks a **pickup radius** and a **drop-off radius** (sliders, 5 km by default, 1–30 km),
  drawn as circles on the map, and a **time tolerance** (± 30 min to ± 4 h, ± 2 h by default).
- A drive matches when its stored road route passes within the pickup radius of the passenger's start
  and, at least `MIN_RIDE_KM` further along, within the drop-off radius of their destination. The
  pickup/drop-off points are the closest points of the route; the driver stays on their route.
- Time is compared at the **pickup**, estimated from the drive's departure and duration: a
  Lausanne → Zurich drive leaving at 7:00 is a ~8:00 ride for a passenger in Bern.
- Drives are ranked by the time the passenger loses: minutes from the requested time, plus
  `MINUTES_PER_GAP_KM` (12, walking pace) per km to the pickup and from the drop-off. Up to
  `MAX_RESULTS` (20) are returned.
- It's plain geometry on the stored routes, no routing call per candidate, so every drive in the time
  window is considered. A cheap ellipse test on the drive's endpoints (`could_pass_near`) skips loading
  the route of drives that can't come near.
- Drives without a road route (OSRM was unreachable when posted) are taken as a straight line.

## Public transport integration — transport.opendata.ch

Used two ways:

1. **Pure transit alternative**: for every match request, also fetch a direct transit connection from the
   passenger's origin to destination, shown as a comparison option (`transit_option` in the response).
2. **Last-mile combo**: for the best `MAX_LAST_MILE_LOOKUPS` (5) driver matches whose drop-off point is
   more than `LAST_MILE_THRESHOLD_KM` (default 1 km) from the passenger's actual destination, fetch a
   transit connection from the stop nearest the drop-off point to the passenger's destination — i.e. "drive with this person, then take the train/bus
   the rest of the way." This is the simplified version of the project's "ride + public transport
   composition" vision, without building full multimodal routing.

API notes (transport.opendata.ch, no key needed):
- `GET /v1/locations?x={lat}&y={lon}&type=coordinate` → nearest stations. **Note the API's `x`/`y` params
  are (lat, lon), not (lon, lat)** — easy to get backwards, already handled correctly in `transit.py`.
- `GET /v1/connections?from=STATION&to=STATION&date=YYYY-MM-DD&time=HH:mm` → journey options.
- Full docs: https://transport.opendata.ch/docs.html

## Remaining work

1. **Set up and smoke-test the backend for real**, in WSL (`python3 -m venv .venv`, `pip install -r
   requirements.txt`, `uvicorn backend.main:app --reload --app-dir backend` or adjust imports/run command
   as needed — the zip currently expects to be run with `backend/` as the working directory or via
   `PYTHONPATH`, double check `main.py`'s relative imports work with however you choose to run it).
2. **Live-test `matching.py` and `transit.py`** against the real OSRM and transport.opendata.ch APIs (not
   possible from the sandbox this was built in) — fix any response-shape surprises.
3. **Build the frontend** (`frontend/index.html` + `frontend/app.js`):
   - Leaflet map (tiles from OpenStreetMap) as the main view.
   - Three views/tabs: "Post a Drive" (driver form), "Request a Ride" (passenger form + results), "Browse
     Drives" (list + map markers of all upcoming drives).
   - For picking origin/destination: a "pick origin" / "pick destination" button toggle, then click the
     map to place a marker (see the `.point-picker` / `.picking` classes already in `style.css` for this
     interaction).
   - Render `POST /api/match` results: ranked driver cards with pickup/drop-off times and distances, and
     for each, if present, the `last_mile_transit` suggestion; plus the standalone `transit_option` as a
     comparison card. CSS classes for this (`.match-card`, `.score`, `.transit-box`, `.last-mile`, etc.)
     are already defined in `style.css`.
   - Draw the driver's route polyline on the map (`route_geometry`, `[lon, lat]` pairs — remember to
     swap to `[lat, lon]` for Leaflet) when viewing a drive or a match.
4. **Seed some demo data** (a handful of posted drives across a few Swiss towns) so the "browse" and
   "match" use cases have something to show immediately.
5. Optional polish: show the driver's full route on the map for a match, add basic form validation
   feedback, a loading state while OSRM/transit calls are in flight (they can take a second or two).

## Suggested run instructions (for the README once finished)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --app-dir backend --port 8000
# open http://localhost:8000
```
