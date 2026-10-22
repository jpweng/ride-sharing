## Decisions

- **Audience:** investors and real testers. For now the site only needs to be shown, but real
  drivers are coming soon, so nothing built now should assume the demo stays a demo.
- **Languages:** the three national languages (German, French, Italian) plus English.
- **Branding:** no logo yet.

## Done

- **Demo drives:** a rolling week of fictional drives, refreshed hourly; visitors' drives are kept.
- **Data retention:** visitors' drives, searches and names are deleted 30 days after they're last
  useful.
- **Demo notices:** a banner on every page, a "Demo" badge on fictional drives, a warning to use a
  made-up name when signing in, a privacy notice (`privacy.html`) and `noindex`. The privacy notice
  still lacks who is responsible for the data and how to contact them; see open questions.
- **Ride and Rail identity:** the name everywhere, a text wordmark, a teal palette instead of the
  SBB-inspired red, and a home page explaining the concept in three steps.
- **Uptime alerts:** a health check every 5 minutes, with an email when the site is down and when
  it's back.

## Next steps, in order of priority

1. **Translate into German, French, Italian and English.** Doing it now, while the frontend is small
   (about 730 lines of JavaScript), is much cheaper than later. Plain JavaScript with one dictionary
   per language and a language picker, defaulting to the browser's language. Error messages from the
   API that reach the user need translating too.

## Before real drivers

Not needed for the demo, but these shape decisions made now:

- **Accounts:** a real sign-in (e.g. a link sent by email) instead of a typed name, so nobody can
  post or read as someone else.
- **Contact and booking:** how a passenger reaches a driver and reserves a seat.
- **Legal:** terms of use and a full privacy policy. In Switzerland, carpooling is fine as long as
  drivers only share costs; earning money from rides needs a professional licence, so pricing rules
  matter.
- **Demo drives:** they're labelled "Demo" already; decide whether to switch them off once real
  drives exist.

## Open questions

1. **Name:** is "Ride and Rail" the final name?
2. **Deadline:** is there a date for an investor meeting or the start of testing?
3. **Testers:** how many, and do they get access by invitation, or is the site open to anyone?
4. **Privacy contact:** who is responsible for the data (a person or a company), and which contact
   to publish? The Swiss data protection law requires both in the privacy notice.
