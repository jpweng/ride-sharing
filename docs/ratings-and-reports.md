# Ratings and reports

After a ride, the passenger and the driver rate each other, and either can report a problem to
the team. [backend/ratings.py](../backend/ratings.py) and [backend/reports.py](../backend/reports.py)
have the rules; in short:

## Ratings

- **Who rates whom:** the two sides of an accepted [booking](booking.md), each once, 1 to 5 stars:
  the passenger rates the driver, the driver the passenger. No comment: words about a problem go
  in a report.
- **When:** once the ride should be over (the booking's drop-off time). The hourly maintenance
  run then puts "How was your ride with …?" in both inboxes (`ratings.ask_after_rides()`, the
  `rate_ride` kind), with the five stars to click. A ride can be rated until its booking is
  deleted with the drive, 30 days after the departure.
- **What is shown:** a person's average and number of ratings, separately as a driver and as a
  passenger: next to the driver on search results and on the passenger's
  bookings (`driver_rating`), and next to the passenger on the driver's requests and passengers
  (`passenger_rating`): `{average, count}`, or nothing without ratings yet. Nobody sees who gave
  which rating.
- **How long:** ratings outlive the booking and the drive. Those a person received are deleted
  with their account; those they gave stay, without their author.

## Reports

- **Who:** the two sides of a booking, about the other one, once per booking, whatever its status
  (a driver who cancels at the last minute can be reported too): *Report a problem* on a booking
  and on the inbox's request to rate.
- **What:** a reason (didn't show up, very late, unsafe driving, inappropriate behaviour,
  something else) and, optionally, what happened in their words.
- **Who sees it:** only the team. The reported person isn't told, and nothing changes for them
  automatically: what to do is the team's decision.
- **What is kept:** who reported whom, the reason, their words, and the ride as it was (the route,
  the times, both names, the booking's status), since the booking and the drive are deleted later.
  Reports stay when the accounts go, with the names they were filed with.
- **Reaching the team:** each report is logged (`Report #… filed`), and emailed to `REPORTS_EMAIL`
  when that is set (the optional GitHub repo variable of that name, then a deploy). Without it,
  reports are only seen by listing them.
- **Handling them**, on the server (see [Deploying to AWS](deploying-to-aws.md) for the shell):

  ```bash
  docker compose exec app python backend/admin.py reports                      # the open ones
  docker compose exec app python backend/admin.py close-report 12 "warned"     # handled
  docker compose exec app python backend/admin.py ban someone@example.ch       # if it comes to that
  ```

Not there: consequences that follow automatically from ratings, late cancellations or no-shows,
and comments with ratings.

In the browser it is [frontend/ratings.js](../frontend/ratings.js), which adds to the cards of
drives, of bookings (`BOOKING_EXTRAS`, offered by `bookings.js`) and of the inbox.

## Endpoints

Access levels and error codes as in [API](api.md).

- `POST /api/bookings/{id}/rating` `{stars}` — may change, the booking's passenger or driver;
  rates the other side. 400 `not_rateable` (the booking was never accepted), `ride_not_over`,
  `already_rated`; 404 `booking_not_found`
- `POST /api/bookings/{id}/report` `{reason, details}` — may change, the booking's passenger or
  driver; `reason` is one of `no_show`, `late`, `unsafe`, `behaviour`, `other`; `details` at most
  1000 characters. 400 `already_reported`; 404 `booking_not_found`

Drives and matches carry `driver_rating`; bookings carry `passenger_rating`, and their `drive`
its `driver_rating`.
