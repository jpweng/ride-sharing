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
- **Time to get there:** between two drives there must be the time the driver needs from where
  the earlier one ends to where the later one starts: the road between the two in usual traffic,
  and their breaks (below). Bern → Lausanne at 13:00 arrives around 14:20: a drive from Zürich at
  15:00 is refused, one in the evening is not.
- **Where the driver is, is what counts:** only the drive that ends last before the new one and
  the one that starts first after it are looked at for the way between.

Times are compared to the minute. Drives posted before this rule stay as they are, and a
cancelled drive is no longer in the way.

## The time needed between two drives

The way between them is a road route like a drive's own (`matching.route_with_fallback`: the
routing server, or the straight line at 60 km/h without it). A route's time is the empty road's;
the time needed (`needed_between`) adds what a real day adds:

- **Usual traffic:** driving takes 1.1 times as long as on the empty road, and 1.3 times at the
  peak hours, Monday to Friday from 06:30 to 09:00 and from 16:00 to 19:00 (`USUAL_TRAFFIC`,
  `PEAK_TRAFFIC`, `PEAK_HOURS`). Each part of the way counts at the pace of the hour it is driven
  in. The earlier drive itself is counted that way too: it arrives later than its route says, and
  that comes off the time between the two.
- **Breaks:** a quarter of an hour of rest for every two hours at the wheel, the earlier drive
  and the way between counted together (`BREAK_S`, `BREAK_EVERY_S`).

So after Bern → Lausanne at 13:00 on a Sunday (78 minutes by its route), a drive from Zürich, 2 h
15 of empty road away, needs 2 h 28 for the way, 8 minutes that traffic added to the first drive,
and a break for 3 h 54 at the wheel: 2 h 51 after 14:18. And a drive that starts where the one
before ends still needs a little time after it: 5 minutes after 51 minutes on a Sunday, 14 after
the same drive at 08:00 on a weekday, 52 after a drive of 3 h 44.

The road is asked for only when the time could be too short: with enough time for the straight
line at 10 km/h, there is time. The other drives looked at are those leaving from 24 hours before
the new one to 12 hours after it arrives.

## In the API

Both endpoints answer **409** with the code `drive_overlaps` or `drive_too_close`, and in
`detail.params` the drive in the way: `from`, `to` (its two places), `time` (its departure,
`HH:MM`), `drive_id`, and for `drive_too_close` `duration`, the time needed between the two as the
page writes durations ("2 h 51"). Nothing is posted; a ride request that couldn't be accepted for this
stays open for other drivers.

The page shows the code's sentence with those names in it, where it shows any error of the form:
"You already have a drive at that time: Bern → Lausanne, leaving at 13:00." The API's errors could
not name anything before: `api_error()` now takes `params`, which `fetchJson` gives to the
translation.

Two of a driver's drives are never checked at once (`one_at_a_time`): a button pressed twice
posts one drive.
