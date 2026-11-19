# Booking

A passenger asks for seats on a posted drive, the driver accepts or declines, and either of them
can cancel afterwards. [backend/bookings.py](../backend/bookings.py) has the rules; in short:

- **Asking:** *Request a seat* on a search result or on a drive in the list, with the number of
  seats and an optional message to the driver. From a search, the booking keeps the passenger's own
  start and destination and the points where the drive's route passes nearest to them, with the
  times there (the same as the search showed); from the list, it's for the whole drive. A request
  holds no seat.
- **The driver's answer:** each of the driver's drives lists its requests and passengers, with
  *Accept* (and an optional message to the passenger: the car, where to wait) and *Decline*.
  Accepting takes the booking's seats from the drive, in one transaction that refuses to go below
  zero, so a drive is never overbooked. A drive with no free seat is no longer found by searches;
  requests still waiting on it stay, and can be accepted if a seat becomes free.
- **Cancelling:** the passenger can withdraw a request or cancel an accepted booking; the driver
  can cancel a booking they accepted. An accepted booking's seats are free again.
- **One booking per passenger and drive.** While it's requested or accepted, the passenger can't
  ask again. Once it's declined or cancelled, by either of them, they can: the new request
  replaces the old booking, and the driver is told again.
- **Where they meet:** the pickup point and time are shown to both, on the map too. The two
  messages are how they agree on the exact spot. No phone number or email address is exchanged.
- **Price:** nothing is charged; they settle between themselves. Each booking shows what the
  passenger pays per seat by the [pricing rules](../backend/pricing.py), with the passengers
  accepted so far: the price falls when more are accepted, for those already aboard too. A drive's
  `price_chf` is what the next passenger would pay.
- **Notifications:** each step lands in the other one's inbox: a request and a cancellation by the
  passenger in the driver's; the answer and a cancellation by the driver in the passenger's.
  No email is sent yet.
- **Demo drives can't be booked** (they're fictional), nor one's own drives, nor drives that have
  left.
- **When a drive is cancelled** (by its driver, or because they delete their account), the
  passengers with a request or a seat on it are told, and the bookings are deleted with the drive.
  A passenger deleting their account frees their seats, and their drivers are told. Like drives,
  bookings are deleted 30 days after the departure
  ([data retention](demo-drives-and-data-retention.md)); they leave both pages a day after it.

**Accepting a ride request directly.** The other way round, a driver shown a passenger's
[ride request](ride-predictions.md) (in their inbox, or in the list of requests in their
[areas](driver-areas.md)) can take it with *Accept this ride*: the drive is posted and the
passenger booked on it, accepted, in one step (`bookings.accept_request()`).

- **The first driver to accept gets it:** the request is closed in the same step, so a second
  driver is told it's no longer open, and nothing is posted for them. It leaves the list of
  requests, and other notified drivers' notifications say it's no longer open.
- **Which drive:** the passenger's own trip at their time. A driver who was told because they
  usually make a trip drives that trip instead, leaving so as to reach the passenger at their
  time, and picks them up on the way; that trip must serve the request as a search would find it.
- **Seats and price:** the driver says how many seats they offer in all (3 by default), the
  passenger's included, so others can ask to join; the price is the default suggestion for the
  drive. They can add a message for the passenger.
- **The passenger** is told who drives them and finds the booking under *Your bookings*; they can
  cancel it like any other, and the driver keeps the drive. The passengers of other open requests
  the drive fits are told, as for any posted drive.
- *Offer this drive* is still there, to change the trip or the time first; the passenger then asks
  for a seat as usual.

After the ride, both sides are asked to [rate it](ratings-and-reports.md), and either can report
a problem to the team at any time.

Not there yet: paying through the app, and what happens after late cancellations or no-shows.

A booking's statuses: `requested`, then `accepted` or `declined` by the driver;
`cancelled_by_passenger` or `cancelled_by_driver` afterwards. The inbox's kinds:
`booking_requested`, `booking_accepted`, `booking_declined`, `booking_cancelled` (its details say
`by` whom and what it `was`), `booking_drive_cancelled`, and `request_accepted` (a driver took the
passenger's ride request, whose status becomes `accepted`). They carry the drive and the
passenger's name in their `details`, as the booking itself may be gone.

In the browser, all of it is [frontend/bookings.js](../frontend/bookings.js), a script of its own
loaded after `app.js`. It adds to the cards of drives and to the inbox through two lists `app.js`
offers to such scripts: `CARD_EXTRAS` (functions called with each card of a drive: the driver's
own, a search result, the public list), `NOTIFICATION_CARDS` (kind of notification → what fills
its card) and `REQUEST_EXTRAS` (functions called with each card of a ride request shown to a
driver, for *Accept this ride*).

## Endpoints

Access levels and error codes as in [API](api.md).

- `POST /api/drives/{id}/bookings` `{origin, destination, seats, note}` — may change; asks for
  `seats` (1 by default) on the drive. `origin` and `destination` (`{name, lat, lon}`) are the
  passenger's own, both or neither (the whole drive); `note` is at most 300 characters. Returns the
  booking. 404 `drive_not_found`; 400 `not_bookable` (a demo drive), `own_drive`, `drive_departed`,
  `no_seats_left`, `already_requested` (a request waiting, or an accepted booking), `not_on_route` (the drive doesn't pass within 60 km of both
  ends, in that order)
- `GET  /api/bookings` — signed in; `{as_passenger, as_driver}`: one's own bookings, and the
  requests and accepted bookings on one's drives, the soonest drive first. Each has its `status`,
  `seats`, `passenger_name`, `origin_name`, `destination_name`, `pickup` and `dropoff`
  (`lat`, `lon`, `time`, `gap_km`), `ride_km`, the two notes, `price_chf` (per seat) and
  `total_chf`, and its `drive`
- `POST /api/bookings/{id}/accept` `{note}` — may change, the driver; 400 `booking_not_open` (not a
  request any more), `no_seats_left`, `drive_departed`
- `POST /api/bookings/{id}/decline` — may change, the driver; 400 `booking_not_open`
- `POST /api/bookings/{id}/cancel` — may change, the passenger (a request or an accepted booking)
  or the driver (an accepted booking); 400 `booking_not_open`

All three answer 404 `booking_not_found` for a booking the user has no part in.

- `POST /api/requests/{id}/accept` `{seats_available, note, price_chf, origin, destination,
  departure_time}` — may change; takes a passenger's open ride request: posts the drive and books
  the passenger on it. `seats_available` (3 by default) is what the driver offers in all;
  `price_chf` is optional (the default suggestion); `origin`, `destination` and `departure_time`
  are the driver's own trip, all three or none (the passenger's trip at their time). Returns the
  booking. 404 `request_not_found`; 400 `request_not_open` (taken, cancelled, past, or a demo
  request), `own_request`, `not_on_route` (the driver's own trip doesn't serve it),
  `price_too_high` / `price_too_low`, `drive_departed`

Every drive and match also carries `seats_available`, its free seats, and `seats_booked`, those
taken by accepted bookings.
