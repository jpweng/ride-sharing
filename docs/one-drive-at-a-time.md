# One drive at a time

A driver is in one place at a time. A drive isn't posted when its driver has another one at the
same time, or one so close to it that they couldn't get from where the earlier ends to where the
later starts. [backend/drive_conflicts.py](../backend/drive_conflicts.py) checks it wherever a
drive is posted: `POST /api/drives`, and a driver accepting a passenger's ride request
(`POST /api/requests/{id}/accept`, which posts a drive, see [Booking](booking.md)).

## The rule

A drive lasts from its departure for as long as its road route takes.

- **Not at the same time:** refused when another of the driver's drives is on the road at any
  moment of it. Posting the same drive twice is that too.
- **Time to get there:** between two drives there must be at least the time the road takes from
  where the earlier one ends to where the later one starts. Bern → Lausanne at 13:00 arrives
  around 14:20: a drive from Zürich at 15:00 is refused, one at 17:30 is not. A drive that starts
  where the one before ends (within a km) can leave as soon as that one has arrived.
- **Where the driver is, is what counts:** only the drive that ends last before the new one and
  the one that starts first after it are looked at for the way between.

Times are compared to the minute. Drives posted before this rule stay as they are, and a
cancelled drive is no longer in the way.

## The way between two drives

A road route like a drive's own (`matching.route_with_fallback`: the routing server, or the
straight line at 60 km/h without it). It is asked for only when the time could be too short: with
enough time for the straight line at 20 km/h, there is time, and the other drives looked at are
those leaving from 24 hours before the new one to 12 hours after it arrives.

## In the API

Both endpoints answer **409** with the code `drive_overlaps` or `drive_too_close`, and in
`detail.params` the drive in the way: `from`, `to` (its two places), `time` (its departure,
`HH:MM`), `drive_id`, and for `drive_too_close` `duration`, the way between the two as the page
writes durations ("2 h 15"). Nothing is posted; a ride request that couldn't be accepted for this
stays open for other drivers.

The page shows the code's sentence with those names in it, where it shows any error of the form:
"You already have a drive at that time: Bern → Lausanne, leaving at 13:00." The API's errors could
not name anything before: `api_error()` now takes `params`, which `fetchJson` gives to the
translation.

Two of a driver's drives are never checked at once (`one_at_a_time`): a button pressed twice
posts one drive.
