"""
What keeps a search's computing short, without changing what it finds: the
database leaving out the drives that can't serve the trip (db.list_drives'
passing_near and ends_inside), and routes kept ready between searches
(search.prepared_routes). Isolated database, offline.
"""
import random
from datetime import date, timedelta

import combined
import database as db
import matching
import search

DAY = (date.today() + timedelta(days=2)).isoformat()
# Switzerland, more or less.
SOUTH, NORTH, WEST, EAST = 45.8, 47.8, 6.0, 10.5


def _place(rng):
    return (rng.uniform(SOUTH, NORTH), rng.uniform(WEST, EAST))


def _drives(rng, count):
    """Random drives, each a road 1 to 1.6 times its straight line long; a few without a road distance."""
    ids = []
    for i in range(count):
        start, end = _place(rng), _place(rng)
        if i % 3 == 0:   # a short hop
            end = (start[0] + rng.uniform(-0.2, 0.2), start[1] + rng.uniform(-0.3, 0.3))
        km = matching.haversine_km(*start, *end) * rng.uniform(1.0, 1.6)
        ids.append(db.create_drive(
            driver_name=f"Driver {i}", origin_name="A", origin_lat=start[0], origin_lon=start[1],
            destination_name="B", destination_lat=end[0], destination_lon=end[1],
            departure_time=f"{DAY}T08:00", seats_available=3, route_geometry=None,
            route_distance_m=None if i % 25 == 0 else km * 1000, route_duration_s=km * 60, route_source="haversine",
        ))
    return ids


def test_the_database_leaves_out_no_drive_that_could_pass_near(isolated_db):
    rng = random.Random(1)
    _drives(rng, 400)
    everyone = db.list_drives(with_geometry=False)
    assert len(everyone) == 400
    listed_in_all = 0
    for _ in range(40):
        point, other, radius = _place(rng), _place(rng), rng.choice((5.0, 10.0, 30.0))
        near = {d["id"] for d in everyone if matching.could_pass_near(d, point, radius)}
        listed = {d["id"] for d in db.list_drives(with_geometry=False, passing_near=[(*point, radius)])}
        assert near <= listed
        listed_in_all += len(listed)
        # Near both points, and near either.
        near_other = {d["id"] for d in everyone if matching.could_pass_near(d, other, radius)}
        both = [(*point, radius), (*other, radius)]
        assert near & near_other <= {d["id"] for d in db.list_drives(with_geometry=False, passing_near=both)}
        either = {d["id"] for d in db.list_drives(with_geometry=False, passing_near=both, passing_near_any=True)}
        assert near | near_other <= either
    # And it leaves most of the others out, even among drives across the whole country.
    assert listed_in_all < 0.4 * 40 * len(everyone)


def test_the_box_holds_every_drive_on_the_passengers_way(isolated_db):
    rng = random.Random(2)
    _drives(rng, 400)
    everyone = db.list_drives(with_geometry=False)
    for _ in range(40):
        origin, destination = _place(rng), _place(rng)
        trip_km = matching.haversine_km(*origin, *destination)
        on_the_way = set()
        for d in everyone:
            start, end = (d["origin_lat"], d["origin_lon"]), (d["destination_lat"], d["destination_lon"])
            way_km = (matching.haversine_km(*origin, *start) + matching.haversine_km(*start, *end)
                      + matching.haversine_km(*end, *destination))
            if way_km <= combined.MAX_DETOUR * trip_km:
                on_the_way.add(d["id"])
        box = combined._on_the_way_box(origin, destination)
        assert on_the_way <= {d["id"] for d in db.list_drives(with_geometry=False, ends_inside=box)}


def test_a_search_finds_the_same_with_and_without_the_databases_help(isolated_db, monkeypatch):
    rng = random.Random(3)
    _drives(rng, 300)
    helped, plain = [], []
    list_drives = db.list_drives

    def without_help(*args, passing_near=None, passing_near_any=False, ends_inside=None, **kwargs):
        return list_drives(*args, **kwargs)

    trips = [(_place(rng), _place(rng)) for _ in range(25)]
    for found in (helped, plain):
        for origin, destination in trips:
            asked = combined._naive(f"{DAY}T08:00")
            found.append((
                [m["id"] for m in search.upcoming_matches(origin, destination, f"{DAY}T08:00", 30.0, 30.0)],
                [(c["drive"]["id"], c["shape"]) for c in combined._candidates(origin, destination, asked, "depart", 240, 10.0, set())],
                [c["drive"]["id"] for c in combined._between_candidates(origin, destination, asked, "depart", 240, 10.0, set())],
            ))
        monkeypatch.setattr(db, "list_drives", without_help)
    assert helped == plain
    assert sum(len(m) + len(c) + len(b) for m, c, b in helped) > 50   # and it did find things


def _demo_drive(at):
    return db.create_drive(
        driver_name="Demo", origin_name="A", origin_lat=46.0, origin_lon=7.0,
        destination_name="B", destination_lat=46.9, destination_lon=7.0,
        departure_time=f"{DAY}T{at}", seats_available=3, route_geometry=[[7.0, 46.0], [7.0, 46.9]],
        route_distance_m=100_000, route_duration_s=6000, route_source="osrm", is_demo=True,
    )


def test_routes_are_kept_ready_between_searches(isolated_db, monkeypatch):
    one, two = _demo_drive("08:00"), _demo_drive("09:00")
    real = db.create_drive(
        driver_name="Real", origin_name="A", origin_lat=46.0, origin_lon=7.0,
        destination_name="B", destination_lat=46.9, destination_lon=7.0,
        departure_time=f"{DAY}T08:00", seats_available=3, route_geometry=[[7.0, 46.0], [7.2, 46.5], [7.0, 46.9]],
        route_distance_m=110_000, route_duration_s=6000, route_source="osrm",
    )
    read = []
    route_geometries = db.route_geometries
    monkeypatch.setattr(db, "route_geometries", lambda ids: read.append(list(ids)) or route_geometries(ids))
    drives = db.list_drives(with_geometry=False)

    routes = search.prepared_routes(drives)
    # The two demo drives of the trip share one route, read once; the posted drive has its own.
    assert routes[one] is routes[two] and routes[real] is not routes[one]
    assert len(routes[one]) == 2 and len(routes[real]) == 3 and [len(ids) for ids in read] == [2]
    # The next search reads nothing.
    again = search.prepared_routes(drives)
    assert again[one] is routes[one] and again[real] is routes[real] and len(read) == 1
    # Not for ever, and not without limit.
    monkeypatch.setattr(search, "ROUTES_KEPT_S", -1.0)
    search._routes.clear()
    search.prepared_routes(drives)
    search.prepared_routes(drives)
    assert len(read) == 3
    monkeypatch.setattr(search, "ROUTES_KEPT", 1)
    search.prepared_routes(drives)
    assert len(search._routes) == 1


def test_matches_are_the_same_from_kept_routes(isolated_db):
    _demo_drive("08:00")
    drives = db.list_drives(with_geometry=True)
    start, end = (46.05, 7.0), (46.85, 7.0)
    from_geometry = matching.find_matches(drives, start, end, f"{DAY}T08:00", 10.0, 10.0, 240)
    from_routes = matching.find_matches(
        db.list_drives(with_geometry=False), start, end, f"{DAY}T08:00", 10.0, 10.0, 240,
        routes=search.prepared_routes(drives),
    )
    assert len(from_routes) == 1
    assert [{k: v for k, v in m.items() if k != "route_geometry"} for m in from_geometry] == \
        [{k: v for k, v in m.items() if k != "route_geometry"} for m in from_routes]
    assert from_geometry[0]["route_geometry"] == from_routes[0]["route_geometry"]
