# Search limits

A passenger gives a start, a destination and a time. How far a drive may pass from them, and what
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

## In the API

`POST /api/match` (see [API](api.md)) returns, next to `driver_matches` and `transit_option`,
`search_radius_km` and `wider_radius_km`; each match has `wider`, `first_mile_transit` and
`last_mile_transit`.
