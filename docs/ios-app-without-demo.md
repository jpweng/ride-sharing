# The iOS app shows the site without its demo

The site is a demo for now, and says so: a banner on every page, fictional drives marked *Demo*,
"view as a demo driver" on the sign-in page, a note on the About page and in the privacy notice.
The [iOS app](../mobile/ios/) shows the same site **without any of it**: no fictional drive, and
nothing that says "demo". Browsers and the Android app are not affected.

How the site knows: the app's web view names itself at the end of its User-Agent,
`RideAndRailApp/<version>`. A User-Agent proves nothing; anyone who sends this one gets the site
without its demo, which shows them less, never more.

## What changes in the app

| | In a browser | In the iOS app |
| --- | --- | --- |
| Fictional (demo) drives | In searches, combined trips, the list of drives and the inbox, marked *Demo*, not bookable | Left out by the server. A search shows the real drives only, and "Nobody is driving this way" when there is none. |
| *View as a demo driver* | On the sign-in page | Hidden; the server offers no demo driver and refuses the sign-in (404 `demo_driver_not_found`). |
| The banner | "Demo — a prototype for testing. …", then who sees your name and the links | Who sees your name, and the links (About, Privacy, Code of conduct, Contact) |
| About page | The note "A demo version for now" on top, and the section "A demo for now" | Neither |
| Privacy notice | Opens with "This site is a demo …, not for arranging real trips" | Opens with what the notice explains |
| After asking for a ride nobody was told about | "… (demo drivers are fictional and are never asked) …" | The same sentence without the brackets (`ride.askSentNoneWithoutDemo`) |

Nothing is deleted: the demo drives stay in the database for everybody else. What real users do
is the same in the app as anywhere: their drives, requests and bookings are real ones.

## How

- **Server** ([backend/ios_app.py](../backend/ios_app.py)): a middleware in `main.py` marks a
  request from the app, and [demo_drives.shown()](../backend/demo_drives.py) then answers "no"
  for that request, as it does for everybody when `DEMO_DRIVES=off`. Every place that puts demo
  drives in an answer asks `shown()`; the hourly upkeep, which decides what exists, still goes by
  `enabled()`.
- **Pages** ([frontend/ios_app.js](../frontend/ios_app.js)): loaded in the `<head>` of every
  page, it puts the class `without-demo` on the page when the User-Agent names the app, before
  anything is drawn. `style.css` then hides everything marked `class="demo-only"`.
- **The app** ([mobile/ios/](../mobile/ios/)): the site's own pages that open in a new tab (the
  code of conduct) are shown in a web view of the app's, not in a browser sheet, which would have
  Safari's User-Agent and show the demo.

## Rules for new pages and texts

- Anything a page says about the demo goes inside an element with `class="demo-only"`, in every
  language, and what is left must read well without it.
- A new page loads `ios_app.js` in its `<head>`, before `style.css`.
- A new place that puts demo drives in an answer asks `demo_drives.shown()`.

`backend/tests/test_ios_app.py` checks all three: no page says anything about the demo outside
`demo-only`, every page loads the script first, and the app's requests get no demo drive.

## Endpoints

No new one. With the app's User-Agent: `POST /api/match`, `POST /api/match/combined`,
`GET /api/drives` and `GET /api/notifications` leave demo drives out; `GET /api/drives/{id}` and a booking of a demo drive
answer 404; `GET /api/auth/demo_drivers` answers `[]`; `POST /api/auth/demo` answers 404
`demo_driver_not_found`.
