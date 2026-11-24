# A ride and public transport

A search proposes, next to the drives that make the whole trip, trips that combine a ride with
public transport: the car for part of the way, the train or the bus for the rest.
[backend/combined.py](../backend/combined.py) finds them; the page shows them in
[frontend/combined.js](../frontend/combined.js).

## The three shapes

- **A ride, then public transport** (`ride_then_transit`): a drive picks the passenger up near
  their start (within a search's 10 km) and takes them towards their destination. They get out
  where its route is closest to the destination and go on from the stop there.
- **Public transport, then a ride** (`transit_then_ride`): public transport takes them to a stop
  on a drive's route, where the drive picks them up and drops them near their destination.
- **Public transport, a ride, public transport** (`transit_ride_transit`): a drive that is near
  neither end of the trip but goes the passenger's way, like Zürich → Fribourg for someone going
  from St. Gallen to a village near Payerne. They ride all of it: public transport takes them to
  the stop where the drive starts, and on from the stop where it ends.

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
- **The walk to or from the car decides none of this.** The two rules above are about what the
  passenger rides, so a pickup a walk away from their start doesn't take a trip out of a search.
  What a trip says is about all of it ([The trip's times](#the-trips-times)).
- At most 4, the earliest arrival first ("Leave at") or the latest start first ("Arrive by");
  two drives at the same time on the same trip make one proposal.
- **Next to 4 or more drives for the whole way, only drives that can be booked:** the fictional
  demo drives are then left out of the combined trips (as they are of the search's wider look,
  [search limits](search-limits.md)). Such a search usually answers without a single timetable
  lookup for them.

A ride between two public transport legs has two changes, so it has rules of its own:

- **Only when there is room:** they are looked for when the search has fewer than 4 drives and
  combined trips to show, and fill what is left of the 4.
- **The whole drive, near neither end:** it starts more than 10 km from the passenger's start and
  ends more than 10 km from their destination (nearer, it is one of the other shapes). The rules
  above about the ride and the stops apply to it, the stops being within 3 km of its two ends.
- **Not much of a detour:** from the passenger's start to where the drive starts, the drive, and
  from where it ends to their destination are together at most 1.15 times the straight line
  between their two ends (`MAX_DETOUR`). Zürich → Bern is on the way from St. Gallen to Payerne,
  Zürich → Basel is not.
- **It may take longer than the others:** up to 1.5 times public transport alone plus 30 minutes
  (`MAX_SLOWER_BETWEEN`), since two changes seldom make a quick trip. The card says how long it
  takes, as every card does.

## The trip's times

A trip's start, its end and how long it takes are the passenger's, from their own start to their
own destination. The car seldom passes at their door: a ride first picks them up where its route
is closest to their start, a ride last drops them where it is closest to their destination. The
walk between the two is part of the trip:

- **A ride first starts when they leave for the pickup**, the walk before the car is there; **a
  ride last ends when they are at their destination**, the walk after the drop-off. A car at
  16:15, 1.3 km from their start, makes a trip that starts at 15:55.
- **The walk is an estimate**, the one a match's pickup has
  ([backend/meeting_points.py](../backend/meeting_points.py)): 1.3 times the straight line at
  12 minutes a km, not a walking route.
- **No walk within 200 m** (the car is there), **nor beyond an hour on foot** (`MAX_WALK_MIN`,
  about 3.8 km): that far is no walk, the trip is counted from the car and its card says how far
  the car passes.
- **How much quicker than public transport alone** a trip says it is counts the walk.
- The car's own times and the connection are what they are without it: the time to change at the
  stop is the same.
- A ride between two public transport legs has no such walk: public transport takes the passenger
  to the car and on from it.

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
4. When that leaves room, the rides between two public transport legs, in what is left of the 5
   seconds (not started with less than a second left). The drives come from their two ends and
   times alone, no route is loaded to find them. For the 2 most promising places to change at, at
   the end of the trip the time is about, one lookup at the time asked for serves every drive
   there, as in step 3; the 2 most promising drives that fit get a lookup for their other leg, at
   the car's own time.

A search doesn't do its waiting in a row ([backend/routes/match.py](../backend/routes/match.py)):
public transport alone is asked for when the search starts, the stop names and the legs to the
drives found while the combined trips are looked for. A combined trip is compared with public
transport alone once both are there.

That is up to 9 connection lookups per search at the free timetable service, usually 6 or fewer,
and up to 6 more when rides between two public transport legs are looked for (answers are cached
for 5 minutes). The service has no fixed limit per client and asks applications to cache; it
throttles one that asks the same question every few seconds.

## In the API

`POST /api/match` returns `combined_matches` next to `driver_matches`: each is the drive, as a
match (`pickup`, `dropoff`, `ride_km`, the prices of the part ridden, `bookable`), with:

- `shape`: `ride_then_transit`, `transit_then_ride` or `transit_ride_transit`;
- `transit`: `{from_station, to_station, connection, fare}`, the connection as in `transit_option`;
- `change`: `{name, lat, lon}`, the stop where the passenger changes;
- for `transit_ride_transit`, instead of those two: `transit_before` and `transit_after` (each as
  `transit`), `change_before` and `change_after` (the stops where the passenger gets in and out);
- `starts`, `arrives`, `total_min`: the whole trip, from the passenger's start to their
  destination, the walk to or from the car included;
- `walk_min` on `pickup` (a ride first) or `dropoff` (a ride last): that walk in minutes, `null`
  when none is counted;
- `vs_transit_min`: minutes longer (negative: shorter) than public transport alone, when known;
- `transit_alone_fare`: what the ticket for public transport alone costs (`transit_option`'s
  `fare`), when known.

As for matches, who drives is left out for someone who isn't signed in.

The page asks for them on their own, after the drives: `POST /api/match/combined`
([The passenger page](passenger-page.md), "In steps").

## On the page

Under the matches, "A ride and public transport": a card per trip. Its first line is the whole
trip, as on a drive's card ([The passenger page](passenger-page.md)): when it starts and ends, how
long it takes, and what it costs, the ride's seat and the tickets of its public transport legs
together (2nd class, full fare, "≈" when a fare is an estimate). Under it, to compare: what the
train alone costs for the whole trip ("train alone ≈ CHF 75.00", the search's
`transit_alone_fare`); when that isn't known, the two amounts the price is made of. Without a
ticket's price it is the ride's alone, "without the train ticket". Then how the trip goes, in
order, one part a line (two, or three for a ride between two public transport legs), each after
the sign of what the passenger rides, in a circle, the signs joined by a line: who drives the
passenger, in words ("Judith drives you to Zürich"; after public transport, "Judith drives you on
from there"; signed out, "A driver takes you to …"), and "Public transport to Sévaz". Stops go by
their towns there. Then how much quicker than public transport alone it is, when it is. A drop-off
or a pickup more than 1 km from the passenger's own end of the trip is said on the card: with the
walk the trip's times count ("20 min on foot to the pickup (1.3 km)"), or, too far to walk, the
distance alone. Its details give the same steps once more, with what the card leaves out: their
times and the stops'
full names; under the ride, how far the car passes and the seat's price; under a connection, how
long it takes, its lines, its ticket's price and the link to it. The walk is a step of its own,
before the car of a ride first and after the car of a ride last: when to leave (or when the
passenger is there), about how long it takes, how far the car passes, and a link to the way on
Google Maps. Then the drive's own route.
"Request a seat" asks for the car's part: from the
passenger's start to the stop, from the stop to their destination, or from one stop to the other
(`ride_from`, `ride_to`), a booking like any other ([Booking](booking.md)). Without a drive for
the whole way, the section's title is all the page says about it.
