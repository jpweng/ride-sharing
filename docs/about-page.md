# The About page

[frontend/about.html](../frontend/about.html) says what Ride and Rail is and what it offers, for
someone who has never used it. It is linked from the banner of the app ("About"), needs no
account, and is a text page like the privacy notice: one complete text per language
(`<article lang="xx" data-i18n-block>`), with the visitor's shown by `i18n.js`. Its title is
`about.title` in `frontend/i18n/<lang>/about.json`.

## It describes the service as it is

The page is the public description of the service, on the site and in the apps (the Android app
is the site itself, see [Installable app (PWA) and Android app](pwa.md)). It must agree with what
the site does, with the README, with these docs and with the
[privacy notice](../frontend/privacy.html), at all times:

- **A change to what users can do, see or pay changes the page in the same pull request**, in all
  four languages, with the formal address. This is a rule of [CLAUDE.md](../CLAUDE.md).
- The page states what exists, not what is planned. The one exception is the sentence about the
  Android and iPhone apps ("coming soon").
- It gives few numbers, so that it rarely goes stale. Those it gives are checked against the code.

Where each statement comes from, to know what to reread when something changes:

| The page says | Source |
| --- | --- |
| One search shows drives, trips that combine a ride with public transport, and public transport alone | [search limits](search-limits.md), [combined trips](combined-trips.md), [passenger page](passenger-page.md) |
| Leave at or arrive by; searching needs no account | [passenger page](passenger-page.md), [API](api.md) |
| Pickup and drop-off points, times, the price next to the train fare | `backend/matching.py`, `backend/pricing.py`, [train fares](train-fares.md) |
| Asking for a seat, the driver's answer, told in the inbox and by email | [booking](booking.md), [notifications by email](email-notifications.md) |
| Asking drivers for a ride nobody has posted | [ride predictions and ride requests](ride-predictions.md), `backend/notify.py` |
| Posting drives; areas, their ride requests, accepting one directly | [driver areas](driver-areas.md), [booking](booking.md) |
| Passenger mode and driver mode with one account | `frontend/app.js` (`setMode`) |
| Costs shared, the price limits, nothing paid through the service | `backend/pricing.py`, [booking](booking.md) |
| Sign-in by email link or the code in the same email, or Google or Apple; 18 or older; the code of conduct | [sign-in](sign-in.md), [social login](social-login.md), `frontend/conduct.html` |
| First name and initial; optional profiles and who sees them | [profiles](profiles.md) |
| Ratings and reports | [ratings and reports](ratings-and-reports.md) |
| Browser, home screen, four languages | [PWA](pwa.md), [translations](translations.md) |
| Demo drives are fictional and can't be booked | [demo drives and data retention](demo-drives-and-data-retention.md) |
| Writing to the team | [the contact page](contact.md) |
| No advertising or analytics, 30 days, deleting one's account | [privacy notice](../frontend/privacy.html), `backend/maintenance.py` |

Some statements depend on how the site is set up, not only on the code. The page is written for
production as it runs today: sign-in with Google and Apple offered
([their keys are set](social-login.md)), demo drives on (`DEMO_DRIVES`), notifications by email on
(`EMAIL_NOTIFICATIONS`) and profile photos on (`PHOTO_BUCKET`). Switching one of them off for good
means changing the page.

**The apps.** The page says apps for Android and iPhone are coming soon. When the Android app
([mobile/android/](../mobile/android/)) is published, replace that with a link to it; the same for
the iPhone app, which doesn't exist yet. The store listings describe the same service as this page:
write them from it.

**Not in the page yet:** who is behind Ride and Rail (a person or a company). It is the same open
question as the privacy notice's "Who is responsible" (see [Suggestions.md](../Suggestions.md)).

## Checks

[backend/tests/test_about_page.py](../backend/tests/test_about_page.py): the four texts have the
same structure (headings, paragraphs, list items and links in the same order), the links lead to
existing pages, the app's banner links to the page in every language, and the facts taken from the
code are still the code's (30 days of retention, 18 years, the four languages, a ride never
costing more than the train and costing less with more passengers). The page is also covered by
the checks every page gets: all four languages and the formal address
([test_i18n.py](../backend/tests/test_i18n.py)), installable and in the offline copy
([test_pwa.py](../backend/tests/test_pwa.py)).
