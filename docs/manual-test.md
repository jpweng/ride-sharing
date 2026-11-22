# Manual test procedure

Every feature of the site tested by hand, part by part, with two accounts playing passenger and
driver. The automated tests (`uv run pytest`) check the rules; this checks what a person sees, on
the real site, in real browsers and mailboxes. A pull request that changes what a step describes
updates the step.

## Before you start

Test on <https://rideandrail.ch>, the site users get. Allow about three hours, plus one wait: Part 9
needs a ride to be over, so step 5.1 prepares it early.

| You need | For |
| --- | --- |
| Two email addresses you can read, with no account on the site yet | Account **P** (passenger) and account **D** (driver). `name+p@…` and `name+d@…` variants of one mailbox count as two. |
| A third address | Part 6: a second driver, D2, in a private window or a third browser |
| Two browsers, or one browser and a private window | P in browser A and D in browser B, signed in at the same time |
| A phone: Android with Chrome, or iPhone with Safari | Parts 10 and 11 |
| A large photo (over 1 MB) and a file that is not an image | Part 8 |

How to read the steps:

- Each step has a number, an action, and after **Expect** what must happen. When it doesn't, note
  the step's number, what happened instead, and the device and browser. To tick steps off, copy a
  part into an issue.
- Do the parts in order: later parts use the accounts, drives and bookings made earlier.
- Button and message names are the English ones. Run the whole procedure in English; Part 1 checks
  the three other languages.
- Test trips with fictional drives every day: **Lausanne → Genève** and **Lausanne → Bern**. Demo
  drives carry a "Demo" label and cannot be booked.
- Your own test drives are real to other users: they can find them and ask to join until Part 13
  deletes the accounts.
- A few steps are marked **(local)**. They need the server's own output, so run them on your machine
  (see the README's "Run it"); the sign-in email is printed there instead of sent.
- Steps that wait for another moment: 6.33 and 6.34 (later days), 7.12 and 7.13 (after 22:00), 10.9
  (the next deploy), 10.10 (the Android app).

## Part 1. First visit and languages

Signed out, in a new private window of browser A, so nothing is remembered from earlier visits.

- [ ] **1.1** Open <https://rideandrail.ch>. **Expect:** the logo and "Ride and Rail", the subtitle
  "Carpooling for Switzerland", the DE FR IT EN picker, the Demo banner with "About", "Privacy",
  "Code of conduct" and "Contact", the tabs "Find a ride" and "Inbox", a "Passenger mode" button and
  a "Sign in" link. The page is a card with From and To. No map, no search button.
- [ ] **1.2** Look at the language. **Expect:** your browser's first language among German, French,
  Italian and English, otherwise English.
- [ ] **1.3** Click DE, FR, IT, EN in turn. **Expect:** every text changes at once without a reload,
  the browser tab's title too. No English left in another language, no raw key such as `ride.title`.
  The chosen language is highlighted.
- [ ] **1.4** Choose FR and reload. **Expect:** still French.
- [ ] **1.5** Open <https://rideandrail.ch/?lang=it>. **Expect:** Italian. Click DE: German, and
  `?lang=it` leaves the address bar.
- [ ] **1.6** In DE, FR and IT, read the banner and the sign-in page ("Sign in" link). **Expect:**
  formal address everywhere: "Sie", "vous", "Lei". Never "du" or "tu".
- [ ] **1.7** Click "Privacy" in the banner. **Expect:** the privacy notice in the current language,
  with its own language picker and "Back to the app". Each of the four languages shows a complete
  text.
- [ ] **1.8** Back in the app, click "Code of conduct". **Expect:** the same: four complete texts,
  one per language.
- [ ] **1.9** On either page, click the "Ride and Rail" wordmark. **Expect:** back in the app.
- [ ] **1.10** Open <https://rideandrail.ch/#/nothing>. **Expect:** the "Find a ride" page, no
  error.
- [ ] **1.11** View the page source. **Expect:** `<meta name="robots" content="noindex, nofollow"
  />`, which keeps the demo out of search engines.

**The About and Contact pages**

- [ ] **1.12** Click "About" in the banner. **Expect:** what Ride and Rail is and offers, as a
  complete text in each of the four languages. Its links to the privacy notice, the code of conduct
  and the contact page open them. What it says agrees with what the rest of this procedure shows.
- [ ] **1.13** Click "Contact" in the banner, then find the link to it in the privacy notice and in
  the code of conduct. **Expect:** "Contact us" with "Your name", "Your email address", "Your
  message" and "Send", in the four languages. If it says "The contact form isn't available at the
  moment…" instead, the form is switched off on the server: note it and skip the next three steps.
- [ ] **1.14** Click "Send" with a field left empty, then with "abc" as the address. **Expect:**
  "Fill in your name, your email address and your message.", then "Enter a valid email address."
- [ ] **1.15** Send a real message with an address you can read. **Expect:** "Sending…", then "Thank
  you. Your message is on its way to our team; we will answer at (the address)." The team's mailbox
  gets an email whose subject starts with "[Ride and Rail contact]", with the name, the address and
  the message. Answering it writes to the visitor's address.
- [ ] **1.16** Send three more within ten minutes. **Expect:** the fourth of the four answers "Too
  many requests from here. Please try again later."
- [ ] **1.17** Go back to EN for the rest of the procedure.

## Part 2. Searching without an account

Still signed out in browser A. Searching is public: everything here must work without an account.

**The two fields**

- [ ] **2.1** Click into the empty From field. **Expect:** a list with one entry, "Pick on map".
- [ ] **2.2** Type "La", then "Lau". **Expect:** nothing for two letters. With three, after a short
  pause, suggestions with towns first and "Pick on map" last.
- [ ] **2.3** Use the arrow keys, Enter and Escape in the list. **Expect:** the arrows move the
  highlight, Enter takes the entry, Escape closes the list.
- [ ] **2.4** Choose "Lausanne". **Expect:** the field holds the name and no search starts yet. On a
  window 980 px wide or more the time is already in the bar; narrower, it is not shown yet.
- [ ] **2.5** In To, type "Genève" and choose it. **Expect:** "Searching…", then results, without
  pressing anything. The time shows "Leave at", today, and now rounded up to five minutes.
- [ ] **2.6** In To, try a street address ("Rue de Bourg 1 Lausanne"), a named place ("CHUV") and a
  station ("Bern Bahnhof"). **Expect:** each is found, and choosing one searches again. Put "Genève"
  back.

**The results**

- [ ] **2.7** Read the cards. **Expect:** at most 20. Each starts with the times in large figures
  ("hh:mm → hh:mm"), how long the ride takes and, at the right, "CHF N" with "train ≈ CHF N" under
  it. Then a figure in a circle and "A driver takes you from (a stop) to (a stop)", with a "Demo"
  label after "A driver"; "pickup N km from your start" or "drop-off N km from your destination"
  only when that is more than 1 km. The price is never above the train fare. Demo cards have
  no button to ask for a seat. Each card has a small arrow at the end of its first line, and
  pointing at a card outlines it in green. Under the cards, a green-tinted panel asks "Nothing
  that suits you?" with the filled button "Ask drivers for this ride".
- [ ] **2.8** Click a card. **Expect:** its details open: the drive's route, the departure and the
  free seats, how far the pickup and the drop-off are from your start and destination, "To the
  pickup: N min on foot", "From the drop-off:" with times on foot, by car and by public transport,
  "on time" or "N min earlier" or "later", "N km together" and "Show on map". The arrow points up,
  and the card says "Demo drives are fictional and can't be booked." A second click closes them.
- [ ] **2.9** Click "Directions" in the details. **Expect:** Google Maps opens in a new tab with the
  way between the two points. The card stays open.
- [ ] **2.10** Look for a card whose pickup or drop-off is 3 km or more away. **Expect:** its
  details propose public transport for that part: two stations and one or two connections. To the
  pickup, the connection arrives at least 10 minutes before the car.
- [ ] **2.11** Click "Show on map" on a Demo card. **Expect:** the map opens over the page with its
  route highlighted, the other routes grey, pins at your start and destination, and two markers,
  "Pickup ~hh:mm" and "Drop-off ~hh:mm". A demo ride shows on the map like any ride; it only can't
  be booked.

**Public transport for the same trip**

- [ ] **2.12** Under the rides, find the line "Public transport", for example "3 connections ·
  12:27 → 13:32 · 1 h 05 min". Click it. **Expect:** the two stations, "Ticket, 2nd class: about
  CHF N (half fare about CHF N)", and a row per connection: times, duration, transfers, lines and
  who runs them. No operator logo.
- [ ] **2.13** Click "Tickets on SBB". **Expect:** sbb.ch opens in a new tab, in the page's
  language, with that connection.

**The time**

- [ ] **2.14** Before touching the time, turn "Leave at" into "Arrive by". **Expect:** the time
  jumps to one hour from now and a new search runs. Every drop-off is at or before that time, the
  latest first. Public transport connections arrive by it too, and the line gives the times of the
  one that leaves last.
- [ ] **2.15** Turn it back to "Leave at". **Expect:** the time is now again, and the search runs
  again.
- [ ] **2.16** Choose tomorrow at 07:00. **Expect:** one new search, about half a second after the
  last change. Pickups are within four hours of 07:00.
- [ ] **2.17** Choose a time earlier today. **Expect:** the time goes back to now, and the search is
  for now.
- [ ] **2.18** Click the round swap button. **Expect:** From and To change places and a new search
  runs.

**Picking a place on the map**

- [ ] **2.19** Click into From, empty it, and choose "Pick on map". **Expect:** the map opens over
  the page with "Tap the place on the map."
- [ ] **2.20** Zoom in on a town and click a building. **Expect:** a small card names what is there
  (a shop, a stop, a company, else the address), with "Use this place" and up to three places under
  "Next to it:". Clicking one of those proposes it instead. Clicking elsewhere on the map proposes
  another place.
- [ ] **2.21** Click "Use this place". **Expect:** the map closes, From shows the place, and the
  search runs.
- [ ] **2.22** "Pick on map" again, zoom out until the whole country shows, click near a town.
  **Expect:** the town is proposed.
- [ ] **2.23** Close the map without choosing, three times: with "Close the map", with a click
  beside the map, with Escape. **Expect:** each closes it, From shows its place again, and the
  results are the same as before.
- [ ] **2.24** Empty From by hand, or type a few other letters, then click elsewhere on the page.
  **Expect:** From shows its place again: the one the results are for.

**No ride, and a ride plus public transport**

- [ ] **2.25** Search between two remote villages, for example Juf → Bosco/Gurin. **Expect:** a
  small picture and "Nobody is driving this way around that time yet." "Ask drivers for this ride"
  is the one filled button. Below, the public transport line or "No direct public transport option
  found."
- [ ] **2.26** Click "Ask drivers for this ride". **Expect:** the sign-in page with "Sign in to
  request a seat or to ask drivers for a ride." The browser's Back button returns to the search,
  still filled in.
- [ ] **2.27** Search Bulle → Genève, "Leave at" 06:50 on a weekday. **Expect:** a section "A ride
  and public transport" with up to four cards. Each starts with the whole trip: its times in large
  figures, how long it takes and, at the right, "≈ CHF N" with "train alone ≈ CHF N" under it.
  Then two lines in order: "A driver takes you to (a stop)", then "Public transport to (a
  station)". Open one: the details start with the two steps and their times, then "ride CHF N +
  train ≈ CHF N". With no drive for the whole way, nothing is above the section's title. If the
  section is missing, try Sion → Genève at 06:20, or Genève → Bulle around 17:30. Step 5.21 makes
  one for certain.
- [ ] **2.27b** Search again while watching the results. **Expect:** the drives (or "Searching…")
  first; "Searching…" where the public transport line will be and, under drives,
  "Looking for rides combined with public transport…"; then each part in its place, without the
  drives' cards moving or closing.
- [ ] **2.28** Read the end of each combined card's total line. **Expect:** "N min quicker than
  public transport alone" on a trip at least 5 minutes quicker. Nothing about it on the others: none
  says it is slower.
- [ ] **2.29** Open a combined card. **Expect:** the departure, seats and "N km together", the two
  stations, the ticket's price, the connection with "Tickets on SBB", and "Show on map".

**Remembered trip, language**

- [ ] **2.30** With results on the page and a time chosen for tomorrow, reload. **Expect:** the same
  From, To, "Leave at" or "Arrive by" and time, searched again.
- [ ] **2.31** Open the public transport line, then switch to DE. **Expect:** the cards are in
  German and the public transport box is still open. Go back to EN.

## Part 3. Signing in

Close the private window. From here on use normal windows, so sessions last: browser A for P,
browser B for D. If either is signed in with your own account, sign out first.

**Pages that need an account**

- [ ] **3.1** In browser A, click the "Inbox" tab. **Expect:** the "Sign in" page: an email field,
  "Send me a sign-in link", the Google and Apple buttons when they are offered, and a box "Just
  looking?" with a list of demo drivers.
- [ ] **3.2** Click the "Passenger mode" button. **Expect:** it now reads "Driver mode", the first
  tab is "Your drives", and the sign-in page shows. Click it again: "Passenger mode" and "Find a
  ride".

**The email link**

- [ ] **3.3** On the sign-in page, send with the field empty, then with "abc". **Expect:** "Enter
  your email address.", then "Enter a valid email address."
- [ ] **3.4** Enter P's address and send. **Expect:** the button reads "Sending…", then "A sign-in
  link is on its way to (the address). Open it within 15 minutes…".
- [ ] **3.5** Read the email. **Expect:** it arrives within a minute, not in spam, in English:
  subject "Your sign-in link for Ride and Rail", a button "Sign in to Ride and Rail", the link as
  text, a 6-digit code, and a line saying it can be ignored.
- [ ] **3.6** Before opening it, search Lausanne → Genève in the first tab. Then open the link in a
  new tab of browser A. **Expect:** "Signing you in…" briefly, then "Welcome to Ride and Rail" with
  first name, last name and two checkboxes. The address bar no longer holds the link's code.
- [ ] **3.7** Click "Continue" with nothing filled, then with a first name only, then with both
  names and the boxes unticked. **Expect:** "Enter your first name.", "Enter your last name.", then
  "To use Ride and Rail, confirm that you are 18 or older and accept the code of conduct."
- [ ] **3.8** Click "code of conduct" in the second checkbox's text. **Expect:** it opens in a new
  tab.
- [ ] **3.9** Enter "Paula Passenger", tick both boxes, click "Continue". **Expect:** the "Find a
  ride" page with the Lausanne → Genève search of step 3.6 filled in and searched. The header shows
  "Paula P." and "Sign out". The cards now show drivers' names and their first letter in the circle.
- [ ] **3.10** Go back to the first tab. **Expect:** it is signed in too, and its results now show
  the names.
- [ ] **3.11** In browser B, switch to FR and ask for a link for D's address. **Expect:** the email
  is in French.
- [ ] **3.12** Open D's link in browser B and finish the profile as "Daniel Driver". Go back to EN.
  **Expect:** signed in as "Daniel D.".
- [ ] **3.13** In a private window, open D's link a second time. **Expect:** "Signing in didn't
  work: This sign-in link is invalid, has expired or was already used. Ask for a new one." The
  button "Ask for a new link" shows the sign-in page.
- [ ] **3.14** Optional, 16 minutes: ask for a new link for P in the private window, wait 16
  minutes, open it. **Expect:** the same "invalid, has expired" message.

**The session and the account page**

- [ ] **3.15** As P, click your name in the header. **Expect:** "Your account": "Other users see you
  as Paula P.", your email address, "Sign out", the "Your profile" form and "Delete your account".
- [ ] **3.16** Close browser A completely and open the site again. **Expect:** still signed in as
  Paula P.
- [ ] **3.17** Click "Sign out". **Expect:** the "Find a ride" page with empty fields and a "Sign
  in" link. After a reload the fields are still empty: the last trip is forgotten.

**The code in the same email**

For where the link can't be opened: the app installed on an iPhone (step 10.4), or an email read on
another device. In browser A, signed out.

- [ ] **3.18** Ask for a link for P's address. **Expect:** under the confirmation: "Reading the
  email on another device, or using the installed app? Type the code from the email here instead.",
  a field "Code from the email" and "Sign in with the code".
- [ ] **3.19** Read the email. **Expect:** after the link: "Or type this code on the page where you
  asked for the link:" and six digits.
- [ ] **3.20** Click "Sign in with the code" with the field empty, then with a wrong code of six
  digits. **Expect:** "Enter the 6-digit code from the email.", then "This code is wrong or no
  longer valid. Check it, or ask for a new link."
- [ ] **3.21** Type the right code. **Expect:** signed in as Paula P., with no welcome step. The
  inbox says "Nothing yet…" and both of its checkboxes are ticked. In a private window, the link of
  that email is now refused as already used.
- [ ] **3.22** Sign out and ask for a new link. Type a wrong code five times, then the right one.
  **Expect:** all six are refused. The link of that email still signs in: open it.

**Viewing as a demo driver**

- [ ] **3.23** In a private window, open the sign-in page. **Expect:** the list under "Just
  looking?" holds 8 names, Jean Dupont first.
- [ ] **3.24** Click "View as a demo driver". **Expect:** driver mode, "Your drives" with Jean
  Dupont's drives, the map beside them, and a note: "You're viewing the demo as Jean Dupont, a
  fictional driver: you can look around but not change anything." The header shows the name with a
  "demo" label.
- [ ] **3.25** Fill the "Offer a drive" form and click "Offer drive". Then click "Cancel drive" on a
  listed drive and confirm. **Expect:** both refused with "You're viewing the demo as a fictional
  driver: nothing can be changed…". The drive stays.
- [ ] **3.26** Open the inbox and untick a checkbox. **Expect:** the same message, and the setting
  does not change.
- [ ] **3.27** Switch to passenger mode, search a trip, click "Ask drivers for this ride".
  **Expect:** the same message.
- [ ] **3.28** Click the name in the header. **Expect:** the account page without a profile form,
  without areas and without "Delete your account". Sign out.

**Google and Apple**

- [ ] **3.29** Look at the sign-in page. **Expect:** under "or", "Continue with Google" and
  "Continue with Apple", for each provider whose keys are set on the server. Without them: no
  button, and <https://rideandrail.ch/auth/google/start> answers 404.
- [ ] **3.30** Click a provider's button and sign in with the address of account P. **Expect:** you
  land in Paula P.'s account, with no welcome step.
- [ ] **3.31** Sign in with a provider's account whose address has no account here. **Expect:** the
  welcome step with the provider's name filled in as a suggestion. Delete that account afterwards.
- [ ] **3.32** Start with a provider, then cancel there. **Expect:** back on the sign-in page with
  "You didn't finish signing in. Try again, or use the email link."

**A changed code of conduct (local)**

- [ ] **3.33** Locally, signed in: change `CONDUCT_VERSION` in `backend/auth.py`, let the server
  restart, reload the page. **Expect:** the welcome page without the name fields, with "The code of
  conduct has changed. Please read it and accept it to continue." After ticking both boxes you are
  back in the app. Undo the change.

## Part 4. Driver: offering a drive

As D in browser B, unless a step names another account.

**Driver mode**

- [ ] **4.1** Click "Passenger mode". **Expect:** the button reads "Driver mode", the tabs are "Your
  drives" and "Inbox", and the map is beside the page. After a reload it is still driver mode.
- [ ] **4.2** Read the page. **Expect:** "You have no upcoming drives. Offer one below.", then "Ride
  requests in your areas" with "Add an area in your profile to see the ride requests around you."
  and a link "Open your profile", then the "Offer a drive" form.
- [ ] **4.3** Look at the price field. **Expect:** greyed out, with "Choose where you're leaving
  from and where you're going to see the price range."

**The form**

- [ ] **4.4** Click "Offer drive" with nothing set, then with From only. **Expect:** "Choose where
  you're leaving from.", then "Choose where you're going."
- [ ] **4.5** Type "Lausanne" in From and choose it. **Expect:** its coordinates under the field and
  a marker on the map.
- [ ] **4.6** Click "Pick on map" next to To, click a place on the map, then "Use this place".
  **Expect:** the button is highlighted while picking, the place is proposed in a card as in step
  2.20, then To holds its name and the map frames both points. Clicking "Pick on map" twice in a row
  stops the picking.
- [ ] **4.7** Set To to "Bern" by typing. **Expect:** "Calculating the price range…", then "From CHF
  N to CHF N for N km. Train, 2nd class: CHF N (half fare CHF N)." The price field is filled and can
  be edited.
- [ ] **4.8** Check the numbers. **Expect:** the lowest price is about CHF 0.15 per km. The highest
  is the lower of CHF 0.375 per km and the train's full fare. The price filled in is the half fare,
  kept inside the range.
- [ ] **4.9** Read the line under the price. **Expect:** "When more passengers join, each pays less:
  2 passengers: CHF N each · 3 passengers: CHF N each." Set Seats to 1: the line goes. Set it to 8:
  it stops at 4 passengers. Set it back to 3.
- [ ] **4.10** Raise the price just above the half fare. **Expect:** "Above the half-fare train
  ticket (CHF N). Passengers with a half-fare travelcard may prefer the train."
- [ ] **4.11** Type a price above the highest, click "Offer drive". Then one below the lowest.
  **Expect:** both refused with "Choose a price between CHF N and CHF N." Nothing is posted.
- [ ] **4.12** Set From and To to the same place, and click "Offer drive". **Expect:** "The start
  and the destination are the same place." under the price, and again after the click. Nothing is
  posted.

**Posting, seeing and cancelling a drive**

- [ ] **4.13** Lausanne → Bern, tomorrow 08:00, 3 seats, the price as proposed. Click "Offer drive".
  **Expect:** "Posting…", then "Drive offered: Lausanne → Bern." From and To are emptied. The drive
  is listed with its date and time, "3 seats", the distance, "CHF N per seat", "train ≈ CHF N" and
  "Cancel drive". The map shows its route along roads, not a straight line.
- [ ] **4.14** Click the drive's card. **Expect:** the map shows its route with a marker at each
  end.
- [ ] **4.15** As P in browser A: search Lausanne → Bern, tomorrow 08:00. **Expect:** Daniel D.'s
  drive is found, without a "Demo" label, above demo drives that fit as well, with a "Request a
  seat" button.
- [ ] **4.16** In a private window, signed out: the same search. **Expect:** the same card says
  "A driver" instead of the name and offers "Sign in to request a seat", which leads to the sign-in
  page.
- [ ] **4.17** As D, switch to passenger mode and run the same search. **Expect:** your own drive is
  listed without "Request a seat". Switch back to driver mode.
- [ ] **4.18** As D, offer a second drive: Lausanne → Fribourg, tomorrow 12:00. Click "Cancel drive"
  on it and choose Cancel in the question. **Expect:** the question reads "Cancel your drive
  Lausanne → Fribourg?" and the drive stays.
- [ ] **4.19** Click "Cancel drive" again and confirm. **Expect:** the drive leaves the list, and
  P's search Lausanne → Fribourg no longer finds it.
- [ ] **4.20** Offer a drive with a departure earlier today. **Expect:** "This departure time has
  passed. Choose a later one." Nothing is posted.

## Part 5. Booking, with both accounts

P in browser A in passenger mode, D in browser B in driver mode. The inbox count updates within a
minute, or at once when you change page.

**First, the ride that Part 9 will rate**

- [ ] **5.1** As D, offer Lausanne → Renens VD leaving in about 20 minutes, 2 seats. As P, search
  Lausanne → Renens VD, click "Request a seat" on Daniel D.'s drive, then "Send request". As D,
  reload "Your drives" and click "Accept". **Expect:** P's booking reads "Booked". Note the time:
  the request to rate it arrives up to an hour after the drop-off.

**Asking for a seat**

- [ ] **5.2** As P, search Lausanne → Bern, tomorrow 08:00. On Daniel D.'s card click "Request a
  seat". **Expect:** a seats list from 1 to 3, a field "Message to the driver (optional)…", "Send
  request" and "Not now". Clicks inside this form do not open or close the card. "Not now" brings
  the button back.
- [ ] **5.3** Ask for 2 seats with a message. **Expect:** on the card, "Requested" and "Request
  sent. The driver's answer will arrive in your inbox.", and no button.
- [ ] **5.4** Look below the search. **Expect:** a section "Your bookings" with the route and
  "Requested", "Driver: Daniel D.", the date, "2 seats", the pickup and drop-off lines, "CHF N per
  seat", "2 seats: CHF N in all", "You asked for a seat. Waiting for the driver's answer.",
  "Withdraw request" and "Report a problem".
- [ ] **5.5** Click the booking's card. **Expect:** the map opens with the drive's route and two
  markers, "Pickup ~hh:mm" and "Drop-off ~hh:mm".
- [ ] **5.6** On Daniel D.'s search card, open the details and click "Show on map". **Expect:** the
  map opens over the page with his route highlighted, the other routes grey, pins at your start and
  destination, and the pickup and drop-off markers with their times.
- [ ] **5.7** Reload and let the search run again. **Expect:** Daniel D.'s card still says
  "Requested" and offers no second request.

**The driver's answer**

- [ ] **5.8** As D, look at the "Inbox" tab, then open it. **Expect:** a count "1" on the tab. In
  the inbox: "Paula P. asks for 2 seats on your drive" with the trip and "Answer on your drives".
  The count goes once the inbox was opened.
- [ ] **5.9** Click "Answer on your drives". **Expect:** under the drive, "Passengers and requests":
  "Paula P. · 2 seats" with "Requested", "Lausanne → Bern, pickup ~08:00", the price, "Message from
  Paula P.: …", a message field, "Accept" and "Decline". The drive still shows 3 seats: a request
  holds no seat.
- [ ] **5.10** Click the passenger's row, away from its buttons. **Expect:** the map shows the drive
  with the pickup and drop-off markers.
- [ ] **5.11** Type a message ("Grey Golf, north exit") and click "Accept". **Expect:** the row
  reads "Booked" with "Cancel this booking". The drive shows 1 seat.
- [ ] **5.12** As P, open the inbox. **Expect:** "Daniel D. accepted your request for a seat" with
  "See your bookings". That button opens "Find a ride" at "Your bookings": "Booked", "Your seat on
  this drive is booked.", "Message from the driver: Grey Golf, north exit" and "Cancel booking".

**Cancelling, declining, asking again**

- [ ] **5.13** As P, click "Cancel booking" and confirm "Cancel your seat on Lausanne → Bern?".
  **Expect:** the booking leaves "Your bookings", and the search card offers "Request a seat" again.
  As D: "Paula P. cancelled their booking on your drive" in the inbox, and the drive has 3 seats
  again.
- [ ] **5.14** As P, ask for 1 seat. As D, click "Decline". **Expect:** P's inbox says "Daniel D.
  declined your request for a seat". The booking reads "Declined" and "The driver declined your
  request.", with no cancel button. The search card offers "Request a seat" again.
- [ ] **5.15** As P, ask again for 1 seat, then click "Withdraw request" and confirm. **Expect:**
  the booking is gone. D's inbox says "Paula P. withdrew their request for a seat".
- [ ] **5.16** As P, ask for 3 seats. As D, accept. **Expect:** the drive shows 0 seats. In a
  private window, the search Lausanne → Bern for tomorrow 08:00 no longer finds it.
- [ ] **5.17** As D, click "Cancel this booking" and confirm "Cancel Paula P.'s booking? Their seats
  become free again." **Expect:** 3 seats again. P's inbox says "Daniel D. cancelled your booking".
  The booking reads "Cancelled by the driver", and P can ask again.
- [ ] **5.18** As P, ask for 1 seat. As D, open "Your drives" in two tabs. Accept in the first, then
  click "Decline" in the second without reloading it. **Expect:** "This booking was already answered
  or cancelled." Leave this booking accepted for Part 8.

**A cancelled drive**

- [ ] **5.19** As D, offer Lausanne → Vevey, tomorrow 15:00. As P, ask for a seat. As D, accept,
  then cancel the drive. **Expect:** P's inbox says "Daniel D. cancelled the drive you had a seat
  on", with no button, and the booking is gone from "Your bookings".
- [ ] **5.20** The same again, but D cancels the drive before answering. **Expect:** "Daniel D.
  cancelled the drive you had asked to join".

**A seat on a combined trip, and the wider search**

- [ ] **5.21** As D, offer Bulle → Lausanne, tomorrow 09:00. As P, search Bulle → Genève, "Leave at"
  tomorrow 08:45. **Expect:** under "A ride and public transport", a card for Daniel D.: "By car to
  (a stop in Lausanne)", then "Public transport to Genève", with "Request a seat".
- [ ] **5.22** Ask for a seat on that card. **Expect:** the booking in "Your bookings" is for the
  car's part only. D sees the request with the trip from Bulle to that stop.
- [ ] **5.22b** As D, offer Lausanne → Bern, tomorrow 09:00 (if not offered yet). As P, search Genève → Burgdorf, "Leave
  at" tomorrow 07:30. **Expect:** under "A ride and public transport", a card for Daniel D. with
  three steps: "Public transport to (a stop in Lausanne)", "By car from (that stop) to (a stop in
  Bern)", "Public transport to Burgdorf", then the total. Its details give both connections, each
  with its ticket's price and "Tickets on SBB". (Such a card appears when the search has fewer than
  four drives and combined trips to show.)
- [ ] **5.23** As P, search Rolle → Bern, tomorrow 08:00. **Expect:** above the results, "No ride
  you can book passes within 10 km of your start and destination. The ones marked “further away”
  pass within 30 km…". Daniel D.'s Lausanne → Bern drive carries the "further away" label and can be
  asked for a seat. Its details propose public transport to the pickup when a connection fits.

## Part 6. Ride requests and driver areas

A driver is told about ride requests in the inbox at most once a day and three times a week. The
steps below are ordered so that one day is enough: keep their order. The list of requests on "Your
drives" has no such limit.

**Areas**

- [ ] **6.1** As D in driver mode, click your name. **Expect:** the account page now also shows
  "Your car" and "Your areas", with "You have no areas yet. Add one below." In passenger mode both
  are hidden. Go back to driver mode.
- [ ] **6.2** Click "Add area" without a place. **Expect:** "Choose a place first."
- [ ] **6.3** Type "Morges" and choose it. **Expect:** a circle around it on the map. The radius
  list offers 5, 10, 20 and 30 km, with 10 chosen, and changing it resizes the circle. Click "Add
  area": "Area added: Morges, within 10 km.", and it is listed.
- [ ] **6.4** Add Nyon with "Pick on map", radius 5 km. **Expect:** picking works as in step 2.20,
  and the area is listed.
- [ ] **6.5** In the list, change Nyon's radius to 10 km and reload. Click the area's card.
  **Expect:** the radius is kept, and the map shows its circle.
- [ ] **6.6** Add three more areas. **Expect:** with five, the form is replaced by "You have 5
  areas, the most you can save. Remove one to add another."
- [ ] **6.7** Click "Remove" on one of the three and confirm "Remove your area …?". Remove the other
  two. **Expect:** the form is back, and Morges and Nyon remain.
- [ ] **6.8** Open "Your drives". **Expect:** under "Ride requests in your areas": "No open ride
  requests in your areas right now."

**A second driver**

- [ ] **6.9** In a private window, sign in with the third address as "Daniel Dubois". **Expect:**
  the name shown is "Daniel D. 2", because "Daniel D." is taken. This is D2. Switch to driver mode
  and add the area Morges, 10 km.

**Asking drivers: the limits**

- [ ] **6.10** As P, search Lausanne → Renens VD without choosing a time and click "Ask drivers for
  this ride". **Expect:** "A ride request must be at least 15 minutes ahead."
- [ ] **6.11** Choose a date 15 days ahead and ask. **Expect:** "A ride request can be at most 14
  days ahead."
- [ ] **6.12** Search Morges → Zürich, "Arrive by" 30 minutes from now, and ask. **Expect:** "The
  drive there takes too long to arrive by then. Choose a later time."
- [ ] **6.13** Search with the same town in From and To, for tomorrow, and ask. **Expect:** "The
  start and the destination are the same place."

**A request to a driver who made the trip**

The drive of step 5.1 must have left by now.

- [ ] **6.14** As P, search Lausanne → Renens VD, "Leave at" the day after tomorrow, at the time of
  day the drive of step 5.1 left. Click "Ask drivers for this ride". **Expect:** "Request sent: 1
  driver who makes this trip or drives around there was told. Your inbox tells you when a drive that
  fits is posted." A section "Your ride requests" lists it with "Cancel request".
- [ ] **6.15** As D, open the inbox. **Expect:** "Paula P. is looking for a ride", the trip and its
  time, "You drove Lausanne → Renens VD around hh:mm once recently.", "Click a request to see it on
  the map.", and the buttons "Accept this ride", "Offer this drive", "Not this time" and "Not
  interested in this route".
- [ ] **6.16** Click the card, away from its buttons. **Expect:** the map shows Paula's start and
  destination joined by a dashed line, your usual route, and on it "Pickup ~hh:mm" and "Drop-off
  ~hh:mm".
- [ ] **6.17** Click "Accept this ride". **Expect:** "You'll make your usual drive Lausanne → Renens
  VD, leaving at …, and pick Paula P. up on the way…", "Seats you offer in all" set to 3, a message
  field, "Confirm" and "Not now". "Not now" brings the buttons back.
- [ ] **6.18** Click "Accept this ride" again, add a message, click "Confirm". **Expect:** "Done:
  it's in your drives, and Paula P. has been told." "See your drives" shows the new drive with Paula
  P. "Booked" on it.
- [ ] **6.19** As P, open the inbox. **Expect:** "Daniel D. will drive you". "See your bookings"
  shows the booking as "Booked" with the driver's message. The request has left "Your ride
  requests".

**A request to a driver by area**

- [ ] **6.20** As P, search Morges → Nyon, "Leave at" the day after tomorrow 08:00, and ask. Then
  the same trip at 09:00 the same day, and ask. **Expect:** each time "Request sent: 1 driver…". D
  was already told once today, so the driver told is D2.
- [ ] **6.21** As D2, open the inbox. **Expect:** one card, "2 passengers are looking for a ride",
  with "It starts in your area Morges." Clicking the card shows the start and destination joined by
  a dashed line, with no route of yours.
- [ ] **6.22** Click "Offer this drive". **Expect:** "Your drives" opens with From, To and the
  departure filled in, and "Paula P.'s trip is filled in below. Change the start and destination to
  your own if you like…". Click "Offer drive". Back in the inbox the card reads "You offered a
  drive."
- [ ] **6.23** As P, open the inbox. **Expect:** "A drive that fits your request was posted", with
  the request and "Daniel D. 2 · Morges → Nyon" and its time. "See the drives" runs the search for
  that request, and D2's drive is among the results.
- [ ] **6.24** As D2, cancel that drive. **Expect:** P's inbox says "A drive that fit your request
  was cancelled", with "See the drives".

**The list of requests in a driver's areas**

- [ ] **6.25** As D, open "Your drives". **Expect:** under "Ride requests in your areas", Paula's
  two Morges → Nyon requests, the soonest first: the route, "Paula P." and the time, "It starts and
  ends in your areas.", "Accept this ride" and "Offer this drive".
- [ ] **6.26** As P, ask for Morges → Bern, "Arrive by" 09:00 in three days. As D, reload.
  **Expect:** it is listed with "arrive by …" and "It starts in your area Morges."
- [ ] **6.27** Use the two lists above the requests. **Expect:** choosing the area Nyon hides the
  Bern request. Choosing a day shows that day's only. A choice with nothing shows "No ride requests
  for this choice."
- [ ] **6.28** Click a request's card. **Expect:** the map shows its start and destination joined by
  a dashed line.
- [ ] **6.29** Click "Offer this drive" on the Bern request. **Expect:** the form is filled, with a
  departure early enough to arrive by 09:00. Do not post it.
- [ ] **6.30** As D and as D2, open "Accept this ride" on the Bern request. D clicks "Confirm"
  first, then D2. **Expect:** D gets "Done…". D2 gets "This ride request is no longer open: another
  driver took it, or it was cancelled." After a reload the request is in neither list.
- [ ] **6.31** As P, click a card under "Your ride requests", then "Cancel request" on one.
  **Expect:** the click runs the search with the request's trip and time. The cancelled request
  leaves P's list and, after a reload, D's.
- [ ] **6.32** As D in passenger mode, ask for Morges → Nyon in three days. Back in driver mode,
  open "Your drives". **Expect:** your own request is not in the list. Cancel it.

**On later days**

- [ ] **6.33** The next day: as P, ask for Morges → Nyon on a new date. As the driver told, click
  "Not this time". **Expect:** the card reads "You declined this time."
- [ ] **6.34** Another day: a new request on the same trip, and "Not interested in this route".
  **Expect:** "You won't hear about this route again." A later request on that trip gives that
  driver no inbox card, though the list on "Your drives" still shows it.

## Part 7. Inbox and emails

Parts 5 and 6 already filled three inboxes and three mailboxes. This part checks what was sent, and
the switches.

**The inbox**

- [ ] **7.1** Watch the "Inbox" tab while the other account does something that concerns you.
  **Expect:** the count appears within a minute without a reload, in passenger mode and in driver
  mode. Opening the inbox clears it. Cards not seen before stand out from the others.

**The emails**

- [ ] **7.2** Go through the mailboxes of P, D and D2. **Expect:** an email for each inbox card that
  was still unread a minute after it was made. Its subject is the card's sentence, for example
  "Paula P. asks for a seat on your drive", "Daniel D. accepted your request for a seat", "Paula P.
  is looking for a ride you could offer", "A drive that fits your ride request was posted" or
  "Daniel D. will drive you". A card you read in the app within that minute has no email.
- [ ] **7.3** Open one. **Expect:** "Hello,", the sentence, the trip with its time, a button "Open
  your inbox", and at the bottom "You get these emails when something on Ride and Rail needs your
  attention. Turn these emails off".
- [ ] **7.4** Compare the languages. **Expect:** D's emails are in French, because D last signed in
  from a French page (step 3.11). P's and D2's are in English.
- [ ] **7.5** Click "Open your inbox" in an email. **Expect:** the site opens on the inbox in the
  email's language. Signed out, it shows the sign-in page.

**Turning the emails off and on**

- [ ] **7.6** In one of D2's emails, click "Turn these emails off". **Expect:** a page "Notification
  emails" that asks "Stop the emails Ride and Rail sends you about your rides?" with a button.
  Nothing has changed yet: in D2's inbox, "Also send me my notifications by email" is still ticked.
- [ ] **7.7** Click the button. **Expect:** "Emails turned off", an explanation and a link "Open
  Ride and Rail". It works without being signed in. In D2's inbox the checkbox is now unticked.
- [ ] **7.8** As D2, offer Morges → Lausanne, tomorrow 18:00. As P, ask for a seat on it.
  **Expect:** D2's inbox gets the card, and no email comes within three minutes.
- [ ] **7.9** As D2, tick the checkbox again. As P, withdraw the request and ask again. **Expect:**
  this time the email arrives.
- [ ] **7.10** Open <https://rideandrail.ch/unsubscribe/abc>. **Expect:** the same page as for a
  real link, and the same "Emails turned off" after the button. No error, nothing to learn from
  guessing.
- [ ] **7.11** As D2, untick "Tell me when a passenger asks for a ride on a route I've driven or in
  one of my areas" and reload. **Expect:** it stays unticked. To see its effect, leave it unticked
  until tomorrow: a new request by P from Morges then gives D2 no inbox card. Tick it again
  afterwards.

**Quiet hours, in the evening**

- [ ] **7.12** After 22:00 Swiss time: as P, ask for a seat on one of D's drives. **Expect:** D's
  email arrives within two minutes, at night too.
- [ ] **7.13** After 22:00, on a day D was not yet told about a request: as P, ask drivers for a
  ride D would be told about. **Expect:** D's inbox has the card at once, and the email arrives
  after 07:00.

## Part 8. Profiles and photos

If the account page shows no "Photo" part, photos are switched off on the server: note it and skip
the photo steps.

**Your own profile**

- [ ] **8.1** As P in passenger mode, click your name. **Expect:** "Your profile" with a note that
  everything is optional, "Photo" with "Choose a photo", "About you", and five habits: Talkative,
  Likes to sleep in the car, Smoker, Travels with a pet, Likes music in the car. Each offers "Rather
  not say", "Yes" and "No". No "Your car" in passenger mode.
- [ ] **8.2** Write a few words in "About you", set Talkative to Yes and Smoker to No, click "Save
  profile". **Expect:** "Profile saved." After a reload everything is kept. The text field stops at
  500 characters.
- [ ] **8.3** Click "Choose a photo" and pick the large photo. **Expect:** it shows within a few
  seconds, cut to a centred square, and "Remove the photo" appears.
- [ ] **8.4** Choose the file that is not an image. **Expect:** "This image couldn't be read. Try
  another one." The photo stays.
- [ ] **8.5** Choose another photo, then click "Remove the photo" and reload. **Expect:** the new
  one replaces the old, then none is shown, after the reload too. Add a photo again.
- [ ] **8.6** As D in driver mode, open the account page. **Expect:** "Your car" with Make, Model,
  Year and Colour. Enter a car with the year 1900 and save: refused. With 2019: "Profile saved." Add
  a photo and a few words too.
- [ ] **8.7** As D, switch to passenger mode, change "About you" and save. Switch back to driver
  mode. **Expect:** the car is still there.

**Who sees whose profile**

- [ ] **8.8** As P, search Lausanne → Bern, tomorrow 08:00. **Expect:** Daniel D.'s card shows his
  photo in the circle. Demo cards show a letter and have no "See profile".
- [ ] **8.9** Open the card's details and click "See profile". **Expect:** a popup with the photo,
  "Daniel D.", his words, only the habits he answered, and "Car: …". No email address. OK or a click
  beside it closes it.
- [ ] **8.10** As P, look at a booking under "Your bookings". **Expect:** "See profile" opens the
  driver's profile.
- [ ] **8.11** As D, open "Your drives" and click "See profile" on Paula's row under "Passengers and
  requests". **Expect:** Paula's photo, words and the two habits she answered. No car, no email
  address.
- [ ] **8.12** As D, look at the request card in the inbox (step 6.15) and at the list of requests
  on "Your drives". **Expect:** "See profile" is on the inbox card. The list shows names only.
- [ ] **8.13** As P, search Morges → Lausanne, tomorrow 18:00, and click "See profile" on D2's
  drive. **Expect:** "Daniel D. 2 hasn't added anything to their profile yet."
- [ ] **8.14** In a private window, signed out, run the search of step 8.8. **Expect:** no name, no
  photo and no "See profile" on any card.
- [ ] **8.15** As P, open Daniel D.'s photo in a new tab and copy its address. Paste it in the
  signed-out private window. **Expect:** the photo shows for P and is refused when signed out.

## Part 9. Ratings and reports

The ride of step 5.1 is over. The app asks for ratings once an hour, so the question arrives up to
an hour after the drop-off time.

**Ratings**

- [ ] **9.1** As P, open the inbox. **Expect:** "How was your ride with Daniel D.?", the trip, five
  buttons from one to five stars, and "Report a problem". As D: "How was Paula P. as a passenger?".
  Each also gets the question by email, after 07:00 if it was made at night.
- [ ] **9.2** As P, click the fourth button. **Expect:** "You rated this ride. Thank you." After a
  reload the stars are not offered again.
- [ ] **9.3** As D, click the fifth button. **Expect:** the same.
- [ ] **9.4** As P, search Lausanne → Bern, tomorrow 08:00. **Expect:** "★ 4.0 (1)" next to "Daniel
  D.". Hovering it explains: the average of the stars passengers gave this driver, and how many
  ratings. The same badge is on P's bookings with D.
- [ ] **9.5** In a private window, signed out, run the same search. **Expect:** the rating shows,
  the name does not.
- [ ] **9.6** As D, open "Your drives". **Expect:** "★ 5.0 (1)" next to "Paula P." under "Passengers
  and requests".

**Reports**

- [ ] **9.7** As P, click "Report a problem" on a booking under "Your bookings". **Expect:** "What
  went wrong?", five reasons (Didn't show up, Very late, Unsafe driving, Inappropriate behaviour,
  Something else), a field "What happened? (optional)", "Only the Ride and Rail team reads this.
  Daniel D. isn't told.", "Send report" and "Not now". "Not now" brings the link back.
- [ ] **9.8** Choose "Very late", write "Test report, please ignore", click "Send report".
  **Expect:** "Thank you. Your report has reached the team." D gets no inbox card and no email about
  it.
- [ ] **9.9** Reload and report the same booking again. **Expect:** "You have already reported this
  ride."
- [ ] **9.10** As D, click "Report a problem" on Paula's row of the same booking and send one.
  **Expect:** accepted: each side can report once.
- [ ] **9.11** Look at the rating card in the inbox. **Expect:** it has "Report a problem" too.
- [ ] **9.12** On the server: `docker compose exec app python backend/admin.py reports`. **Expect:**
  both test reports are listed with who reported whom, the reason, the words and the ride.
  `close-report <number> "test"` closes each. If a reports address is set, an email arrived there
  for each.

## Part 10. Installed app, offline and updates

On the phone.

- [ ] **10.1** Android, Chrome: open the site and choose "Install app" or "Add to Home screen" in
  the menu. **Expect:** an icon with the logo and the name "Ride & Rail". It opens full screen,
  without an address bar, in the site's teal.
- [ ] **10.2** iPhone, Safari: Share, then "Add to Home Screen". **Expect:** the logo as icon and
  the name "Ride and Rail". It opens full screen.
- [ ] **10.3** Android: in the installed app, ask for a sign-in link, open it from the mail app,
  come back to the installed app. **Expect:** the app is signed in when it comes back to the front.
- [ ] **10.4** iPhone: in the installed app, ask for a sign-in link, read the email in Mail, come
  back to the app and type the email's code. **Expect:** signed in inside the app. The link itself
  opens in Safari and signs Safari in, not the app: that is what the code is for.
- [ ] **10.5** With the app open, turn on airplane mode. **Expect:** a notice at the bottom: "You're
  offline. Finding and offering rides needs an internet connection." A search answers "You're
  offline. Please check your connection and try again." Back online, the notice goes.
- [ ] **10.6** Close the app, stay in airplane mode, open it again. **Expect:** the app opens with
  the offline notice, not a browser error page.
- [ ] **10.7** Still offline, open <https://rideandrail.ch/anything> in the browser. **Expect:** a
  page "You're offline" in your language with "Try again".
- [ ] **10.8** On a computer, open <https://rideandrail.ch/sw.js>, `/manifest.webmanifest` and
  `/.well-known/assetlinks.json`. **Expect:** a script, the app's description with three icons, and
  a JSON list (empty until the Android app's fingerprint is set).
- [ ] **10.9** At the next deploy that changes the site: keep a tab open before it, look at it again
  afterwards. **Expect:** "A new version of Ride and Rail is available." with "Reload", which loads
  the new version.
- [ ] **10.10** Once the Android app is in testing on Google Play: install and open it. **Expect:**
  the site full screen, with no address bar at the top.

## Part 11. Screen sizes and browsers

**Layouts**

- [ ] **11.1** Computer, window 980 px wide or more, passenger mode. **Expect:** From, To and the
  time in one bar, wider than the centred column of results.
- [ ] **11.2** Narrow the window below 980 px. **Expect:** a card with From and To. The time appears
  once both are set.
- [ ] **11.3** Computer, driver mode. **Expect:** the map beside the page at all times.
- [ ] **11.4** Phone, passenger mode: "Pick on map", then "Show on map" on a ride. **Expect:** the
  map takes the whole screen, "Close the map" is within reach, and after "Use this place" the page
  is back at the field.
- [ ] **11.5** Phone, driver mode. **Expect:** the map is a band above the form. "Pick on map"
  scrolls up to it, and after choosing a place the page returns to the field.
- [ ] **11.6** Phone: type in From with the keyboard open. **Expect:** the suggestions stay visible
  above the keyboard, and a tap takes one.
- [ ] **11.7** Phone, 360 px wide, in DE, FR, IT and EN: the header, the tabs with the mode button
  and your name, a result card, a booking, an inbox card. **Expect:** nothing cut off, nothing
  overlapping, no sideways scrolling.
- [ ] **11.8** Phone: run the main path once. As P search, open a card, ask for a seat. As D open
  the inbox, answer, offer a drive. **Expect:** every button can be tapped without zooming.
- [ ] **11.9** Computer, keyboard only: Tab through the passenger page, choose a place from the
  list, open a card's "Details", close the map and a popup with Escape. **Expect:** all of it works
  without the mouse, and the focus is always visible.
- [ ] **11.10** Browser zoom at 200 %. **Expect:** the pages stay readable and usable.
- [ ] **11.11** Look at dates, times and prices in each language. **Expect:** Swiss formats: 24-hour
  times, day before month, "CHF 12.30".

**Browsers**

- [ ] **11.12** In Chrome, Firefox, Edge and Safari on a computer, Chrome on Android and Safari on
  iPhone: repeat steps 2.5 (search), 2.20 (pick on the map), 4.13 (offer a drive) and 5.2 to 5.3
  (ask for a seat). **Expect:** the same in each.

## Part 12. Access rules and limits

Some steps type an address or a line in the browser's console (F12). For the sign-in limits, use
only `+` variants of your own address: an invented address makes the email bounce, which hurts the
site's sender reputation.

**What each visitor may read**

- [ ] **12.1** Open <https://rideandrail.ch/api/health>. **Expect:** `{"status":"ok", …}`.
- [ ] **12.2** Signed out, open `/api/drives?limit=5`. **Expect:** five drives with `"driver_name":
  null` and `"driver_id": null`.
- [ ] **12.3** Signed out, open `/api/bookings`, `/api/notifications`, `/api/profile`,
  `/api/requests` and `/api/areas`. **Expect:** each answers 401 with the code `not_signed_in`.
- [ ] **12.4** As P, open `/api/drives?mine=true`. **Expect:** P's own drives only: an empty list.
- [ ] **12.5** As P, note the `id` of one of D's drives in `/api/drives?limit=500`, then run in the
  console, with that id: `fetch('/api/drives/ID', {method: 'DELETE'}).then(r =>
  r.json()).then(console.log)`. **Expect:** the code `not_your_drive`, and the drive is still there.
- [ ] **12.6** As P, on any other website, run in the console:
  `fetch('https://rideandrail.ch/api/auth/sign_out', {method: 'POST', credentials: 'include'})`.
  **Expect:** it fails, and P is still signed in on the site.
- [ ] **12.7** As P, open the browser's developer tools and look at the cookies. **Expect:** one
  cookie `__Host-session`, marked HttpOnly and Secure, SameSite Lax, expiring in about 90 days.

**An account that is not finished**

- [ ] **12.8** In a private window, sign in with a new `+` address and stop at "Welcome to Ride and
  Rail". Open `#/drive` and `#/inbox`. **Expect:** the welcome page each time. The cards of a search
  still show "A driver" instead of names.
- [ ] **12.9** On the welcome page, click "Delete your account", then "Delete my account", and
  confirm. **Expect:** the sign-in page with "Your account and your data have been deleted."

**Sign-in limits, at the very end of the session**

- [ ] **12.10** Ask for a sign-in link for the same `+` address four times within 15 minutes.
  **Expect:** the page says the same each time, and only three emails arrive.
- [ ] **12.11** Optional. It blocks sign-in links from your network for 15 minutes: ask for links
  for eleven different `+` addresses in a row. **Expect:** the eleventh answers "Too many requests
  from here. Please try again later."

**A closed account, on the server**

- [ ] **12.12** `docker compose exec app python backend/admin.py ban <D2's address>`. **Expect:**
  D2's open window is signed out at the next click. Asking for a link says it was sent, and no email
  arrives. An older link answers "This account is closed." Then `unban`.

**Not covered by hand.** These take days or a deploy, and are left to the automated tests: data
deleted after 30 days, the second round of drivers told after 12 hours, the limit of three requests
a week, the pause after five unanswered requests with "Turn back on", an email that fails three
times, and the load test.

## Part 13. Deleting the accounts

Last, because it removes everything the other parts made. At this point P has seats on several of
D's drives and a request for a seat on D2's drive.

- [ ] **13.1** As D2, open the account page, click "Delete my account" and choose Cancel.
  **Expect:** the question reads "Delete your account and all your data? This can't be undone.", and
  nothing is deleted.
- [ ] **13.2** As D, click "Delete my account" and confirm. **Expect:** the sign-in page with "Your
  account and your data have been deleted.", and "Sign in" in the header.
- [ ] **13.3** As P, open the inbox and "Find a ride". **Expect:** "Daniel D. cancelled the drive
  you had a seat on" for each of D's upcoming drives P was booked on. Those bookings are gone from
  "Your bookings", and no search finds D's drives.
- [ ] **13.4** As D2, look at Paula's row on your drive. **Expect:** her "★ 5.0 (1)" is still there:
  a rating outlives the account that gave it.
- [ ] **13.5** Sign in again with D's address. **Expect:** the welcome step, as for a new account.
  After it: no drives, no areas, an empty profile and an empty inbox. Delete this account too.
- [ ] **13.6** As P, delete the account. **Expect:** the sign-in page with the same message. Opening
  "Find a ride" shows empty fields.
- [ ] **13.7** As D2, open the inbox and "Your drives". **Expect:** a card says Paula withdrew her
  request for a seat. The cards about her ride requests are gone from the inbox, and her requests
  from the list in your areas.
- [ ] **13.8** Delete D2. In a private window, search Lausanne → Bern and Morges → Nyon. **Expect:**
  none of the test drives is found.
- [ ] **13.9** In an old email of a deleted account, click "Turn these emails off", then try its
  sign-in link. **Expect:** the same neutral page as in step 7.10, and "This sign-in link is
  invalid, has expired or was already used."
