# Search limits

A passenger gives a start, a destination and a time: when they want to leave, or when they want to
be there. How far a drive may pass from them, and what
is proposed for the rest of the way, is decided by the app:
[backend/search.py](../backend/search.py) and [backend/transit_legs.py](../backend/transit_legs.py).

## How far a search looks

- **Usually 10 km** (`SEARCH_RADIUS_KM`): the drives whose route passes within 10 km of the start
  and of the destination, with a pickup within 4 hours of the time asked for.
- **30 km when nothing bookable is that near** (`WIDER_RADIUS_KM`): if none of those drives can
  be booked (there are none, or only demo drives), the bookable drives passing within 30 km of
  both ends are added, marked `wider: true`. Demo drives are left out of this wider look.
- **Never further than the trip is worth:** as in every search, the way to the pickup and the
  way from the drop-off together must be shorter than the trip itself (`matching.match_drive`),
  so a 30 km detour is only proposed for a long trip.

## Leave at, or arrive by

The time is one of two things (`time_kind`, `matching.match_drive`):

- **`depart`** (the default): when the passenger wants to leave. The pickup is compared with it,
  up to 4 hours either way.
- **`arrive`**: when they want to be there. The drop-off is compared with it: never after it
  (times show to the minute), and up to 4 hours before; the latest arrival in time comes first. It
  is the car's drop-off that is in time, not the passenger at their destination: the card says how
  far the drop-off is from it and how long the rest takes (below). The public transport
  alternative is asked for connections that arrive by that time too, and starts with the one to
  take: the one that leaves last (`transit.best_first_to_arrive_by`). One that leaves before the
  quickest and takes over an hour longer is left out: for a village with a few buses a day, the
  timetable also answers with leaving the evening before.

The results stay one list, the best first (closest in time and place), at most 20. Above them,
the page says when it looked further, and those drives carry a "further away" label. A drive
found this way can be booked like any other: a [booking](booking.md) accepts the same 30 km
(`bookings.MAX_GAP_KM`).

Ride requests keep 10 km: a request tells drivers about a trip, and a driver 30 km away is told
only if they chose that with an [area](driver-areas.md).

## Public transport to the pickup and from the drop-off

A drive rarely passes at the door. For each match, the app may propose a public transport
connection from the passenger's start to the pickup (`first_mile_transit`) and from the drop-off
to their destination (`last_mile_transit`), each `{from_station, to_station, connections}`.

A leg is proposed only when it makes sense:

- **The gap is at least 3 km** (`MIN_GAP_KM`). Nobody takes a bus for 1 or 2 km.
- **There is a stop within 3 km of where the drive passes** (`MAX_STOP_KM`), and it isn't the
  stop the passenger would start from, or end at, anyway.
- **A connection fits the car's time.** To the pickup: it arrives at least 10 minutes before the
  car (`TRANSFER_MIN`); the two that leave the least waiting are shown. From the drop-off: it
  leaves at least 10 minutes after the car arrives.
- **The leg takes at most 2 hours, waiting included** (`MAX_LEG_MIN`): a first bus the next
  morning is not a way home.

Otherwise the match shows nothing for that end. The stations are chosen as for the public
transport option: a town picked by name means its station, an address or a point on the map the
nearest stop (`transit.resolve_station`).

Connections come from transport.opendata.ch, a free, rate-limited service: only the first 5
matches that need a leg get one (`MAX_MATCHES`), those that can be booked first, and answers are
cached for a few minutes ([backend/transit.py](../backend/transit.py)). If the service can't be
reached, matches simply come without legs.

## Names and the way on, for each match

So a card can be read without the map, each match's `pickup` and `dropoff` carry more
([backend/meeting_points.py](../backend/meeting_points.py)); the page words it:

- **`name`** (both points): the nearest public transport stop when it is at most 500 m away, else
  `null`. It comes from the timetable service already used for legs (`transit.nearest_named_station`:
  same service, same cache, no new party receives coordinates, so the privacy page is unchanged).
  OSRM's `nearest` isn't used: our server runs without the data street names need
  (`--disable-feature-dataset ROUTE_STEPS`), and a street name lacks its town. The stop is near the
  point, not at it, so the card says "near". Lookups are limited per search: 12 different uncached
  points, those of the rides that can be booked first, asked four at a time (for the whole app)
  and given 3 s in all; when the service doesn't answer within that, it isn't asked again for a
  minute. A point that got no name isn't asked again for 2 minutes. No name never fails a search:
  the card falls back to the wording without a place.
- **`pickup.walk_min`**: the way from the passenger's start on foot, `null` when the pickup is
  200 m or less away.
- **`dropoff.onward`**: the rest of the way to the destination, `{walk_min, car_min, transit_min,
  walk_km, car_km, source}`, or `null` when the drop-off is 200 m or less from it (`NEAR_KM`).
  - `car_min`: one OSRM `table` call for all matches (same timeouts and circuit breaker as
    routes), `source: "osrm"`; without OSRM an estimate from the straight line (`"estimate"`).
  - `walk_min`: **an estimate, not a walking route**: a distance at the walking pace used for
    ranking (12 min per km, `matching.MINUTES_PER_GAP_KM`). The distance is OSRM's road distance
    when there is one, else the straight line times 1.3; never below the straight line. The same
    estimate gives `pickup.walk_min`. The card leaves a walk of more than an hour out, and shows
    longer times in hours and minutes.
  - `transit_min`: from the car's drop-off time to the arrival of the first connection of
    `last_mile_transit`; `null` when there is no such leg (no extra timetable call).

The page also links the way from the start to the pickup and from the drop-off to the destination
to Google Maps' plain URL scheme (`https://www.google.com/maps/dir/?api=1&origin=…&destination=…`,
new tab, `rel="noopener"`): no Google API, no key, nothing sent from the server.

## In the API

`POST /api/match` (see [API](api.md)) returns, next to `driver_matches` and `transit_option`,
`search_radius_km` and `wider_radius_km`; each match has `wider`, `first_mile_transit` and
`last_mile_transit`.
