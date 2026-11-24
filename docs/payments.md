# Payments

A passenger can pay for a seat through the site, with [Stripe](https://stripe.com). It is
**switched off** until Stripe's keys are set: without them nothing is charged, passenger and driver
settle between themselves, and the site says so. The logic is in
[backend/payments.py](../backend/payments.py), the calls to Stripe in
[backend/stripe_gateway.py](../backend/stripe_gateway.py), the routes in
[backend/routes/payments.py](../backend/routes/payments.py), the page's script in
[frontend/payments.js](../frontend/payments.js).

## How it goes

1. **A payment is due from the driver's acceptance.** When a driver accepts a
   [booking](booking.md), or takes a ride request directly, the passenger has a payment to make.
   Paying comes after the driver's answer, so nobody pays for a seat they don't get, and a driver
   who takes a ride request books a passenger who isn't there at that moment.
2. **Until when:** 24 hours after the acceptance (`payments.PAY_WITHIN`), and at the latest the
   pickup time. The booking shows *Pay CHF …* and the time to pay by; the driver sees *Waiting for
   payment*.
3. **Paying** happens on Stripe's Checkout page, in the passenger's language. The amount is the
   price per seat at that moment ([pricing rules](../backend/pricing.py)) times the seats. Which
   payment methods the page offers (cards, TWINT, Apple Pay, Google Pay…) is set in the Stripe
   dashboard, not in the code. The site never sees a card number.
4. **Paid:** Stripe calls the site's webhook, and the page the passenger comes back to asks Stripe
   as well, in case that call is late. The booking shows *Paid* to both, and the driver is told
   (`payment_received`).
5. **Not paid in time:** the booking is cancelled, its seats are free again, and both are told
   (the passenger: `booking_unpaid`; the driver: `booking_cancelled` with `reason: "unpaid"`).
   This runs hourly, with the rest of the [upkeep](demo-drives-and-data-retention.md); before
   giving a payment up, the site asks Stripe whether it was made after all.
6. **A booking that ends before the ride** ends its payment: cancelled by the passenger or the
   driver, the drive cancelled, an account deleted. Nothing is due any more, and what was paid is
   refunded in full (`payment_refunded` tells the passenger). A payment that arrives after the
   booking ended is refunded at once.

Bookings accepted while payments were off never get a payment: switching payments on changes
nothing for them.

The money stays in the Stripe account. Paying drivers out, the rules for late cancellations and a
service fee are separate steps, described here as they are added.

## What is kept

One row per booking in the `payments` table: the amounts (in centimes), Stripe's identifiers, the
status (`due`, `paid`, `cancelled`), and the trip it was for (start, destination, pickup and
drop-off time). No names. A payment outlives its booking and its drive, which are deleted 30 days
after the departure: it is the record of money that moved, kept for ten years
(`payments.RECORDS_KEPT`), as Swiss bookkeeping rules ask. When an account is deleted the row
stays, without the link to that account.

Refunds are made in two steps, so that none is lost or made twice: the amount to give back is
written down first (`refund_due_cts`), then Stripe is told, with an idempotency key; if that call
fails, the hourly run tries again.

## What users are told

The [About page](about-page.md) and the [privacy notice](../frontend/privacy.html) contain both
descriptions: "nothing is paid through Ride and Rail" and "you pay through Ride and Rail", with
Stripe named as the payment provider. The elements are marked `data-payments="off"` and
`data-payments="on"` (hidden), and [frontend/payment_texts.js](../frontend/payment_texts.js) shows
the ones that are true, from `GET /api/payments/config`. So the pages agree with what the site does
whichever way the switch is, without a change to them on the day it is turned on.

## Switching it on

Payments need a Stripe account, which needs a registered business. Until then everything can be
tried with a Stripe **test** account (test keys, test cards, no real money).

1. In the Stripe dashboard: switch on the payment methods to offer, and add a webhook endpoint
   `https://<site>/api/payments/webhook` for the events `checkout.session.completed` and
   `checkout.session.async_payment_succeeded`.
2. Set two repository **secrets**: `STRIPE_SECRET_KEY` (the secret API key) and
   `STRIPE_WEBHOOK_SECRET` (the endpoint's signing secret), then run the deploy again.
3. Check `GET /api/payments/config`: `{"enabled": true}`.

With only one of the two set, payments stay off and the log says which one is missing. Removing the
keys switches payments off again: nobody is asked to pay or cancelled for not paying, and refunds
that were due are sent once the keys are back.

Locally: `STRIPE_SECRET_KEY=sk_test_… STRIPE_WEBHOOK_SECRET=whsec_… uv run uvicorn main:app
--app-dir backend`, with `stripe listen --forward-to localhost:8000/api/payments/webhook` (the
Stripe CLI prints the signing secret). Without the CLI the flow still works, since the page asks
Stripe when the passenger comes back.

## Endpoints

Access levels and error codes as in [API](api.md).

- `GET  /api/payments/config` — public; `{enabled}`
- `POST /api/bookings/{id}/pay` — may change, the booking's passenger; `{url}`: the Checkout page
  to send the browser to. A page opened earlier for the booking is closed first. 503
  `payments_off`; 404 `booking_not_found` (also for someone else's booking); 400 `payment_not_due`
  (not accepted, already paid, or not paid through the site), `payment_too_late`; 502
  `payment_failed` (Stripe refused or couldn't be reached)
- `POST /api/bookings/{id}/pay/check` — may change, the booking's passenger; asks Stripe what
  became of the open Checkout page and answers `{status}`
- `POST /api/payments/webhook` — Stripe's calls, authenticated by their signature
  (`Stripe-Signature`), not by a session; 400 `invalid_signature`, 503 `payments_off`

`GET /api/bookings` gives each booking a `payment`: `null` when it isn't paid through the site,
else `{status, amount_chf, pay_by, refunded_chf}` (`amount_chf`: still to pay at the current price
while `due`, what was paid afterwards).

Stripe sends the passenger back to `/?paid=<booking id>#/ride` after paying and to
`/?unpaid=<booking id>#/ride` after leaving the page without paying.
