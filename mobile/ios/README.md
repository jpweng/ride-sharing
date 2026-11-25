# iOS app (the site in a web view)

The iOS app is the website itself, <https://rideandrail.ch>, shown full screen by a small native
app. There is no second copy of the app to maintain: every deploy of the site updates what the app
shows. A new version on the App Store is only needed when this folder changes.

Apple has no equivalent of the [Android app](../android/)'s Trusted Web Activity, so the shell is a
few Swift files around a `WKWebView`:

- [App/Site.swift](App/Site.swift): where the site is, which addresses are its own, where each
  address is opened, and the sign-in hand-off. Rules only, checked by
  [Tests/SiteTests.swift](Tests/SiteTests.swift).
- [App/WebViewController.swift](App/WebViewController.swift): the one screen. It does what
  `Site.route` says and stands in for what a browser provides around a page.
- [App/SignIn.swift](App/SignIn.swift): sign-in with Google or Apple in the system's sign-in sheet.
- [App/PageSheetController.swift](App/PageSheetController.swift): one of the site's own pages
  shown over the screen.
- [project.yml](project.yml): the Xcode project, as input for
  [XcodeGen](https://github.com/yonaskolb/XcodeGen). The generated `RideAndRail.xcodeproj` is not
  committed.

Bundle identifier `ch.rideandrail.app` (the Android app's package name), iOS 16 or later, iPhone
only (on an iPad it runs as an iPhone app), in the site's four languages: the app's language
follows the phone's, and the site follows the app's.

## What the app does around the site

| In a browser | In the app |
| --- | --- |
| A link to another site, or one to it that opens a new tab (directions, train tickets) | Shown over the app: in the app that owns the address when it is installed (Maps, SBB Mobile), else in a browser sheet. Closing it returns to the page as it was. |
| One of the site's own pages that opens in a new tab (the code of conduct next to a form) | Shown over the app in a web view of its own, with a *Done* button. |
| The demo: fictional drives, *View as a demo driver*, the banner's and the pages' words about it | **Not shown.** The site goes by the app's User-Agent (`RideAndRailApp/…`): see [The iOS app shows the site without its demo](../../docs/ios-app-without-demo.md). |
| The payment page the site sends the passenger to | Stays in the app: it sends the passenger back to the site. |
| `mailto:` and `tel:` links | Handed to iOS. |
| *Continue with Google / Apple* | The system's sign-in sheet, see below. |
| `confirm()` and `alert()` (cancelling a drive, deleting the account) | Native dialogs: a web view shows none by itself. |
| Choosing a profile photo | The system's photo picker; the camera asks for permission the first time it is used. |
| The service worker's offline copy | Not available in a web view. Without a connection at launch the app shows its own "You're offline" screen with *Try again*. |
| A new version after a deploy | Every launch loads the site from the server. An app left in the background for more than six hours loads it afresh when it comes back. |

The app asks for no permission at launch and contains no analytics or advertising code.

## Sign-in

- **Email:** the code of the sign-in email is typed in the app, as in the installed web app (the
  link opens in Safari, which has its own cookies). See [docs/sign-in.md](../../docs/sign-in.md).
- **Google and Apple:** Google refuses to sign people in inside an app's web view, and Apple's own
  sheet (Face ID) only appears in a browser. So the page's link, `/auth/<provider>/start`, is
  opened in the system's sign-in sheet (`ASWebAuthenticationSession`) with `app=ios` and a
  challenge. Once the provider vouches for the person, the server sends the sheet to
  `ch.rideandrail.app://signin?code=…`, which closes it; the web view posts that one-time code,
  with the verifier the challenge was made from, to `/auth/app/finish`, which starts the session
  in the app. The server's side is described in
  [docs/app-sign-in.md](../../docs/app-sign-in.md).

## Building

The app can only be built on a Mac. The [iOS app workflow](../../.github/workflows/ios.yml) does
it on GitHub's:

- **On every pull request that changes this folder:** generates the project, runs the tests in an
  iPhone simulator, launches the app there and keeps a screenshot of it (the run's `ios-build`
  artifact).
- **By hand** (*Actions → iOS app → Run workflow*): the same, and with *Upload to TestFlight*
  ticked, an App Store build signed and uploaded to App Store Connect.

On a Mac with Xcode:

```bash
brew install xcodegen
cd mobile/ios
xcodegen generate
open RideAndRail.xcodeproj   # then run on a simulator
```

To show another site than production (a test server reachable over HTTPS), build with
`SITE_HOST=test.example.org`.

The icon is generated with the site's, from the same description of the logo:
`uv run --with pillow python frontend/icons/make_icons.py`.

## Steps to the App Store (for a human)

None of these is needed to merge this folder; they need the Apple developer account.

1. **Register the app.** In [App Store Connect](https://appstoreconnect.apple.com), *Apps → +
   → New App*: platform iOS, name "Ride and Rail", bundle ID `ch.rideandrail.app` (register it
   first under *Certificates, Identifiers & Profiles → Identifiers* if it isn't offered), SKU of
   your choice.
2. **Create an API key for the workflow.** *Users and Access → Integrations → App Store Connect
   API → Team Keys → +*, with the role *Admin* (needed to create the signing certificate and
   profile). Download the `.p8` file (once only) and note the key ID and the issuer ID.
3. **Give them to GitHub**, as repository secrets:
   ```bash
   gh secret set APP_STORE_CONNECT_KEY_ID --body "XXXXXXXXXX"
   gh secret set APP_STORE_CONNECT_ISSUER_ID --body "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
   base64 -w0 AuthKey_XXXXXXXXXX.p8 | gh secret set APP_STORE_CONNECT_PRIVATE_KEY
   ```
   The team ID comes from the repository variable `APPLE_TEAM_ID`, which sign-in with Apple
   already uses. Keep the `.p8` file in a password manager, never in git (`*.p8` is ignored).
4. **Upload a build:** *Actions → iOS app → Run workflow*, with *Upload to TestFlight* ticked.
   The build number is the run's number, so every upload is a new build. A few minutes after the
   run, the build appears in App Store Connect under *TestFlight*.
5. **Test with TestFlight.** Internal testers (people of the App Store Connect team) get the build
   at once; external testers (up to 10,000, by email or a public link) after a short review of the
   first build.
6. **Submit for review.** The app's page needs: screenshots for a 6.9-inch iPhone (1320×2868), the
   description, the subtitle, keywords, the support URL <https://rideandrail.ch/contact.html>, the privacy
   policy URL <https://rideandrail.ch/privacy.html>, the *App Privacy* answers, an age rating, and
   for the reviewer a way to sign in (an account's email address and how to get its code, or a
   demo account).

Before submitting, check Apple's current rules on two points:

- An app that is mostly a website can be refused (guideline 4.2, minimum functionality). What the
  app adds around the site is listed above.
- When an account is deleted, an app offering *Sign in with Apple* must revoke Apple's tokens
  (see [docs/social-login.md](../../docs/social-login.md)); the site keeps none today.

Payment for a ride is payment for a service used outside the app, which Apple lets apps take
with their own payment provider (guideline 3.1.3(e)); in-app purchase is not involved.

## Later releases

A change in this folder needs a new build: raise `MARKETING_VERSION` in `project.yml` when the
change is visible to users, run the workflow with *Upload to TestFlight*, and submit the build.
Changes to the site need nothing here.
