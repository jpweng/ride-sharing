"""
Load test: an event evening. Many passengers look for a ride to an event in
Bern (BERNEXPO) from the towns around, or home from it afterwards; they
search, browse drives and look at some, check their inbox, and a few ask for
a ride nobody posted. A few drivers post drives to the event and look at
their own.

Run it against a local instance only, never against rideandrail.ch: see
loadtest/run.sh, which starts the app with stand-ins for its upstream APIs
(loadtest/stub_upstreams.py) and runs this file with a pinned Locust.
"""
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse

from locust import HttpUser, between, events, task

VENUE = {"name": "BERNEXPO, Bern", "lat": 46.9590, "lon": 7.4680, "kind": "address"}

# Where the audience comes from: (name, lat, lon), up to ~70 km away.
TOWNS = [
    ("Thun", 46.7580, 7.6280), ("Fribourg", 46.8065, 7.1619), ("Biel/Bienne", 47.1368, 7.2467),
    ("Solothurn", 47.2088, 7.5323), ("Burgdorf", 47.0590, 7.6280), ("Langenthal", 47.2153, 7.7960),
    ("Interlaken", 46.6863, 7.8632), ("Neuchâtel", 46.9900, 6.9293), ("Olten", 47.3500, 7.9033),
    ("Lyss", 47.0742, 7.3066), ("Münsingen", 46.8730, 7.5610), ("Murten", 46.9283, 7.1171),
    ("Konolfingen", 46.8790, 7.6200), ("Spiez", 46.6860, 7.6800), ("Langnau i.E.", 46.9400, 7.7870),
    ("Bern", 46.9480, 7.4474), ("Köniz", 46.9240, 7.4140), ("Worb", 46.9300, 7.5630),
]

FIRST = ["Anna", "Luca", "Mia", "Noah", "Lea", "Elias", "Laura", "David", "Sara", "Jonas", "Chiara", "Marco"]
LAST = ["Müller", "Meier", "Schmid", "Keller", "Weber", "Huber", "Rochat", "Rossi", "Favre", "Bianchi"]

# The event is tomorrow evening (always within the demo drives' week).
EVENT_DAY = (datetime.now() + timedelta(days=1)).date()


def _iso(hour, minute=0):
    return datetime.combine(EVENT_DAY, datetime.min.time()).replace(hour=hour, minute=minute).isoformat(timespec="minutes")


def _near(town):
    """A street address in or near `town`."""
    name, lat, lon = town
    return {"name": f"Street, {name}", "lat": lat + random.uniform(-0.02, 0.02),
            "lon": lon + random.uniform(-0.03, 0.03), "kind": "address"}


@events.init.add_listener
def _local_only(environment, **kwargs):
    host = urlparse(environment.host or "").hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"Refusing to load {host!r}: run load tests against a local instance only.")


class Passenger(HttpUser):
    weight = 20
    wait_time = between(5, 20)  # reading results, moving the map

    def on_start(self):
        self.name = f"{random.choice(FIRST)} {random.choice(LAST)} {random.randrange(10**6)}"
        self.drive_ids = []
        self.client.get(f"/api/notifications?name={self.name}", name="/api/notifications")
        self.client.get(f"/api/ride_requests?passenger_name={self.name}", name="/api/ride_requests")

    def _trip(self):
        town = _near(random.choice(TOWNS))
        if random.random() < 0.7:
            return town, VENUE, _iso(18, random.choice((0, 15, 30, 45)))
        return VENUE, town, _iso(23, random.choice((0, 15, 30)))

    @task(12)
    def search(self):
        origin, destination, when = self._trip()
        body = {"passenger_name": self.name, "origin": origin, "destination": destination, "requested_time": when}
        if random.random() < 0.3:  # some widen their circles
            body.update(pickup_radius_km=10, dropoff_radius_km=10, time_tolerance_min=180)
        with self.client.post("/api/match", json=body, catch_response=True) as resp:
            if resp.ok:
                matches = resp.json()["driver_matches"]
                self.drive_ids = [m["id"] for m in matches] or self.drive_ids
            else:
                resp.failure(f"{resp.status_code}")

    @task(6)
    def browse(self):
        resp = self.client.get("/api/drives?upcoming_only=true&limit=100", name="/api/drives?limit=100")
        if resp.ok and not self.drive_ids:
            self.drive_ids = [d["id"] for d in resp.json()[:20]]

    @task(6)
    def view_drive(self):
        if self.drive_ids:
            self.client.get(f"/api/drives/{random.choice(self.drive_ids)}", name="/api/drives/{id}")

    @task(3)
    def inbox(self):
        self.client.get(f"/api/notifications?name={self.name}", name="/api/notifications")

    @task(1)
    def ask_for_a_ride(self):
        origin, destination, when = self._trip()
        self.client.post("/api/requests", json={
            "passenger_name": self.name, "origin": origin, "destination": destination, "requested_time": when,
        })


class Driver(HttpUser):
    weight = 1
    wait_time = between(20, 60)

    def on_start(self):
        self.name = f"{random.choice(FIRST)} {random.choice(LAST)} driver {random.randrange(10**6)}"
        self.client.get(f"/api/notifications?name={self.name}", name="/api/notifications")

    @task(3)
    def post_drive(self):
        self.client.post("/api/drives", json={
            "driver_name": self.name, "origin": _near(random.choice(TOWNS)), "destination": VENUE,
            "departure_time": _iso(17, random.choice((30, 45))), "seats_available": random.randint(1, 4),
        })

    @task(2)
    def my_drives(self):
        self.client.get(f"/api/drives?driver_name={self.name}", name="/api/drives?driver_name")

    @task(1)
    def cancel_one(self):
        resp = self.client.get(f"/api/drives?driver_name={self.name}", name="/api/drives?driver_name")
        if resp.ok and resp.json():
            drive_id = resp.json()[0]["id"]
            self.client.delete(f"/api/drives/{drive_id}?driver_name={self.name}", name="/api/drives/{id} DELETE")
