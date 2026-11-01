# Ride predictions and ride requests — design

**Status: ride requests and notifications are built (2026-11-01, `backend/notify.py`); ride
predictions are designed, not built yet.** Keep this document in step with the code: if a threshold or
limit changes, update it in the same commit.

## What it does

Today, Ride and Rail only has rides when drivers remember to post them, and a passenger who finds no
match leaves with nothing. This feature uses each person's own history to fill both gaps:

1. **Ride predictions.** The app spots recurring trips (e.g. Lausanne → Genève on weekdays at 07:15)
   and suggests the next ones. A driver sees "Publish your next drives?", a passenger "Request your
   next rides?", each with the upcoming dates ticked and one button to confirm.
2. **Ride requests.** A passenger can ask for a ride that nobody has posted. Drivers who made a
   suitable drive in the past are told about it. Only a few drivers are told, and rarely, because
   drivers who get too many notifications leave.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Publishing predictions | **Never automatic:** the user confirms every drive or request | A predicted drive that doesn't happen breaks a passenger's plans; a wrong request wastes drivers' attention. One tap to confirm keeps most of the convenience. |
| Suggestions and notifications | Suggestions only appear on the user's own page; they **never send a notification** | Notifications are kept for things another person is waiting on. Guesses about the user's own habits can wait until they visit. |
| Which requests reach drivers | Only requests the passenger made or confirmed | Same reason: drivers only hear about real needs. |
| Channel | An in-app inbox for now, behind a channel interface | There are no email addresses yet (identity is a typed name). Email sign-in is on the "Before real drivers" list; email notifications plug in then, with no change to the throttling. |
| History used | The last 13 weeks, within the existing 30-day retention for visitors | A weekly habit shows up after 3 weeks, so 30 days is enough to start. Keeping more history for real users is a separate privacy decision. |
| Demo | 3 months of fictional history | So investors and testers can see suggestions and notifications without building up weeks of their own history. |

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
   - has a route passing within the passenger's pickup and drop-off radii, in that order
     (`matching.match_drive`, the same rule as a search).
2. **Excluded:**
   - the passenger themself;
   - drivers who already posted a matching drive, because the passenger sees that drive already;
   - drivers who muted this route (a request whose start and end are each within `MUTE_RADIUS_KM`,
     5 km, of the muted request's) or turned request notifications off.
3. **Ranking:** drivers who made the drive most often come first, then a bonus for the same weekday
   (`SAME_WEEKDAY_BONUS`, worth 4 occurrences) and a closer time, minus the distance to the pickup
   and from the drop-off (the match's score, in minutes, over `RANK_MINUTES_PER_POINT` = 10). A
   habitual driver is the most likely to say yes. **Visitors' drivers come before demo drivers**:
   the demo network's thousands of fictional drivers, with weeks of history each, would otherwise
   always take the places, and real testers would never be told.

### How often

| Limit | Value | Why |
|---|---|---|
| Drivers per request | **3** (`DRIVERS_PER_REQUEST`) | Enough for a good chance of an offer, without pinging everyone on a busy corridor. |
| Per driver | **1 per day, 3 per rolling 7 days** | Nobody hears from us every day. If a driver is over budget, the next candidate is tried; nothing waits in a queue. |
| Merging | An unread notification for the same route and date becomes "2 passengers…" and doesn't count against the budget | Several passengers on one trip is better news, not more noise. |
| Back-off | After **3** notifications in a row without a reaction, the budget drops to 1 per week. After **5**, notifications pause for **30 days**, and the inbox says so with a "turn back on" button. | Silence is the clearest sign that we're bothering someone. Any reaction resets the count. |
| Extra round | Once only: after **12 h** without an offer, if the trip is still more than 12 h away, up to **2** more drivers | Gives the request a second chance without turning into a broadcast. |

Each driver notification offers three choices:
- **Offer this drive** fills in the Driver form with the driver's usual start and end and the
  requested time. The driver still confirms.
- **Not this time.**
- **Not interested in this route**, which mutes it.

### Telling passengers

When a drive is posted (manually, from a suggestion, or as an offer), open requests it fits are
checked with the same rules as a search, and their passengers are told. A passenger gets at most one
notification per request per day; several drives are merged into one.

### Delivery and email later

A notification is first saved to the database (`notifications` table), then delivered through each
channel in `CHANNELS`. Today the only channel is the inbox, which just shows the stored rows. Email
will be an `EmailChannel` with quiet hours (no email at night) and a weekly digest, on top of the
same limits.

## Data

- `open_requests`: the passenger, start and end, time, pickup/drop-off radii and time tolerance (the
  same as a search), status (`open` / `cancelled`; past ones have expired), whether the extra round
  ran. With predictions: whether it came from a suggestion, `is_demo`.
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
it's the same every time). Storing it would have meant ~470,000 past rows for 3 months of the
~36,000-a-week network.

To show the feature:
- **Showcase drivers** (Jean Dupont and a few others): their drives are posted only 2 days ahead, so
  signing in as one of them shows "Publish your next drives?" for the rest of the week.
- **Demo passengers** with recurring searches see "Request your next rides?".
- A couple of **demo ride requests** a day reach the showcase drivers' inboxes, through the same
  rules and limits as real ones. (Not built yet. Meanwhile, signing in as a demo driver, e.g. Jean
  Dupont, shows the requests real testers sent them.)

## Privacy

The privacy notice says (ride requests) and must say (predictions), in all four languages:
- that past drives and searches are used to suggest the next ones and to tell drivers about requests
  on routes they often drive, and that both can be turned off;
- that notified drivers see the passenger's name, trip and time.

## Open questions

- **Prediction history for real users:** keep 30 days, or longer once there are accounts?
- **Email notifications:** they arrive with email sign-in. Decide the digest day and the quiet hours
  then.
