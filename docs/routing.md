# Routing

Road routes (when a drive is posted, and once per demo trip) come from our own
[OSRM](https://project-osrm.org) server, the `osrm` service in [deploy/compose.yaml](../deploy/compose.yaml),
reachable only by the app. Its image holds car routes for Switzerland, built from Geofabrik's
[Switzerland extract](https://download.geofabrik.de/europe/switzerland.html) of OpenStreetMap
(© OpenStreetMap contributors, ODbL) with OSRM's multi-level Dijkstra (MLD) algorithm:

- [routing/build.sh](../routing/build.sh) downloads the extract (checking its MD5), preprocesses it
  in the pinned OSRM image (`osrm-extract` with the car profile, `osrm-partition`,
  `osrm-customize`) and builds the image from [routing/Dockerfile](../routing/Dockerfile), tagged with
  the extract's date, e.g. `261117-mld`. Preprocessing needs ~4 GB of RAM (`osrm-extract`: ~3 min,
  3.5 GB peak on a 4-CPU arm64 runner), far more than the server has, so it never runs there.
- [routing/measure.sh](../routing/measure.sh) starts the image three ways (data loaded in RAM,
  memory-mapped, memory-mapped in 256 MB) and reports start-up time, memory and latency of the
  app's queries.
- [.github/workflows/routing-data.yml](../.github/workflows/routing-data.yml) runs both on a native
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
