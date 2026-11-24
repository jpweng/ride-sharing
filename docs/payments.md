# Payments

A passenger can pay for a seat through the site, with [Stripe](https://stripe.com). It is
**switched off** until Stripe's keys are set: without them nothing is charged, passenger and driver
settle between themselves, and the site says so. The logic is in
[backend/payments.py](../backend/payments.py) (passengers paying) and
[backend/payouts.py](../backend/payouts.py) (drivers being paid), the calls to Stripe in
[backend/stripe_gateway.py](../backend/stripe_gateway.py), the routes in
[backend/routes/payments.py](../backend/routes/payments.py) and
[backend/routes/payouts.py](../backend/routes/payouts.py), the page's scripts in
[frontend/payments.js](../frontend/payments.js) and [frontend/payouts.js](../frontend/payouts.js).

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

The rules for late cancellations and a service fee are separate steps, described here as they are
added.

## Paying drivers

What passengers pay arrives in the site's Stripe account, and is moved from there to each driver's
own account at Stripe (Stripe Connect, "separate charges and transfers"), which Stripe pays out to
the driver's bank account. The site never holds the money itself.

- **Setting payouts up, once.** The Driver page has a section *Getting paid*, shown while payments
  are on. *Set up payouts* opens an Express account at Stripe for the driver and sends them to
  Stripe's own pages, where they give their identity and their bank account. The site keeps only
  the account's identifier and whether Stripe accepted it (`payout_accounts`); it never sees the
  identity document or the IBAN. Back on the Driver page, the site asks Stripe how far the account
  is: *none*, *incomplete* (not finished, or Stripe still checking), *ready*.
- **Drivers can post and accept without it.** The money waits. A driver with money waiting and no
  account that is ready is told so (`payout_setup`), at most once a week
  (`payouts.REMIND_EVERY`).
- **When the money moves:** 24 hours after the ride's drop-off (`payouts.PAY_OUT_AFTER`), hourly,
  what the passenger paid less what was refunded is transferred to the driver's account, tied to
  the passenger's payment, and the driver is told (`payout_sent`). The wait leaves time to hear
  that a ride didn't take place. Stripe pays the account's balance out to the bank account once a
  week (set on the account when it is created).
- **Money that can't be sent:** 60 days after the ride without an account that is ready
  (`payouts.UNCLAIMED_AFTER`), or when the driver has deleted their account, the payment is
  refunded to the passenger (`payout_lapsed` tells the driver). The site doesn't keep it.
- **A driver deleting their account** is first sent what the passengers of their finished rides
  paid, without the 24 hours' wait, if their payouts are set up. The link to their account at
  Stripe is deleted here; the account at Stripe stays theirs.
- **After the ride, a deleted drive changes nothing:** only bookings whose pickup is still ahead
  are refunded when a drive is cancelled.

A transfer is asked of Stripe with an idempotency key naming the payment, like refunds: asked
twice, it is made once; a failed one is tried again at the next hourly run.

## What is kept

One row per booking in the `payments` table: the amounts (in centimes), Stripe's identifiers, the
status (`due`, `paid`, `cancelled`), what was sent to the driver, and the trip it was for (start,
destination, pickup and drop-off time). No names. A payment outlives its booking and its drive, which are deleted 30 days
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

1. In the Stripe dashboard: switch on Connect (Express accounts, in Switzerland), the payment
   methods to offer, and add a webhook endpoint
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

Payouts:

- `GET  /api/payouts` — signed in; `{enabled, state, waiting_chf, sent_chf}`: `state` is `none`,
  `incomplete` or `ready`; `waiting_chf` is what the driver's passengers paid that hasn't been
  sent yet, `sent_chf` what was
- `POST /api/payouts/onboard` — may change; `{url}`: Stripe's pages to set payouts up or carry
  on, valid a few minutes. 503 `payments_off`; 502 `payout_failed`
- `POST /api/payouts/refresh` — may change; asks Stripe how far the account is, and answers as
  `GET /api/payouts`
- `POST /api/payouts/dashboard` — may change; `{url}`: the driver's own pages at Stripe (payouts
  made, bank account). 400 `payouts_not_set_up`; 503 `payments_off`; 502 `payout_failed`

Stripe sends the driver back to `/?payouts=done#/drive` from its pages, and to
`/?payouts=again#/drive` when the link had expired.
