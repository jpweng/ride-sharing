# Ride requests on the driver's map

A driver told about a passenger's ride request (a `request_for_driver` notification, see
`backend/notify.py`) can click its card in the inbox to see the request on the map: the passenger's
start and destination, and, when the driver was chosen because they usually make a trip that fits,
their usual route with the pickup and drop-off points on it, labelled with their times. For a
driver chosen by area, only the passenger's trip is shown. The browser code is
`frontend/request_preview.js`.

## GET /api/notifications/{id}/preview

Read-only. Signed in with a complete profile (`Member`); only the notification's recipient.

Response:

- `request`: `origin_name`, `origin_lat`, `origin_lon`, `destination_name`, `destination_lat`,
  `destination_lon`, `requested_time`.
- `usual_trip` (only for a driver chosen for a trip they usually make): `origin` and `destination`
  (`name`, `lat`, `lon`), `route` (`[[lat, lon], ...]`, the road route thinned as for matches), `pickup`
  and `dropoff` (as in a match: `lat`, `lon`, `time`, ...) and `suggested_departure`.

The route is the road route between the usual trip's ends from the routing service; when that can't
be reached it is a straight line (`matching.route_with_fallback`).

Errors (the code's text is in `errors.<code>`):

| Status | Code | When |
|---|---|---|
| 401 | `not_signed_in` | no session |
| 404 | `notification_not_found` | no such notification, not the caller's, or not a request for a driver |
| 404 | `request_not_open` | the request was taken, cancelled or has expired |
