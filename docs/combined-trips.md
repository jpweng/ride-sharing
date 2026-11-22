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

- **The ride lasts at least 10 minutes** (`MIN_RIDE_MIN`), however long the whole trip is: 20
  minutes by car to or from a station count on a trip across the country. (A drive for the whole
  trip is a match, however short.)
- **It goes the passenger's way:** when they get out they are closer to their destination than
  when they got in, by at least half of the km ridden (`MIN_CLOSER_SHARE`).
- **A stop within 3 km** of the point where they change, and **a connection that fits the car's
  time**: it leaves the stop at least 10 minutes after the car arrives and within the hour, or
  arrives 10 minutes to an hour before the car leaves (`TRANSFER_MIN`, `MAX_WAIT_MIN`), counted in
  minutes as the times are shown.
- **The time asked for is the whole trip's:** for "Leave at", the first thing the passenger boards
  leaves from 15 minutes before to 4 hours after it; for "Arrive by", the last thing they ride
  arrives by it, up to 4 hours before.
- **Not much slower than public transport alone:** a combined trip that takes more than 1.25 times
  the quickest connection for the whole trip plus 15 minutes is left out
  (`MAX_SLOWER_THAN_TRANSIT`). A trip at least 5 minutes quicker says so on its card; one that
  isn't says nothing about it.
- At most 4, the earliest arrival first ("Leave at") or the latest start first ("Arrive by");
  two drives at the same time on the same trip make one proposal.

## How they are found

1. From the drives of the time window, those that last at least 10 minutes and can pass near one
   end of the trip, from their two ends alone (as a search does).
2. On the routes of those closest in time (80 routes at most; demo drives on the same trip share
   one), where the passenger gets in and out, and when: plain geometry on the stored routes, no
   routing call. "Closest in time" is about when the passenger is at the car's end of the trip,
   with the whole trip counted as public transport at a minute a km: to arrive by 11:00 after
   200 km, a ride first leaves around 07:40, not around 11:00.
3. The stop and the connection, from the timetable service: 4 lookups at a time, 5 seconds in
   all. A search never waits longer, and without the timetable service there are simply no
   combined trips. Which lookups depends on where the car is in the trip:
   - **The car is at the end the time is about** (a ride first for "Leave at", a ride last for
     "Arrive by"): each drive's own time decides. For the 3 most promising drives (2 at most
     changing at the same place), a lookup at the time the car is at the stop.
   - **Public transport is at that end** (a ride first for "Arrive by", a ride last for "Leave
     at"): the timetable decides which drives are in time. The last bus that arrives by 11:00 may
     leave at 06:24, and only a drive that is at the stop before then makes the trip. For the 3
     most promising places to change at, one lookup at the time asked for (the connections that
     arrive by it, or leave from it) serves every drive that changes there; the 2 best that fit
     one of them are kept. When none fits and a drive is at the stop well before those
     connections (or well after), the closest one gets a lookup at its own time.

That is up to 9 more connection lookups per search at the free, rate-limited timetable service,
usually 6 or fewer (answers are cached for 5 minutes).

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
each with its times, the total (and how much quicker than public transport alone, when it is), and the price of
the ride. Its details give the connection with its ticket link and fare. "Request a seat" asks for
the car's part: from the passenger's start to the stop, or from the stop to their destination
(`ride_from`, `ride_to`), a booking like any other ([Booking](booking.md)). Without a drive for
the whole way, the page says so in one line above them.
