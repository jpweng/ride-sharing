# Train fares

[backend/fares.py](../backend/fares.py) gives the 2nd class full and half fare for a trip:

- **Always:** the national distance tariff, T601 (Alliance SwissPass, edition of 1 June 2026),
  whose price table it reproduces exactly (tested km by km up to 1000 km). The result is labelled
  `approximate: true` because the tariff distance is estimated: the road distance of the drive
  (rail and road distances between Swiss towns are usually close), or between two stations the
  straight line × 1.25. Fare networks' zone tickets, route prices and saver tickets differ. When
  the T601 changes (usually each December), update `T601_RATES_RP_PER_KM` and `T601_EDITION`.
- **With `OJP_API_KEY` set** (a key from the [opentransportdata.swiss](https://opentransportdata.swiss)
  API manager): the public-transport option of a search gets the real fare between its two
  stations, from an OJP 2.0 trip request and the (beta) OJPFare service, cached per station pair
  for a day. `OJP_FARE_API_KEY` overrides the key for the fare service, `OJP_TRIP_URL` and
  `OJP_FARE_URL` the endpoints. Any error or timeout falls back to the distance tariff. The
  deployed app doesn't pass this variable yet ([deploy/compose.yaml](../deploy/compose.yaml)).

Ride prices are capped with the distance tariff of the distance ridden, so every listed drive and
match is priced without a network call.
