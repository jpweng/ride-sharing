# Answers for the Play Console forms

Google Play asks a set of questions before an app can be published (*Policy → App content*, and
the store listing). These are the answers that follow from what the site does today, with where
each comes from, so that they can be typed in without rereading the code. **They are a draft to
check against each form as Google words it that day**; whoever submits the forms answers for them.

The app is the site (a Trusted Web Activity), so everything here is about the site. The source for
what is collected is the [privacy notice](../../frontend/privacy.html); when it changes, reread
this page.

## Store listing

- **Texts:** [mobile/listing/](../listing/), one file per language (English as the default
  language; German, French and Italian as translations). They are written from the About page by
  [make_listing.py](../make_listing.py), never by hand.
- **Category:** Travel & Local. **Tags:** carpooling, public transport.
- **Contact details:** an email address is required and is shown publicly on the listing. Website:
  `https://rideandrail.ch`.
- **Graphics:** the 512×512 icon (`store_icon.png`, written by Bubblewrap), the 1024×500 feature
  graphic and at least two phone screenshots: see [Store graphics](README.md#store-graphics).
- **Privacy policy:** `https://rideandrail.ch/privacy.html`

## App content

| Form | Answer | Why |
| --- | --- | --- |
| Ads | No ads | The site has no advertising and no trackers. |
| App access | Some functionality is restricted | Searching needs no account; everything else needs signing in by email. Give the reviewer an account: an email address they can read, or the instructions to use *View as a demo driver* on the sign-in page, which shows a driver's pages without an account. |
| Content rating | Questionnaire category: *All other app types*. Users can interact (messages between passenger and driver) and see each other's first name and profile; no user-generated content is public beyond first name, initial and the drives offered; nothing violent, sexual or gambling-related; no location of the device is shared. | [Booking](../../docs/booking.md), [profiles](../../docs/profiles.md) |
| Target audience | 18 and over only | Every user confirms being 18 or older ([sign-in](../../docs/sign-in.md)). |
| News app, health app, government app, financial features | No | |
| Data safety | See below | |

## Data safety

**Does the app collect or share user data?** Yes, it collects. **Is all of it encrypted in
transit?** Yes (HTTPS only). **Can users ask for their data to be deleted?** Yes: in the app, on
the account page (*Delete my account*), which deletes the account and all its data at once;
address to give: `https://rideandrail.ch/privacy.html` (section *Your rights*).

"Shared" in Google's sense means handed to another company for its own use. Nothing is: Amazon Web
Services (hosting, email) and Stripe (payments) process data on the service's behalf; the routing
and timetable services get start and end points without any name or account; other users see what
a user chose to publish (a drive, a request, a profile).

| Data type (Google's list) | Collected | Required | Purpose | What it is |
| --- | --- | --- | --- | --- |
| Personal info → Name | Yes | Yes | App functionality, account management | First and last name; others see the first name and the initial. |
| Personal info → Email address | Yes | Yes | App functionality, account management | Sign-in, and the emails about requests and answers. |
| Personal info → User IDs | Yes | Optional | Account management | The identifier Google or Apple gives for a user who signs in with them. |
| Location → Approximate and precise location | Yes | Yes | App functionality | Not the phone's position (the app never asks for it): the start and end points a user types or picks for a drive, a search or a request. Declared because they say where the user will be. |
| Messages → Other in-app messages | Yes | Optional | App functionality | The message with a seat request and the driver's answer; a message to the team on the contact page. |
| Photos and videos → Photos | Yes | Optional | App functionality | The profile photo. |
| App activity → In-app search history | Yes | Yes | App functionality | Searches, kept 30 days. |
| App activity → Other user-generated content | Yes | Optional | App functionality | The profile's text and habits, the car, ratings and reports. |
| Financial info → Purchase history | Only with payments switched on | Optional | App functionality | Amount, trip and time of a payment for a seat. Card details are entered on Stripe's page and never reach the site, so *User payment info* is not collected. |

Not collected: contacts, calendar, audio, files, health, web browsing history, installed apps,
crash logs and diagnostics from the device, advertising or device identifiers. The server's logs
hold the IP address and browser type of each request for 14 days; Google's list has no type for
that.

None of the data is processed ephemerally, and none is used for advertising, analytics or
personalisation.

## When something changes

- Payments switched on in production: add *Purchase history* to the data safety form.
- A new kind of data in the privacy notice: add its row here and in the form.
- Push notifications or the phone's position used by the site: the app then asks for an Android
  permission; the forms and `twa-manifest.json` (`features.locationDelegation`) follow.
