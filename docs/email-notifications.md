# Notifications by email

What lands in a user's inbox is also sent to their email address, so they hear of a request, an
answer or a cancellation without opening the app. [backend/notify_email.py](../backend/notify_email.py)
has the rules; in short:

- **What is emailed:** every notification of the inbox: a passenger's request for a driver, a
  drive posted for a passenger's request, a drive cancelled, every step of a
  [booking](booking.md) (a driver accepting a request included), and the question how a ride went
  ([ratings](ratings-and-reports.md)). A kind added later gets a general "there is news for you"
  until it has its own sentence.
- **The email** says what happened in one sentence, with the trip and its time, links to the
  inbox, and ends with a link to turn these emails off. It is in the user's language (the one of
  their last sign-in), from the `email.notify.*` keys of the [dictionaries](translations.md).
- **When:** not when the notification is made, so no request waits for an email to leave. Every
  minute the app goes through the notifications whose email is still to decide
  (`send_pending()`, started by `main.py`).
- **Quiet hours:** between 22:00 and 07:00 Swiss time, what can wait is held until the morning: a
  request for a driver, a drive posted for a request, the question how a ride went. News about a booking or a cancelled drive
  goes out at any hour, because someone is counting on that ride.
- **Not sent** when the user has read the notification in the app meanwhile, turned these emails
  off, is banned or has no email address (demo drivers); nor for notifications older than 24
  hours. A failing email is tried three times, then given up on, without holding the others up.
- **Turning them off:** a switch in the inbox (*Also send me my notifications by email*, on by
  default), or the link at the bottom of each email, which works without signing in: it opens a
  page that asks, and its button turns the emails off (so a mail scanner that opens every link
  changes nothing). The link carries a random token kept with the user.
- **The same limits as the inbox:** emails follow notifications, so a driver gets at most one
  request a day and three a week by email too ([why](ride-predictions.md#how-often)).

**Switches:** emails use the same sender as the sign-in link ([Sign-in and accounts](sign-in.md)),
so they need `EMAIL_FROM` and `PUBLIC_BASE_URL` with Amazon SES, and nothing is sent without them.
`EMAIL_NOTIFICATIONS=off` (the optional GitHub repo variable of that name, then a deploy) stops
them all; unset or `on`, they are sent. Notifications made while they were off are not sent after
the fact, nor are those from before this feature existed.

Not there: a weekly digest, and push notifications.

## Endpoints

- `GET  /api/notifications` and `PUT /api/notification_settings` (see [API](api.md)) carry
  `notify_email`, the user's switch; the PUT takes it too
- `GET  /unsubscribe/{token}` — public; a page that asks whether to turn the emails off, in the
  user's language. `POST /unsubscribe/{token}` — public; turns them off and says so. Both answer
  the same for an unknown token
