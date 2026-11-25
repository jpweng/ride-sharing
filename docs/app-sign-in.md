# Sign-in with Google or Apple from the iOS app

The [iOS app](../mobile/ios/) shows the site in a web view. The email sign-in works there as
anywhere (the code of the email is typed in the app, see [Sign-in and accounts](sign-in.md)), but
[Google and Apple](social-login.md) don't: Google refuses to sign people in inside an app's web
view, and Apple's own sheet (Face ID) only appears in a browser.

So the app opens the page's *Continue with …* link in the system's sign-in sheet
(`ASWebAuthenticationSession`): a browser over the app, with Safari's cookies, not the app's. A
session started there would be the sheet's. The server therefore hands the app a one-time code
instead, as native apps do with OAuth ([RFC 8252](https://www.rfc-editor.org/rfc/rfc8252), with the
proof key of [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)).
[backend/app_sign_in.py](../backend/app_sign_in.py) has the details; in short:

1. The app makes a random `verifier` and opens the sheet on
   `/auth/<provider>/start?lang=…&app=ios&app_challenge=<SHA-256 of the verifier, base64url>`. The
   challenge is kept with the attempt, in its cookie; the provider is told nothing about the app.
2. The person signs in at the provider, which sends the sheet back to `/auth/<provider>/callback`
   as in a browser. The server checks the answer and finds or creates the account exactly as
   described in [Sign-in with Google or Apple](social-login.md), but starts no session: it stores
   a code for that account and redirects the sheet to `ch.rideandrail.app://signin?code=…&lang=…`.
   That address closes the sheet and hands it to the app. When the sign-in fails, the address is
   `ch.rideandrail.app://signin?error=<code>&lang=…`, with the error codes of the browser flow.
3. The app's web view posts the code and the verifier to `/auth/app/finish`, which starts the
   session in the web view and redirects it to the site, as the provider's callback does in a
   browser. A first sign-in still goes through the profile page.

What protects it:

- **A code works once**, for two minutes, and is deleted when it is used, whether or not it
  worked. Only its SHA-256 is stored (table `app_sign_ins`), with the account, the challenge and
  the language. Codes nobody used are deleted by the hourly upkeep and by the next sign-in from
  the app; deleting the account deletes its codes.
- **A code is useless without the verifier** its challenge was made from, which never leaves the
  app until step 3. Another app that answers to the same kind of address and gets hold of the
  code can't use it.
- The sheet's own session, if the person is signed in to the site in Safari, is neither used nor
  changed.

The same is left open as with the emailed link: somebody who gets a person to open a sign-in of
their own making signs that person in to *their* account, not the other way round.

Nothing changes for browsers or for the Android app (which is Chrome): without `app=ios`, a
sign-in ends on the site with a session, as before.

## Endpoints

- `GET  /auth/{provider}/start?lang=…&app=ios&app_challenge=…` — public; as without `app`, and
  remembers the challenge. When `app` isn't `ios` or the challenge isn't 43 base64url characters,
  it redirects to `ch.rideandrail.app://signin?error=social_failed` without going to the provider.
- `GET  /auth/google/callback`, `POST /auth/apple/callback` — for an attempt started with `app`:
  303 to `ch.rideandrail.app://signin?code=…` or `…?error=…`, no session.
- `POST /auth/app/finish` — public; form fields `code`, `verifier` and optionally `lang` (only
  used for the page shown when the code is refused). 303 to `/?lang=…#/ride` with the session
  cookie, or to `/?signin_error=social_failed` (unknown, used or expired code, wrong verifier,
  account deleted meanwhile) or `account_banned`. Outside `/api/`, like the provider's callback:
  the web view opens it as a page.
