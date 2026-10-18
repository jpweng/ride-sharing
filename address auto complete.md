# Address autocomplete / geocoding options (for place input, e.g. "Bern → Zurich")

Research note for the ride-sharing app's location input fields (proposing/requesting a ride from a
specific place). The requirement: as the user types, return candidate addresses/places; once selected,
resolve to coordinates (lat/lon) to feed into ride matching and, eventually, into public-transport
trip-planning integration.

## Option 1: Google Places Autocomplete (New)

- Very polished typeahead, huge global coverage, good fuzzy/typo matching, handles cross-border addresses
  natively (e.g. Basel-area German streets, Geneva-area French streets).
- Session-based billing: all keystroke requests during one typing session are bundled under a session
  token, terminated by a Place Details (or Address Validation) call that resolves the choice to
  coordinates.
- Pricing (2026): ~$2.83 per 1,000 Autocomplete sessions; Place Details "Essentials" tier (geometry only,
  cheaper) or fuller tiers if more fields are needed (~$5/1,000 at low volume, decreasing at scale).
  ~10,000 free requests/month across these SKUs.
- Requires a Google Cloud billing account + API key management, and Google Maps Platform ToS constraints
  apply to how results can be displayed (e.g. pairing with a Google basemap in some flows).

Docs: [Autocomplete (New) session pricing](https://developers.google.com/maps/documentation/places/web-service/session-pricing),
[Places API usage & billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing),
[Google Maps Platform pricing](https://developers.google.com/maps/billing-and-pricing/pricing)

## Option 2: geo.admin.ch Search API (Swiss federal geoportal) — chosen for MVP

- Official, free, no API key required. Built on the same authoritative Swiss data (SwissNames, the
  federal building address register / BADAC) as the rest of the Swiss geo-data ecosystem.
- Supports location search across addresses, communes, geographic names — suitable for
  address-autocomplete-style typeahead.
- Switzerland-only coverage — no matches just across the border (Annemasse near Geneva, Lörrach near
  Basel, etc.), which is a real gap for carpooling in border regions.
- Zero cost at any volume, no vendor billing risk, keeps address queries (personal location data) on
  Swiss federal infrastructure rather than a US provider — relevant for nLPD/data-residency posture.
- **Verified live** (2026-09-21, from this environment): `GET
  https://api3.geo.admin.ch/rest/services/api/SearchServer?searchText=Bahnhofstrasse+Bern&type=locations&origins=address&limit=8&sr=4326`
  returns `results[].attrs` with `label` (HTML, e.g. `"Südbahnhofstrasse 2 <b>3007 Bern</b>"`), `lat`,
  `lon` (plain floats, WGS84, present regardless of the `sr` param — no reprojection needed), and
  `detail`. CORS is wide open (`access-control-allow-origin: *`), so it can be called directly from the
  browser, the same way this app already calls Nominatim for reverse geocoding in `app.js`.

Docs: [Search | *.geo.admin.ch](https://docs.geo.admin.ch/access-data/search.html),
[geo.admin.ch API overview](https://www.geo.admin.ch/en/programming-interface-api)

## Option 3: Mapbox Search Box / Geocoding API

- Middle ground: covers Switzerland and neighboring countries (handles cross-border addresses), generally
  cheaper than Google, generous free tier, less restrictive ToS (no requirement to pair with a specific
  basemap).
- Reasonable choice if cross-border coverage matters but Google lock-in is undesirable.

## Option 4: Self-hosted OSM (Photon / Nominatim)

- Zero marginal cost, full control, no vendor dependency — but requires hosting and maintaining the
  search index yourself.
- The public Nominatim instance's usage policy explicitly forbids autocomplete-as-you-type traffic at
  real volume; only viable self-hosted.
- Swiss OSM address coverage is decent but not as authoritative as the federal registry.

## Recommendation

Start the MVP on **geo.admin.ch**: free, no setup/auth, official Swiss address data, and a strong fit for
a Swiss-first app (data-residency angle included). Bring in Mapbox (or Google, if the polish/global
coverage is worth the cost and ToS constraints) later if/when cross-border addresses near the
French/German/Italian/Austrian borders or a more polished typeahead widget become a real requirement —
this would layer on top of, not replace, geo.admin.ch for domestic addresses.

## Implementation (done)

Added client-side address autocomplete to the existing "place name" text fields (origin/destination, in
both "Post a Drive" and "Request a Ride"), as an addition alongside the existing "Pick on map" flow — not
a replacement, since map-click stays useful for spots without a street address (a trailhead, a parking
lot, a village square).

- `frontend/app.js`: `searchGeoAdmin(query)` calls the geo.admin.ch SearchServer directly from the
  browser (no backend proxy needed, same pattern as the existing Nominatim reverse-geocode call), debounced
  (300ms, 3-char minimum). Fails soft — an empty result list on network error, so "Pick on map" is always
  a working fallback. `setupAutocomplete(role)` wires a dropdown (mouse + arrow-key/Enter selection) under
  each of the four place-name inputs; selecting a suggestion sets the point (and its map marker) exactly
  like a map click does, via the existing `setPointCoords`.
- `frontend/index.html`: each place-name input is now wrapped in `.autocomplete`, with a `<ul
  class="autocomplete-list">` for suggestions.
- `frontend/style.css`: `.autocomplete` / `.autocomplete-list` styles (absolute-positioned dropdown,
  active-item highlight).

Not yet done / left as follow-ups if this grows past prototype stage: caching repeated queries, a backend
proxy (would only be needed for rate-limiting or hiding the upstream host, not for CORS or auth — none of
which this API requires), and cross-border coverage via Option 3 if requested.

### Bug fix: place names (e.g. a bare city name) resolving to the wrong address

`origins=address` only searches the building-address register, which has no entry for a city/town as a
whole — so typing e.g. "Bern" returned obscure street addresses that happen to contain "Bern" in their
street name (such as "Berne 210, 9064 Hundwil", ~150 km from the city of Bern) instead of the city itself,
and picking the top suggestion silently sent the wrong coordinates. Fixed by searching
`origins=address,gg25` — `gg25` is the SwissNAMES3D place-name layer (cities, villages, hamlets), and
geo.admin.ch weights those matches far higher than a street-name substring hit, so the city itself now
sorts first while full street addresses still resolve correctly.
