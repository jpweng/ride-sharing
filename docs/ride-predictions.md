# Ride predictions and ride requests — design

**Status: ride requests and notifications are built (2026-11-01, `backend/notify.py`), with demo
and real requests kept apart, and drivers also chosen by their [areas](driver-areas.md), since
2026-11-18; ride predictions are designed, not built yet.** Keep this document in step with the code: if a threshold or
limit changes, update it in the same commit.

## What it does

Today, Ride and Rail only has rides when drivers remember to post them, and a passenger who finds no
match leaves with nothing. This feature uses each person's own history to fill both gaps:

1. **Ride predictions.** The app spots recurring trips (e.g. Lausanne → Genève on weekdays at 07:15)
   and suggests the next ones. A driver sees "Publish your next drives?", a passenger "Request your
   next rides?", each with the upcoming dates ticked and one button to confirm.
2. **Ride requests.** A passenger can ask for a ride that nobody has posted. Drivers who made a
   suitable drive in the past are told about it, and so are drivers who saved an
   [area](driver-areas.md) it starts or ends in. Only a few drivers are told, and rarely, because
   drivers who get too many notifications leave.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Publishing predictions | **Never automatic:** the user confirms every drive or request | A predicted drive that doesn't happen breaks a passenger's plans; a wrong request wastes drivers' attention. One tap to confirm keeps most of the convenience. |
| Suggestions and notifications | Suggestions only appear on the user's own page; they **never send a notification** | Notifications are kept for things another person is waiting on. Guesses about the user's own habits can wait until they visit. |
| Which requests reach drivers | Only requests the passenger made or confirmed | Same reason: drivers only hear about real needs. |
| Channel | The in-app inbox, and the same by email | Every notification of the inbox is also emailed, with quiet hours at night and a switch per user ([Notifications by email](email-notifications.md)); the throttling is the inbox's, unchanged. |
| History used | The last 13 weeks, within the existing 30-day retention for visitors | A weekly habit shows up after 3 weeks, so 30 days is enough to start. Keeping more history for real users is a separate privacy decision. |
| Demo | 3 months of fictional history | So investors and testers can see suggestions and notifications without building up weeks of their own history. |
| Demo and real requests | **Never mixed:** a real passenger's request only reaches real drivers; a request made under a demo driver's name only reaches demo drivers | Nobody waits for a driver who doesn't exist, and no real driver acts on a fictional need. The demo still shows requests arriving (see [Demo data](#demo-data)). |

Vocabulary: a **search** is today's `ride_requests` row (every "Find a ride" click). A **ride
request** is the new, explicit ask (`open_requests`).

## Predictions

Predictions are computed from one user's past trips when their page is opened. A user's history is
small, so there's no background job. For a driver, the trips are their drives. For a passenger, they
are their searches, at most one per route per day because the raw table has many repeats, plus their
ride requests.

Rules (in `backend/predictions.py`):

| Rule | Value | Why |
|---|---|---|
| Same route | Start and end each within **1.5 km** | Enough for the same home and the same office or station, even when picked slightly differently. |
| Same time | Within **±45 min** of the pattern's time of day | Allows for a normal spread of departures without merging the morning and evening trips. |
| Habit | **≥ 3 occurrences** on that weekday | Twice can be a coincidence; three times is a habit. |
| Regularity | Present in **≥ 60 %** of the weeks since the first occurrence | Allows for holidays and sick days, but leaves out occasional trips. |
| Still alive | Last occurrence **≤ 21 days** ago | Stops suggesting a habit that has ended (new job, moved house). |
| Predicted time | Median of the occurrences, rounded to 5 min | The median ignores the odd late day. |
| Horizon | The next **7 days**, at least **2 h** ahead | One week matches how people plan; anything sooner is too late to be useful. |
| Already covered | Skip a day with a drive or request on that route within **±2 h** | Never suggest what the user already posted. |

The user can dismiss one date ("Not now") or the whole pattern ("Stop suggesting this"), or turn
suggestions off.

## Ride requests and notifications

A passenger posts a ride request for at least 1 h and at most 14 days ahead. Less than 1 h leaves no
driver time to react; beyond 14 days, a driver can't know their plans.

### Who is told

Rules (in `backend/notify.py`):

1. **Candidates:** drivers with a past drive in the last 13 weeks that:
   - leaves within **±60 min** of the requested time of day;
   - has a route passing near the passenger's start and destination, in that order
     (`matching.match_drive`, the same rule and the same 10 km as a search).
2. **Candidates by area**, after those: drivers who saved an area (`backend/areas.py`) the
   request starts or ends in, whether or not they ever made the trip. A new driver has no history,
   and a driver living at one end may well go that way. Drivers whose areas cover both ends (home
   at one, work at the other) come first, then the nearest to their area's centre; drivers who fit
   equally well (several who picked the same town) take turns, the one told longest ago first. A
   driver found both ways is told once, as one who made the trip.
3. **Real or demo, never both** (`notify.is_demo_request`):
   - a real passenger's request only considers visitors' drives, and not those posted under a demo
     driver's name. Demo drivers are never told, and the "N drivers were told" the passenger sees
     counts real drivers only. If none fits, the passenger is told that no driver using the app has
     made the trip recently (`ride.askSentNone`);
   - a request made under a demo driver's name (someone viewing the demo as, e.g., Jean Dupont) is
     a **demo request**: it only considers the demo drivers' history. See [Demo data](#demo-data).
   - With `DEMO_DRIVES=off` there are no demo drivers and no demo requests.
   - Demo drivers have no areas, so a demo request reaches nobody by area.
4. **Excluded:**
   - the passenger themself;
   - drivers who already posted a matching drive, because the passenger sees that drive already;
   - drivers who muted this route (a request whose start and end are each within `MUTE_RADIUS_KM`,
     5 km, of the muted request's) or turned request notifications off.
5. **Ranking of those who made the trip:** drivers who made the drive most often come first, then a bonus for the same weekday
   (`SAME_WEEKDAY_BONUS`, worth 4 occurrences) and a closer time, minus the distance to the pickup
   and from the drop-off (the match's score, in minutes, over `RANK_MINUTES_PER_POINT` = 10). A
   habitual driver is the most likely to say yes.

### How often

| Limit | Value | Why |
|---|---|---|
| Drivers per request | **3** who made the trip (`DRIVERS_PER_REQUEST`), plus **3** by area (`AREA_DRIVERS_PER_REQUEST`) | Enough for a good chance of an offer, without pinging everyone on a busy corridor or in a town. The two don't share a limit: a corridor with regulars still reaches drivers who live there. |
| Demo drivers per real request | **0**, in the first round and the extra round | A real passenger must never wait for a driver who doesn't exist. Demo requests reach up to the same 3 (then 2) demo drivers, with the same limits. |
| Per driver | **1 per day, 3 per rolling 7 days**, however they were chosen | Nobody hears from us every day. If a driver is over budget, the next candidate is tried; nothing waits in a queue. |
| Merging | An unread notification for the same route and date becomes "2 passengers…" and doesn't count against the budget | Several passengers on one trip is better news, not more noise. |
| Back-off | After **3** notifications in a row without a reaction, the budget drops to 1 per week. After **5**, notifications pause for **30 days**, and the inbox says so with a "turn back on" button. | Silence is the clearest sign that we're bothering someone. Any reaction resets the count. |
| Extra round | Once only: after **12 h** without an offer, if the trip is still more than 12 h away, up to **2** more drivers, of either kind | Gives the request a second chance without turning into a broadcast. |

Each driver notification offers four choices:
- **Accept this ride** posts the drive and books the passenger on it in one step; the first driver
  to accept gets it ([Booking](booking.md)).
- **Offer this drive** fills in the Driver form with the driver's usual start and end and the
  requested time; for a driver chosen by area, with the passenger's own trip and time, to adjust.
  The driver still confirms.
- **Not this time.**
- **Not interested in this route**, which mutes it.

### Telling passengers

When a drive is posted (manually, from a suggestion, or as an offer), open requests it fits are
checked with the same rules as a search, and their passengers are told. A passenger gets at most one
notification per request per day; several drives are merged into one.

### Delivery

A notification is saved to the database (`notifications` table); the inbox shows the stored rows.
Each is then also sent by email, within a minute or, at night, in the morning, unless its
recipient turned emails off or read it meanwhile: see
[Notifications by email](email-notifications.md). A weekly digest isn't built.

## Data

- `open_requests`: the passenger, start and end, time, the search's limits when it was made
  (`pickup_radius_km`, `dropoff_radius_km`, `time_tolerance_min`: no longer the passenger's choice,
  see `backend/search.py`), status (`open` / `cancelled`; past ones have expired), whether the extra round
  ran. With predictions: whether it came from a suggestion. Whether a request is a demo request
  isn't stored: it follows from the passenger's name (`notify.is_demo_request`).
- `notifications`: recipient, kind (`request_for_driver` / `drive_for_passenger`), the request(s)
  and drive(s) it's about (merged ones in JSON lists), the driver's usual trip and suggested
  departure, created, read and acted-on times, and the action.
- `route_mutes` and `suggestion_dismissals`: the user's "not interested" and "stop suggesting"
  choices, deleted with the user.
- On `users`:
  - `notify_requests` (built), `suggestions_enabled` (with predictions): the user's settings;
  - `backoff_reset_at`: set by "turn back on"; unanswered notifications before it no longer count.
    The pause itself isn't stored: it's 30 days from the notification that made 5 in a row.
- `from_suggestion` on drives and requests, so we can measure how often suggestions are accepted.

Retention follows the existing 30-day rule:
- ride requests are deleted 30 days after their time;
- notifications 30 days after they're sent;
- users are only deleted once nothing references them.

## Demo data

Demo drivers' history isn't stored: it follows from their templates in `demo_drives.py`, each one
driven on its weekdays over the last 13 weeks, minus about 10 % of occurrences skipped (seeded, so
it's the same every time). Storing it would have meant ~1.7 million past rows for 3 months of the
~130,000-a-week network.

To show the feature:
- **Showcase drivers** (Jean Dupont and a few others): their drives are posted only 2 days ahead, so
  signing in as one of them shows "Publish your next drives?" for the rest of the week.
- **Demo passengers** with recurring searches see "Request your next rides?".
- A couple of **demo ride requests** a day reach the showcase drivers' inboxes, through the same
  rules and limits as real ones. (Not built yet. Until they exist, a demo driver's inbox is empty:
  real passengers' requests never reach demo drivers, see below.)
- **Demo requests by hand** (built on the server, but nothing in the app makes one for now): a
  request made under a demo driver's name only reaches demo drivers, through the same rules and
  limits, and the reply names the demo drivers told (their names are fictional; real drivers' names
  are never returned). It was made for viewing the demo as one demo driver (e.g. Jean Dupont) and
  asking for a ride on the Passenger page. Since sign-in by email, "view as a demo driver" is
  read-only and can't ask for a ride.

Why this way: requests from real passengers no longer reach demo drivers, so nobody waits for a
driver who doesn't exist, and the demo needs its own requests to show the driver's side. Making the
demo identity, not a flag the visitor sets, decide it keeps the rule in one place on the server. A
demo driver is recognised by name (`demo_drives.is_demo_user`); an account's name ("Marie M.") is
never a demo driver's. `users.is_demo`, which sign-in by email added, should replace it. On top of
this, "view as a demo driver" leaves out of the inbox any notification about a real user's request
(`notify.inbox`): their trips are nobody else's business.

Demo drives are **never bookable**: searches, the drive list and the inbox mark every drive with
`bookable` (`false` for demo drives), and they stay labelled "Demo". When a demo drive and a real
one fit a passenger equally well, the real one comes first.

**Switching demo drives off:** with `DEMO_DRIVES=off` in the app's environment, the next
maintenance run (at startup, then hourly) deletes every demo drive and the trips' stored routes
(`demo_routes`), and creates none. They're hidden from searches, the drive list, `GET
/api/drives/{id}` and the inbox right away, demo drivers' history is no longer used, and requests
under demo drivers' names are ordinary real requests. Notifications already sent to demo drivers
are left to the 30-day retention. Switching back on recreates the drives at the next run, but
fetches every trip's route from OSRM again: ~1,500 trips, a minute or so from our own OSRM server
(against the public one, one a second, the network comes back over about half an hour).

## Privacy

The privacy notice says (ride requests) and must say (predictions), in all four languages:
- that past drives and searches are used to suggest the next ones and to tell drivers about requests
  on routes they often drive, that drivers' areas are used the same way, and that both can be
  turned off;
- that notified drivers see the passenger's name, trip and time, and that drivers with an area
  around the start or destination see the same in the list of requests in their areas
  ([Driver areas](driver-areas.md)).

## Open questions

- **Prediction history for real users:** keep 30 days, or longer once there are accounts?
- **Email digest:** notifications are emailed one by one; whether a weekly digest is wanted too.
