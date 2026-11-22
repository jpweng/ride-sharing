## Decisions

- **Audience:** investors and real testers. For now the site only needs to be shown, but real
  drivers are coming soon, so nothing built now should assume the demo stays a demo.
- **Languages:** the three national languages (German, French, Italian) plus English.
- **Branding:** the logo is `frontend/icons/logo.svg`; the app icons are generated from it.

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
- **Translations:** German, French, Italian and English, with a language picker; English is the
  default when the browser's language isn't one of them.
- **Contact page:** `contact.html`, a form whose messages are emailed to the team (see
  `docs/contact.md`). The privacy notice and the code of conduct point to it; the privacy notice
  still lacks the name and address of who is responsible.

## Next steps, in order of priority

1. **Native review of the translations.** German, French and Italian were written without a native
   speaker's review. Have each checked before showing the site widely, the privacy notice above
   all. Visitors are addressed formally everywhere ("Sie", "vous", "Lei").

## Before real drivers

Not needed for the demo, but these shape decisions made now:

- **Accounts:** sign-in by an emailed link replaces the typed name (built, see docs/sign-in.md). Still to do
  by a human: SES production access, a lawyer's review of the code of conduct.
- **Contact and booking:** booking is built (see docs/booking.md): a passenger asks for a seat,
  the driver accepts or declines, and they agree on the meeting point with a short message each.
  Still open: how they reach each other on the day (no phone number or chat yet).
- **Legal:** terms of use and a full privacy policy. In Switzerland, carpooling is fine as long as
  drivers only share costs; earning money from rides needs a professional licence, so pricing rules
  matter.
- **Demo drives:** they're labelled "Demo" already. Decided: they move to demo.rideandrail.ch, and
  rideandrail.ch becomes the real site. Plan in `docs/demo-subdomain-plan.md` (not implemented
  yet).

## Open questions

1. **Name:** is "Ride and Rail" the final name?
2. **Deadline:** is there a date for an investor meeting or the start of testing?
3. **Testers:** how many, and do they get access by invitation, or is the site open to anyone?
4. **Privacy contact:** who is responsible for the data (a person or a company)? The Swiss data
   protection law requires the name and address in the privacy notice. The contact itself is the
   contact page.
