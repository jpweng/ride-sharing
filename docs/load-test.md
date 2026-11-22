# Load test

[loadtest/](../loadtest/) simulates an event evening with [Locust](https://locust.io) (pinned): many
passengers searching for a ride to and from an event in Bern, browsing drives and their inbox,
some asking for rides, and a few drivers posting drives. Run it against a local instance only,
never against the live site (the locustfile refuses any other host):

```bash
loadtest/run.sh 300 5m 10   # users, duration, users started per second
```

It starts [loadtest/stub_upstreams.py](../loadtest/stub_upstreams.py), a stand-in for OSRM and
transport.opendata.ch with realistic response times (so no one else's server is loaded), fills a
fresh database with the demo drives, starts the app on 2 CPUs (`APP_CPUS`), runs Locust, and
prints the results with the app's memory and CPU use. Results are in `loadtest/.work/`.

Results (November 2026, a laptop's Core Ultra 7 255H, stub transit delays 0.15 s / 0.5 s): a
search costs ~0.2 s of CPU, and one app process (one CPU: Python's GIL) saturates at ~2.4
searches/s plus ~4 other requests/s; beyond that everything queues. At 40 users (~1 search/s,
3.3 requests/s) `/api/match` took 1.4 s median, 4.1 s at p95 (mostly the transit lookups), other
endpoints under 150 ms at p95, with 0.5 CPU and 250 MB of memory. A t4g CPU is roughly half as
fast, hence Caddy's server-wide limit of 60 searches/min.

## What a search computes

Those figures are from before a search looked for rides combined with public transport
([combined trips](combined-trips.md)), which made it read the time window's drives three times:
for the drives of the whole trip, for a ride first or last, and for a ride between two public
transport legs. Measured on their own (60 searches across the country on a week of demo drives,
the same laptop, no timetable), those three steps took 420 ms a search; on the server, about 1.9 s.

Two changes brought them to 135 ms on the laptop, with the same results, drive for drive:

- **The database leaves out the drives that can't serve the trip**
  ([backend/database.py](../backend/database.py), `list_drives`): with `passing_near`, those
  whose two ends are too far from a point for their route to pass near it, a test in latitude and
  longitude that needs no trigonometry and never leaves out a drive the search's own test would
  keep; with `ends_inside`, those outside a box around the passenger's way. A search turned 20,000
  rows into Python objects to keep a few hundred.
- **Routes are kept ready between searches** ([backend/search.py](../backend/search.py),
  `prepared_routes`): reading a stored route and thinning it out for matching was most of the
  rest, and the drives of a demo trip share one route. The last 400 used are kept for an hour, a
  few megabytes.

The load test itself has not been run again since.
