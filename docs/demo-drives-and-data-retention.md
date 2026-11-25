# Demo drives and data retention

At startup and then every hour, the app runs [backend/maintenance.py](../backend/maintenance.py):

- **Demo drives**: [backend/demo_drives.py](../backend/demo_drives.py) keeps a rolling week of fictional
  drives (about 33,000 by some 4,000 drivers) so the Passenger page always has
  something to show. They form a network over the whole country, from the cities down to small
  towns, denser in Ticino and Graubünden: weekday commutes, midday
  drives on the busy corridors and weekend trips, with departures spread over the day so a search
  at most times finds something. "View as a demo driver" on the sign-in page (e.g. Jean Dupont)
  shows their Driver page, read-only. Demo drives that have departed or no longer match the list are
  deleted, and missing ones for the next 7 days are added. They're flagged `is_demo` in the
  database, and so are demo drivers in `users`. Each trip's road
  route is fetched from OSRM once and stored once in `demo_routes`, shared by all the trip's
  drives: about 1,500 routes, a minute or so from our own OSRM server. Against the public OSRM
  server (`OSRM_URL=https://router.project-osrm.org`), there's a one-second pause after each,
  its usage limit (`OSRM_PAUSE_S` overrides the pause). Demo drives made while routing was down
  get their road route at a later refresh. Demo drives are labelled "Demo", never bookable
  (`"bookable": false` in the API); in a passenger's search results they open and show on the map
  like any drive, and real passengers' ride requests never reach demo drivers
  (see [docs/ride-predictions.md](ride-predictions.md#demo-data)).
- **Switching demo drives off**: set `DEMO_DRIVES=off` in the app's environment (default `on`).
  They're hidden everywhere at once, and the next maintenance run deletes them and their stored
  routes, and creates none. Back `on`, they're recreated, fetching the ~1,500 routes again (a
  minute or so from our own OSRM server, about half an hour from the public one). In production
  it's the optional GitHub repo variable `DEMO_DRIVES`, see [Deploying to AWS](deploying-to-aws.md).
- **The iOS app never shows them**, nor anything the site says about the demo, whatever the
  switch says: see [The iOS app shows the site without its demo](ios-app-without-demo.md).
- **Users' data is kept for 30 days**: drives are deleted 30 days after their departure, searches
  30 days after they were made, ride requests 30 days after their time, notifications 30 days after
  they were sent. Nothing in the UI shows any of this by then. Accounts stay until their owner
  deletes them; names without an account are deleted once nothing references them. Sign-in links
  are deleted after a day, expired sessions at once. On the server, deleted data also remains in
  the daily disk snapshots for 7 more days.

To run it by hand:

```bash
uv run --directory backend python maintenance.py
```
