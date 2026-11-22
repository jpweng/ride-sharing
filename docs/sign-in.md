# Sign-in and accounts

[backend/auth.py](../backend/auth.py) has the details; in short (signing in with Google or Apple
instead of the link is in [Sign-in with Google or Apple](social-login.md)):

- **Sign-in link**: the user enters their email address; the server emails a link with a random
  token, stored only as its SHA-256, valid once for 15 minutes. The link opens `#/signin/<token>`;
  the page then POSTs the token (so it never appears in server logs, and mail scanners that open
  links can't use it up), and the server starts a session. The answer to "send me a link" is the same
  whether the address has an account or not.
- **The code in the same email**: the email also carries a 6-digit code, which the page offers to
  take once a link was asked for (`frontend/signin_code.js`). It is for where the link can't be
  opened: the app installed on an iPhone keeps its own cookies, apart from Safari, where the link
  opens, so the link signs Safari in and never the app; and a computer isn't signed in by a link
  opened on a phone. The code signs in the page it is typed in. A link and its code are one
  sign-in: using either uses up both, and the code lasts as long as the link. Only its SHA-256
  (with the address) is stored. A code is short, so guesses are limited: after 5 wrong codes for
  an address (`CODE_ATTEMPTS`), the codes of the links it was sent so far stop working, and the
  links themselves still do. With the limits on links per address below, that is at most 50
  guesses a day for one address, each with one chance in a million.
- **First sign-in**: first and last name, "I am 18 or older" and the
  [code of conduct](../frontend/conduct.html) (a draft for a lawyer to review), whose acceptance is
  stored with its date and version (`CONDUCT_VERSION`; changing it asks everyone to accept the new
  one). Other users see the first name and last initial ("Marie M.", "Marie M. 2" if taken); it
  can't be changed later, as drives and requests store it.
- **Sessions**: a random token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie named
  `__Host-session`, stored as its hash in `sessions`; 90 days from the last visit (extended at most
  once a day), until sign-out, a ban or the account's deletion. `COOKIE_SECURE=0` drops `Secure`, only
  for testing over plain HTTP on another host than `localhost`.
- **Cross-site request forgery**: browsers don't send a `SameSite=Lax` cookie with cross-site
  POST/PUT/DELETE requests, and on top of that the app refuses those requests to `/api/` unless their
  `Origin` (or, without it, `Sec-Fetch-Site`) says they come from the site itself. Requests with
  neither header don't come from a browser, so they carry no victim's cookie. No CSRF tokens are
  needed this way, and the frontend stays plain `fetch` calls.
- **Who may do what**: searching is public: the passenger page works without an account, and shows
  the rides with their prices and the drivers' ratings. Who drives is for signed-in users: without
  a session (or before the profile is complete) `/api/match`, `/api/drives` and `/api/drives/{id}`
  answer with `driver_name` and `driver_id` set to `null` (`api.without_driver`), and profiles need
  a session anyway. Asking for a seat and asking drivers lead to the sign-in page (`#/signin`); the
  search stays in the tab, which notices the sign-in made in another tab (the emailed link's) when
  it is looked at again, and the link's own tab takes the trip back from the browser (see
  [The passenger page](passenger-page.md)). A signed-in user's searches are kept for the places their fields offer. Posting, cancelling and reading one's own drives, searches,
  requests and inbox need a signed-in user with a complete profile, and use only the session's
  user: the API takes no names.
- **View as a demo driver**: a read-only session (1 day) as one of the fictional drivers, without
  email, for investors and testers. It can read that driver's pages but not post, cancel, answer
  notifications or change settings, and its inbox leaves out real passengers' requests. Demo drivers
  have no email address, so they never receive email.
- **Limits**: per IP, 10 links per 15 minutes and 50 a day (then 429); per address, 3 per
  15 minutes and 10 a day (then no email is sent, with the same answer). Caddy adds its own per-IP
  limit (see [Deploying to AWS](deploying-to-aws.md)).
- **Account deletion** (the account page, reached by clicking one's name): deletes the account and
  everything about it at once. Passengers who were told about the user's upcoming drives get a
  "drive cancelled" notification; drivers' notifications about the user's requests disappear.
- **Bans**: `users.banned_at`, set with `docker compose exec app python backend/admin.py ban
  <email>` (or `unban`). It ends the account's sessions; no link is sent and earlier links are
  refused.
- **Users from before sign-in** (a typed name, no email) can't sign in. Their drives, searches and
  requests expire under the
  [30-day retention](demo-drives-and-data-retention.md), then their names are deleted. Nothing is
  migrated to accounts.

Email ([backend/mailer.py](../backend/mailer.py)) goes through a backend chosen by `EMAIL_BACKEND`:
`console` (the default) logs the email, link included, instead of sending it, so in development
you copy the link from the server's output; `ses` sends through Amazon SES in `SES_REGION`
(default `eu-north-1`) from `EMAIL_FROM`. `PUBLIC_BASE_URL` (default `http://localhost:8000`) is the
site's address for the links; it's never taken from the request. The email is in the language the
page was in, from the `email.*` keys of the dictionaries.

## Endpoints

Access levels and error codes as in [API](api.md).

- `POST /api/auth/link` `{email, language}` — public; emails a sign-in link. Always `{"ok": true}`
  (429 `too_many_requests` over the per-IP limit, 503 `email_unavailable` if sending fails)
- `POST /api/auth/code` `{email, code}` — public; uses the code of a sign-in email sent to that
  address, as `verify` does with the link's token (400 `invalid_code`: wrong, expired, used, or
  too many wrong ones; 403 `account_banned`)
- `POST /api/auth/verify` `{token}` — public; uses the link's token, sets the session cookie,
  returns `{user}` (400 `invalid_link`, 403 `account_banned`)
- `GET  /api/auth/me` — public; `{user}` or `{user: null}`: name, email, `is_demo`,
  `profile_complete`, `needs_conduct`
- `POST /api/auth/profile` `{first_name, last_name, adult, accept_conduct}` — a session, profile
  complete or not; the name only counts the first time
- `POST /api/auth/sign_out` — ends the session, clears the cookie
- `GET  /api/auth/demo_drivers`, `POST /api/auth/demo` `{driver_name}` — public; the demo drivers
  offered, and a read-only session as one of them
- `DELETE /api/account` — a session (not a demo-driver view); deletes the account and its data
