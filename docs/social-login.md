# Sign-in with Google or Apple

Next to the [email link](sign-in.md), the sign-in page can offer *Continue with Google* and
*Continue with Apple*. [backend/social.py](../backend/social.py) has the details; in short:

- **Switched on by its settings.** A provider is offered only once all of its settings are set
  (below). Without them the sign-in page doesn't show its button and its addresses answer 404, so
  this is safe to deploy before any key exists.
- **The flow** is OpenID Connect's authorization code flow, run by the server: the browser is sent
  to the provider with a random `state` and `nonce` (and a PKCE challenge for Google), kept in a
  10-minute cookie in that browser; the provider sends it back with a code; the server exchanges
  the code for an ID token at the provider and checks its signature (the provider's published
  keys), issuer, audience, expiry and nonce. Nothing of the provider's tokens is kept.
- **Which account:** the one already linked to that person at that provider (their `sub`); else
  the one with the same email address, which is then linked; else a new one. The address must be
  verified by the provider, which then vouches for it as our own sign-in link would. So someone
  who signed in by email link before lands in the same account, and Google and Apple with the same
  address are one account. With Apple's *Hide My Email*, the address is a relay address of Apple's:
  a separate account from one made with the real address.
- **First sign-in** still goes through the profile page: name, 18 or older, code of conduct. The
  name the provider gave is filled in as a suggestion (`suggested_name` in `GET /api/auth/me`),
  for the user to check: it can't be changed afterwards.
- **After that** it's the same session, cookie and rules as with the email link, bans included.
  Deleting the account deletes its links to the providers (`user_identities`).
- **When it fails** (the person said no, an address that isn't verified, an answer that doesn't
  check out) the browser comes back to the sign-in page with the reason, and the email link is
  still there. The reason for the log is in the server's output.

In the browser it is [frontend/social.js](../frontend/social.js): the two buttons are plain links
to `/auth/<provider>/start`.

## Setting it up

Both need `PUBLIC_BASE_URL` (already set in production): the provider sends the browser back to
`<PUBLIC_BASE_URL>/auth/<provider>/callback`, which must be registered with it exactly.

**Google** ([Google Cloud console](https://console.cloud.google.com/), *APIs & Services*):

1. Configure the OAuth consent screen (external; the app's name, a support address, the site's
   domain; scopes `openid`, `email`, `profile`), and publish it.
2. Create an *OAuth client ID* of type *Web application*, with the authorised redirect URI
   `https://rideandrail.ch/auth/google/callback`.
3. Its client ID goes in the GitHub repo **variable** `GOOGLE_CLIENT_ID`, its client secret in the
   repo **secret** `GOOGLE_CLIENT_SECRET`.

**Apple** ([Apple Developer](https://developer.apple.com/account/), *Certificates, Identifiers &
Profiles*; needs the paid developer membership):

1. An *App ID* with *Sign in with Apple* enabled, then a *Services ID* (e.g.
   `ch.rideandrail.signin`) with *Sign in with Apple* configured: the domain `rideandrail.ch` and
   the return URL `https://rideandrail.ch/auth/apple/callback`.
2. A *Key* with *Sign in with Apple* enabled: download the `.p8` file (once only) and note its
   key ID.
3. GitHub repo **variables**: `APPLE_CLIENT_ID` (the Services ID), `APPLE_TEAM_ID` (the team ID,
   top right of the developer account), `APPLE_KEY_ID`. Repo **secret** `APPLE_PRIVATE_KEY`: the
   key file base64-encoded on one line (`base64 -w0 AuthKey_XXXXXXXXXX.p8`).

Then run the *Test and deploy* workflow (or merge anything): the deploy writes the settings to
the server's `.env`, and the buttons appear. The two secrets travel like the other settings, in
the deploy command sent to the server, so they are visible in the AWS account's Systems Manager
command history to whoever can read it.

Locally: `GOOGLE_CLIENT_ID=… GOOGLE_CLIENT_SECRET=… uv run uvicorn …` with
`http://localhost:8000/auth/google/callback` registered as a redirect URI. Apple needs HTTPS and a
real domain, so it can't be tried on localhost.

Before publishing an iOS app that offers these buttons, check Apple's current rules for the
*Sign in with Apple* button's look, and for revoking its tokens when an account is deleted (not
done here).

## Endpoints

- `GET  /api/auth/providers` — public; `{providers}`: those offered, of `google` and `apple`
- `GET  /auth/{provider}/start?lang=…` — public; sets the attempt's cookie and redirects to the
  provider (404 `provider_not_found` when it isn't set up)
- `GET  /auth/google/callback`, `POST /auth/apple/callback` — where the provider sends the browser
  back; starts the session and redirects to the app, or to `/?signin_error=<code>` with
  `social_cancelled`, `social_email_unverified`, `social_failed` or `account_banned`

Started by the iOS app (`app=ios`), a sign-in ends in the app instead of the browser that went
to the provider: see [Sign-in with Google or Apple from the iOS app](app-sign-in.md).

The two `/auth/` addresses are outside `/api/` on purpose: Apple's answer is a form posted from
its own site, which the cross-site check on `/api/` would refuse. The `state` in the cookie is
what protects them; that cookie is `SameSite=None` because a `Lax` one wouldn't come with Apple's
form.
