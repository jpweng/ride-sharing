"""
Keeps a rolling week of fictional drives in the database, so "Browse rides"
and matching always have something to show on the public demo.

The drives form a network over the whole country, denser in Ticino and
Graubünden: weekday commutes (out in the morning, back in the evening),
midday drives on the busy corridors, and weekend trips. Each corridor has
departures spread over the day, so a search at most times finds something
within matching.MAX_TIME_WINDOW_HOURS.

Demo drives are flagged `is_demo` and are the only drives touched here:
drives posted by visitors are never modified or deleted. refresh() is
idempotent; maintenance.py runs it at startup and then hourly. It drops demo
drives that have departed or no longer match DEMO_DRIVES, and adds the ones
missing from the next DAYS_AHEAD days.

Run it by hand with the backend on PYTHONPATH, e.g.:
    PYTHONPATH=backend uv run python backend/demo_drives.py
"""
import time as clock
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import database as db
import matching

DAYS_AHEAD = 7

# Departure times are naive Swiss local time, like the ones the frontend's
# datetime-local input sends.
LOCAL_TZ = ZoneInfo("Europe/Zurich")

# The public OSRM server asks for at most one request per second.
ROUTING_PAUSE_S = 1.0

MON_FRI = (0, 1, 2, 3, 4)
DAILY = (0, 1, 2, 3, 4, 5, 6)
SAT, SUN = (5,), (6,)

# Town centre or main station, (lat, lon).
PLACES = {
    # Romandie
    "Genève": (46.2044, 6.1432),
    "Nyon": (46.3833, 6.2333),
    "Lausanne": (46.5197, 6.6323),
    "Yverdon-les-Bains": (46.7785, 6.6411),
    "Vevey": (46.4628, 6.8419),
    "Montreux": (46.4312, 6.9107),
    "Bulle": (46.6193, 7.0569),
    "Fribourg": (46.8065, 7.1619),
    "Martigny": (46.1027, 7.0726),
    "Sion": (46.2331, 7.3606),
    "Sierre": (46.2919, 7.5354),
    "Brig": (46.3159, 7.9876),
    "Neuchâtel": (46.9900, 6.9293),
    "La Chaux-de-Fonds": (47.1035, 6.8328),
    "Delémont": (47.3649, 7.3445),
    "Biel/Bienne": (47.1368, 7.2467),
    # German-speaking Switzerland
    "Bern": (46.9480, 7.4474),
    "Thun": (46.7580, 7.6280),
    "Interlaken": (46.6863, 7.8632),
    "Solothurn": (47.2088, 7.5323),
    "Olten": (47.3500, 7.9033),
    "Aarau": (47.3925, 8.0444),
    "Basel": (47.5596, 7.5886),
    "Luzern": (47.0502, 8.3093),
    "Zug": (47.1662, 8.5155),
    "Zürich": (47.3769, 8.5417),
    "Winterthur": (47.4988, 8.7237),
    "Schaffhausen": (47.6973, 8.6349),
    "St. Gallen": (47.4245, 9.3767),
    "Sargans": (47.0450, 9.4400),
    # Ticino
    "Airolo": (46.5286, 8.6106),
    "Biasca": (46.3597, 8.9696),
    "Bellinzona": (46.1946, 9.0175),
    "Locarno": (46.1709, 8.7995),
    "Cevio": (46.3175, 8.6020),
    "Lugano": (46.0037, 8.9511),
    "Ponte Tresa": (45.9693, 8.8607),
    "Mendrisio": (45.8703, 8.9817),
    "Chiasso": (45.8320, 9.0312),
    "Roveredo": (46.2367, 9.1270),
    # Graubünden
    "Chur": (46.8508, 9.5320),
    "Landquart": (46.9667, 9.5550),
    "Klosters": (46.8696, 9.8813),
    "Davos": (46.8027, 9.8360),
    "Arosa": (46.7833, 9.6790),
    "Flims": (46.8350, 9.2840),
    "Ilanz": (46.7740, 9.2040),
    "Disentis/Mustér": (46.7036, 8.8530),
    "Thusis": (46.6970, 9.4400),
    "St. Moritz": (46.4908, 9.8355),
    "Zernez": (46.6990, 10.0920),
    "Scuol": (46.7966, 10.2980),
    "Poschiavo": (46.3240, 10.0580),
}


def _drive(driver, origin, destination, at, weekdays, seats=3):
    hours, minutes = map(int, at.split(":"))
    return {
        "driver_name": driver,
        "origin": {"name": origin, "lat": PLACES[origin][0], "lon": PLACES[origin][1]},
        "destination": {"name": destination, "lat": PLACES[destination][0], "lon": PLACES[destination][1]},
        "time": time(hours, minutes),
        "weekdays": weekdays,
        "seats_available": seats,
    }


def _commute(driver, home, work, out, back, weekdays=MON_FRI, seats=3):
    """There in the morning, back in the evening, by the same driver."""
    return [
        _drive(driver, home, work, out, weekdays, seats),
        _drive(driver, work, home, back, weekdays, seats),
    ]


# Recurring drives: one drive per listed weekday (Monday = 0) at the given
# local time. A driver never has two drives at the same time on one day.
DEMO_DRIVES = [
    # --- Romandie ---------------------------------------------------------
    *_commute("Jean Dupont", "Lausanne", "Genève", "07:15", "17:45"),
    *_commute("Sarah Pittet", "Nyon", "Genève", "07:40", "18:10", seats=2),
    *_commute("Anne Jaquier", "Yverdon-les-Bains", "Lausanne", "07:30", "17:30"),
    *_commute("Claire Rochat", "Vevey", "Fribourg", "07:20", "17:15"),
    *_commute("Nicolas Favre", "Montreux", "Bern", "06:45", "17:30", seats=2),
    *_commute("Julien Genoud", "Bulle", "Lausanne", "07:00", "18:00"),
    *_commute("Marco Rossi", "Fribourg", "Lausanne", "07:05", "17:20", seats=4),
    *_commute("Laure Chappuis", "Sion", "Lausanne", "06:30", "17:15"),
    *_commute("Pierre-Alain Roduit", "Martigny", "Genève", "07:00", "18:00", weekdays=(0, 2, 4)),
    *_commute("Kevin Aymon", "Sierre", "Sion", "07:20", "17:40"),
    *_commute("Stefan Imboden", "Brig", "Sion", "06:50", "17:20", seats=2),
    *_commute("Thomas Perret", "Neuchâtel", "Bern", "07:10", "17:20"),
    *_commute("Mélanie Cuche", "La Chaux-de-Fonds", "Neuchâtel", "07:00", "17:00"),
    *_commute("Damien Juillerat", "Delémont", "Biel/Bienne", "07:15", "17:10"),
    # Midday and evening on the Lake Geneva - Fribourg - Bern corridor.
    _drive("Olivier Bovet", "Lausanne", "Bern", "10:00", DAILY),
    _drive("Olivier Bovet", "Bern", "Lausanne", "14:30", DAILY),
    _drive("Élodie Morel", "Vevey", "Fribourg", "12:30", (0, 2, 4, 5)),
    _drive("Élodie Morel", "Fribourg", "Vevey", "15:00", (1, 3, 6)),
    _drive("Lucas Monney", "Vevey", "Fribourg", "16:30", MON_FRI, seats=2),
    _drive("Lucas Monney", "Fribourg", "Vevey", "19:30", (4, 6), seats=2),
    _drive("Isabelle Mottier", "Genève", "Lausanne", "12:00", DAILY, seats=2),
    _drive("Isabelle Mottier", "Lausanne", "Genève", "20:00", (4, 6), seats=2),
    # Weekends.
    _drive("Luca Bianchi", "Neuchâtel", "Genève", "09:30", SAT),
    _drive("Luca Bianchi", "Genève", "Neuchâtel", "18:00", SUN),
    _drive("Camille Dubois", "Genève", "Sion", "08:30", SAT),
    _drive("Camille Dubois", "Sion", "Genève", "17:00", SUN),
    # --- German-speaking Switzerland ---------------------------------------
    *_commute("Sophie Meier", "Zürich", "Bern", "07:45", "17:15", seats=2),
    *_commute("Anna Keller", "Basel", "Zürich", "08:00", "17:45", weekdays=(0, 2, 4), seats=1),
    *_commute("Reto Brunner", "Luzern", "Zürich", "07:00", "17:30"),
    *_commute("Andrea Steiner", "Thun", "Bern", "07:30", "17:00"),
    *_commute("Lukas Huber", "Winterthur", "Zürich", "07:15", "17:40"),
    *_commute("Nadine Frei", "St. Gallen", "Zürich", "06:50", "17:20"),
    *_commute("Marco Bühler", "Schaffhausen", "Zürich", "07:10", "17:50", seats=2),
    *_commute("Daniela Graf", "Aarau", "Basel", "07:30", "17:10"),
    *_commute("Simon Wyss", "Solothurn", "Bern", "07:20", "17:30"),
    *_commute("Martina Kälin", "Zug", "Zürich", "07:40", "18:00"),
    *_commute("Beat Schmid", "Olten", "Luzern", "07:15", "17:15"),
    *_commute("Urs Gerber", "Basel", "Bern", "08:30", "16:45"),
    _drive("Fabienne Roth", "Bern", "Zürich", "11:30", DAILY),
    _drive("Fabienne Roth", "Zürich", "Bern", "15:00", DAILY),
    _drive("Peter Zbinden", "Bern", "Interlaken", "09:00", SAT),
    _drive("Peter Zbinden", "Interlaken", "Bern", "17:30", SUN),
    _drive("Jonas Baumann", "Zürich", "Luzern", "10:00", SAT),
    _drive("Jonas Baumann", "Luzern", "Zürich", "18:30", SUN),
    # --- Ticino ------------------------------------------------------------
    *_commute("Giulia Bernasconi", "Lugano", "Bellinzona", "07:15", "17:30"),
    *_commute("Matteo Pedrazzini", "Locarno", "Bellinzona", "07:00", "17:15"),
    *_commute("Chiara Lepori", "Mendrisio", "Lugano", "07:30", "17:45"),
    *_commute("Davide Cattaneo", "Chiasso", "Lugano", "07:10", "18:00", seats=2),
    *_commute("Sara Guscetti", "Locarno", "Lugano", "07:45", "17:30"),
    *_commute("Fabio Rusconi", "Biasca", "Lugano", "06:50", "17:10", seats=4),
    *_commute("Elena Fumagalli", "Airolo", "Bellinzona", "07:00", "17:20"),
    *_commute("Nicola Respini", "Cevio", "Locarno", "07:10", "17:40"),
    *_commute("Alessia Ferrari", "Ponte Tresa", "Lugano", "07:30", "17:30", seats=2),
    *_commute("Lisa Togni", "Roveredo", "Bellinzona", "07:20", "17:15"),
    _drive("Giorgio Brenni", "Bellinzona", "Lugano", "10:00", DAILY),
    _drive("Giorgio Brenni", "Lugano", "Bellinzona", "13:30", DAILY),
    _drive("Paola Rezzonico", "Lugano", "Locarno", "12:00", DAILY, seats=2),
    _drive("Paola Rezzonico", "Locarno", "Lugano", "14:30", DAILY, seats=2),
    _drive("Tiziano Gendotti", "Bellinzona", "Airolo", "11:00", DAILY),
    _drive("Tiziano Gendotti", "Airolo", "Bellinzona", "15:30", DAILY),
    _drive("Ivo Croci", "Lugano", "Mendrisio", "20:00", (4, 5)),
    _drive("Ivo Croci", "Mendrisio", "Lugano", "19:00", MON_FRI),
    # Across the Alps.
    _drive("Marco Zanetti", "Lugano", "Zürich", "08:00", (1, 3)),
    _drive("Marco Zanetti", "Zürich", "Lugano", "16:30", (1, 3)),
    _drive("Laura Pianezzi", "Bellinzona", "Zürich", "17:30", (4,)),
    _drive("Laura Pianezzi", "Zürich", "Bellinzona", "17:30", SUN),
    _drive("Moreno Tognola", "Bellinzona", "Chur", "07:30", (0, 2, 4)),
    _drive("Moreno Tognola", "Chur", "Bellinzona", "17:00", (0, 2, 4)),
    # Weekends.
    _drive("Roberto Ghisletta", "Lugano", "Airolo", "08:30", SAT),
    _drive("Roberto Ghisletta", "Airolo", "Lugano", "17:00", SUN),
    # --- Graubünden --------------------------------------------------------
    *_commute("Gian Caduff", "Chur", "Zürich", "06:45", "17:30"),
    *_commute("Ursina Cavelti", "Davos", "Chur", "07:00", "17:30"),
    *_commute("Men Cadonau", "Scuol", "St. Moritz", "07:10", "17:20"),
    *_commute("Ladina Bezzola", "St. Moritz", "Chur", "07:30", "17:00", weekdays=(0, 2, 4)),
    *_commute("Flurin Casutt", "Ilanz", "Chur", "07:15", "17:30"),
    *_commute("Corsin Deplazes", "Disentis/Mustér", "Chur", "06:50", "17:10", seats=2),
    *_commute("Seraina Joos", "Thusis", "Chur", "07:20", "17:15"),
    *_commute("Andri Gaudenz", "Arosa", "Chur", "07:30", "18:00", seats=2),
    *_commute("Nina Bundi", "Landquart", "Chur", "07:40", "17:00"),
    *_commute("Mirco Lardi", "Poschiavo", "St. Moritz", "07:00", "17:30", weekdays=(0, 2, 4)),
    *_commute("Rolf Kühne", "Sargans", "Chur", "07:20", "17:10"),
    *_commute("Reto Clavadetscher", "Klosters", "Landquart", "07:10", "17:20"),
    _drive("Martin Hartmann", "Chur", "Davos", "11:00", DAILY),
    _drive("Martin Hartmann", "Davos", "Chur", "14:00", DAILY),
    _drive("Claudio Pally", "Chur", "Disentis/Mustér", "12:30", DAILY),
    _drive("Claudio Pally", "Disentis/Mustér", "Chur", "15:30", DAILY),
    _drive("Silvia Item", "Chur", "St. Moritz", "10:30", (5, 6)),
    _drive("Silvia Item", "St. Moritz", "Chur", "16:00", (5, 6)),
    _drive("Gianna Clalüna", "St. Moritz", "Scuol", "12:00", (1, 3, 5)),
    _drive("Gianna Clalüna", "Scuol", "St. Moritz", "15:30", (1, 3, 5)),
    _drive("Duri Puorger", "Davos", "Scuol", "09:00", SAT),
    _drive("Duri Puorger", "Scuol", "Davos", "16:30", SUN),
    # Weekends from the cities to the mountains.
    _drive("Thomas Schällibaum", "Zürich", "Davos", "07:30", SAT, seats=4),
    _drive("Thomas Schällibaum", "Davos", "Zürich", "16:30", SUN, seats=4),
    _drive("Petra Vogel", "Zürich", "Flims", "08:00", SAT),
    _drive("Petra Vogel", "Flims", "Zürich", "16:00", SUN),
    _drive("Jan Zogg", "St. Gallen", "Chur", "08:30", SAT),
    _drive("Jan Zogg", "Chur", "St. Gallen", "17:30", SUN),
]


def _departure_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def _expected(now: datetime) -> dict:
    """(driver, departure time) -> spec, for every demo drive due in the window."""
    expected = {}
    for day in range(DAYS_AHEAD):
        date = now.date() + timedelta(days=day)
        for spec in DEMO_DRIVES:
            departure = datetime.combine(date, spec["time"])
            if date.weekday() in spec["weekdays"] and departure > now:
                expected[(spec["driver_name"], _departure_iso(departure))] = spec
    return expected


def _route(spec, cache: dict) -> dict:
    """Road route for a template: reused from an earlier demo drive on the same
    trip when there is one, so the routing server is only asked once."""
    key = (spec["origin"]["name"], spec["destination"]["name"])
    if key not in cache:
        cache[key] = db.find_demo_route(*key)
    if cache[key] is None:
        cache[key] = matching.route_with_fallback([
            (spec["origin"]["lat"], spec["origin"]["lon"]),
            (spec["destination"]["lat"], spec["destination"]["lon"]),
        ])
        if cache[key]["source"] == "osrm":
            clock.sleep(ROUTING_PAUSE_S)
    return cache[key]


def refresh(now: datetime | None = None) -> tuple[int, int]:
    """Bring demo drives up to date; returns (deleted, created)."""
    db.init_db()
    now = now or datetime.now(LOCAL_TZ).replace(tzinfo=None)
    deleted = db.delete_demo_drives_before(_departure_iso(now))
    expected = _expected(now)

    # Upcoming demo drives from templates that changed or were removed.
    existing = set()
    for d in db.list_drives(upcoming_only=False, with_geometry=False):
        if not d["is_demo"]:
            continue
        spec = expected.get((d["driver_name"], d["departure_time"]))
        if (
            spec
            and spec["origin"]["name"] == d["origin_name"]
            and spec["destination"]["name"] == d["destination_name"]
        ):
            existing.add((d["driver_name"], d["departure_time"]))
        else:
            db.delete_drive(d["id"])
            deleted += 1

    created = 0
    routes = {}
    for (driver_name, departure_time), spec in expected.items():
        if (driver_name, departure_time) in existing:
            continue
        route = _route(spec, routes)
        db.create_drive(
            driver_name=driver_name,
            origin_name=spec["origin"]["name"],
            origin_lat=spec["origin"]["lat"],
            origin_lon=spec["origin"]["lon"],
            destination_name=spec["destination"]["name"],
            destination_lat=spec["destination"]["lat"],
            destination_lon=spec["destination"]["lon"],
            departure_time=departure_time,
            seats_available=spec["seats_available"],
            route_geometry=route["geometry"],
            route_distance_m=route["distance_m"],
            route_duration_s=route["duration_s"],
            route_source=route["source"],
            is_demo=True,
        )
        created += 1
    return deleted, created


if __name__ == "__main__":
    deleted, created = refresh()
    print(f"Demo drives: {deleted} deleted, {created} created")
