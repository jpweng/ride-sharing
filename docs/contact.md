# The contact page

[frontend/contact.html](../frontend/contact.html) is a form (name, email address, message) for
anyone who wants to reach the team: a question, an idea, a problem with a ride or an account, a
request about one's data. It is linked from the app's banner ("Contact"), from the
[privacy notice](../frontend/privacy.html) and from the
[code of conduct](../frontend/conduct.html), and needs no account: someone who can't sign in must
be able to say so.

The message is emailed to the team and kept nowhere on the site: no table, and the log only says
that a message of so many characters was sent. The logic is in
[backend/contact.py](../backend/contact.py), the routes in
[backend/routes/contact.py](../backend/routes/contact.py), the page's script in
[frontend/contact.js](../frontend/contact.js).

## The email

Sent through the same backend as every email ([backend/mailer.py](../backend/mailer.py): Amazon SES
in production, the log in development), in English whatever the visitor's language, with the
message as it was written.

| Header | Value | Why |
| --- | --- | --- |
| `From` | the site's sender, `EMAIL_FROM` (`Ride and Rail <no-reply@…>`) | The only address the server may send from, and the one SPF, DKIM and DMARC vouch for. Sending "from" the visitor's address would be rejected or land in spam. |
| `To` | `CONTACT_EMAIL` | The team's mailbox. |
| `Reply-To` | the visitor's address | Answering the email answers the visitor, not `no-reply`. |
| `Subject` | `[Ride and Rail contact] <name>` | A fixed start to filter and sort on. |

The body gives the visitor's name and address, the language of the page they wrote on, when it was
sent, and, when they are signed in with their own account, which account (address and user id), so
that a request about an account can be told to come from its owner.

Nothing a visitor types can add a header: the name loses its line breaks and control characters
before it goes into the subject, an address with a space or a line break in it is refused, and the
message is escaped in the HTML part.

## Limits

Anyone can make the site send an email with this form, so:

- **Per IP address:** 3 messages in 10 minutes and 10 a day; beyond, `429 too_many_requests`.
- **All visitors together:** 200 a day, which is what a mailbox and the sending quota can take.
- **A hidden field** (`website`), which people don't see and robots fill in: such a message is
  answered `ok` like any other and dropped.
- Requests from other sites are refused like every write to the API (`cross_site_request`).

The counts are in memory (the app is one process), kept for a day; a restart resets them. A
message that couldn't be sent isn't counted.

## Switching it on

The form is on when `CONTACT_EMAIL` holds an address. Without it, or with something that isn't an
address, the site works as before: the page says the form isn't available, and the API answers
`503 contact_unavailable`.

In production the address comes from the GitHub repo **secret** `CONTACT_EMAIL`:

```bash
gh secret set CONTACT_EMAIL   # asks for the value
```

It takes effect at the next deploy (push to `main` or re-run the workflow). It is a secret, not a
variable, because the repository is public: its deploy log shows the values of variables, and
masks those of secrets. The address is never shown to visitors.

With Amazon SES still in sandbox mode, the address must be a verified one
(`ses_sandbox_test_recipients`, see [Deploying to AWS](deploying-to-aws.md)).

The privacy notice names where the mailbox is hosted (the "Services the site uses" section); change
it there when the mailbox moves.

## Endpoints

- `GET  /api/contact` — public; `{"enabled": true|false}`, whether the form is switched on
- `POST /api/contact` — public; body `name` (up to 100 characters are kept), `email`, `message`
  (at most 4,000 characters), `language` (of the page, optional) and `website` (the hidden field);
  answers `{"ok": true}`. Errors:
  - `503 contact_unavailable`: the form is switched off
  - `400 contact_incomplete`: the name, the address or the message is missing
  - `400 invalid_email`: the address isn't one
  - `429 too_many_requests`: over one of the limits
  - `503 email_unavailable`: the email couldn't be sent
  - `403 cross_site_request`: the request doesn't come from the site's own pages

## Checks

[backend/tests/test_contact.py](../backend/tests/test_contact.py): off until the address is set,
the email and its headers (the `Reply-To` also against Amazon SES's own description of its API),
what is refused, the limits, the log, the page and the links to it.
