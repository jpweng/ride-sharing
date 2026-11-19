# Profiles

What a user chooses to say about themselves to those they may ride with: a photo, a few words, a
few habits and, for drivers, their car. [backend/profiles.py](../backend/profiles.py) has the
rules and [backend/photos.py](../backend/photos.py) the photo storage; in short:

## What a profile holds

Everything is optional, and an empty profile is fine.

- **About:** up to 500 characters of free text.
- **Five habits**, each *yes*, *no* or left open ("Rather not say"): talkative, likes to sleep in
  the car, smoker, travels with a pet, likes music in the car. One left open isn't shown.
- **The car** (drivers): make, model, year and colour. The form shows this part in driver mode
  only; saving in passenger mode keeps what was entered.
- **A photo**, see below.

To someone else, a profile shows the first name and last initial they already see, and what was
filled in. Never the email address.

## Who sees a profile

Nothing here is public, and signing in is not enough:

- **Oneself**, on the account page.
- **A passenger sees a driver's profile** while that driver is a candidate for a ride they want:
  the drivers a search of theirs found, for two days (`CANDIDATE_TTL`; the search records them,
  `profile_grants`), and the driver of a drive they asked for a seat on.
- **A driver sees a passenger's profile** once that passenger asked to ride with them: a
  [booking](booking.md) on one of their drives, whatever became of it, or a ride request of that
  passenger the driver was told about. Not before: the list of requests in a driver's areas shows
  names only.

Everyone else gets 404 `profile_not_found`, the same as for a profile that doesn't exist, as do
demo drivers (who have none) and banned accounts. *See profile* appears where the rules allow it:
on a search result, on a booking (both sides) and on a ride request in the inbox.

A profile, its photo and what others were allowed to see are deleted with the account. Expired
search permissions are removed by the hourly maintenance run.

## Photos

- **Where:** a private Amazon S3 bucket ([infra/photos.tf](../infra/photos.tf)), in the same
  Region as the server. Photos have no public address: the app reads them with the server's role
  and serves them through `/api/profiles/{id}/photo`, under the rules above.
- **What is sent:** the browser cuts the picture to a centred square, shrinks it to at most
  512 px and re-encodes it as JPEG before sending it, so the file carries no location or camera
  data. The server only accepts a JPEG of at most 400 kB.
- **Replacing and removing:** a new photo gets a new object and the old one is deleted; the
  bucket keeps no old versions.
- **Switch:** photos are on when `PHOTO_BUCKET` is set (the bucket's name; `PHOTO_REGION` when it
  isn't in eu-north-1). Without it the profile page doesn't offer a photo, and everything else
  works. For development, `PHOTO_DIR=/some/folder` keeps them in a folder instead.

### Turning photos on in production

1. `terraform apply` in `infra/` (see [Deploying to AWS](deploying-to-aws.md)): creates the
   bucket and lets the server's role read, write and delete objects under `profiles/`.
2. Set the repository variable from the `github_actions_variables` output:
   `gh variable set PHOTO_BUCKET --body <name>`.
3. Deploy (push to `main`, or re-run the *Test and deploy* workflow).

If the storage fails, adding a photo answers 503 `photos_unavailable` and a profile is shown
without its photo.

In the browser it is [frontend/profiles.js](../frontend/profiles.js): the form on the account
page, and the *See profile* links added through `CARD_EXTRAS`, `BOOKING_EXTRAS` and
`REQUEST_EXTRAS`.

## Endpoints

Access levels and error codes as in [API](api.md).

- `GET /api/profile` — signed in; one's own profile, with `photos_enabled`
- `PUT /api/profile` `{about, talkative, sleeps, smoker, pets, music, car_make, car_model,
  car_year, car_color}` — may change; replaces the profile (a field left out or `null` is
  cleared), the photo is kept. 422 for a text that is too long or a year outside 1950–2100
- `PUT /api/profile/photo` (the JPEG as the request body) — may change. 400 `photo_invalid`,
  413 `photo_too_large`, 503 `photos_unavailable`
- `DELETE /api/profile/photo` — may change
- `GET /api/profiles/{user_id}` — signed in; someone else's profile when the rules above allow
  it. 404 `profile_not_found` otherwise
- `GET /api/profiles/{user_id}/photo` — the same rule; the JPEG, cached privately for an hour

Search results (`driver_id`), bookings (`passenger_id`, and `driver_id` in their drive) and ride
requests (`passenger_id`) carry the ids these take.
