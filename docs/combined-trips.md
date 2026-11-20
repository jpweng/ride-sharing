# A ride and public transport

A search proposes, next to the drives that make the whole trip, trips that combine a ride with
public transport: the car for part of the way, the train or the bus for the rest.
[backend/combined.py](../backend/combined.py) finds them; the page shows them in
[frontend/combined.js](../frontend/combined.js).

## The two shapes

- **A ride, then public transport** (`ride_then_transit`): a drive picks the passenger up near
  their start (within a search's 10 km) and takes them towards their destination. They get out
  where its route is closest to the destination and go on from the stop there.
- **Public transport, then a ride** (`transit_then_ride`): public transport takes them to a stop
  on a drive's route, where the drive picks them up and drops them near their destination.

A drive that passes near both ends is a plain match, not one of these.

## Which are proposed

- **The car does a real part of the way:** it brings the passenger at least 10 km and 15 % of the
  straight line between the two ends closer (`MIN_PROGRESS_KM`, `MIN_PROGRESS_SHARE`).
- **A stop within 3 km** of the point where they change, and **a connection that fits the car's
  time**: it leaves the stop at least 10 minutes after the car arrives and within the hour, or
  arrives 10 minutes to an hour before the car leaves (`TRANSFER_MIN`, `MAX_WAIT_MIN`).
- **The time asked for is the whole trip's:** for "Leave at", the first thing the passenger boards
  leaves from 15 minutes before to 4 hours after it; for "Arrive by", the last thing they ride
  arrives by it, up to 4 hours before.
- **Not much slower than public transport alone:** a combined trip that takes more than 1.25 times
  the quickest connection for the whole trip plus 15 minutes is left out
  (`MAX_SLOWER_THAN_TRANSIT`). The card says how it compares.
- At most 4, the earliest arrival first ("Leave at") or the latest start first ("Arrive by");
  two drives at the same time on the same trip make one proposal.

## How they are found

1. From the drives of the time window, those that can pass near one end of the trip and far
   enough towards the other, from their two ends alone (as a search does).
2. On the routes of the 80 closest in time, where the passenger gets in and out, and when: plain
   geometry on the stored routes, no routing call.
3. For the 3 most promising of each shape (2 at most changing at the same place), the stop and the
   connection, from the timetable service: 4 lookups at a time, 5 seconds in all. A search never
   waits longer, and without the timetable service there are simply no combined trips.

That is up to 6 more connection lookups per search at the free, rate-limited timetable service
(answers are cached for 5 minutes).

## In the API

`POST /api/match` returns `combined_matches` next to `driver_matches`: each is the drive, as a
match (`pickup`, `dropoff`, `ride_km`, the prices of the part ridden, `bookable`), with:

- `shape`: `ride_then_transit` or `transit_then_ride`;
- `transit`: `{from_station, to_station, connection, fare}`, the connection as in `transit_option`;
- `change`: `{name, lat, lon}`, the stop where the passenger changes;
- `starts`, `arrives`, `total_min`: the whole trip;
- `vs_transit_min`: minutes longer (negative: shorter) than public transport alone, when known.

As for matches, who drives is left out for someone who isn't signed in.

## On the page

Under the matches, "A ride and public transport": a card per trip with its two steps in order,
each with its times, the total and how it compares with public transport alone, and the price of
the ride. Its details give the connection with its ticket link and fare. "Request a seat" asks for
the car's part: from the passenger's start to the stop, or from the stop to their destination
(`ride_from`, `ride_to`), a booking like any other ([Booking](booking.md)). Without a drive for
the whole way, the page says so in one line above them.
