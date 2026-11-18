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
