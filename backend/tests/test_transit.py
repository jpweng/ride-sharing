"""
Tests for transit.py's parsing of transport.opendata.ch responses.

The fake_transit fixture used elsewhere replaces the station lookups and
get_connections wholesale, so it can't catch bugs in how the real API
responses are interpreted. These tests instead stub only the HTTP layer
(requests.get) with payloads recorded from the live API, and run the real
transit.py code on top -- still fully offline.
"""
import pytest
import requests

import operators
import transit

# ---------------------------------------------------------------------------
# Recorded /locations?x=..&y=..&type=coordinate responses (nearest stops)
# ---------------------------------------------------------------------------

# A point in Villeneuve VD. The first entry is the street-address
# pseudo-result the API always puts first: no id, but it does carry a
# coordinate.
VILLENEUVE_NEARBY = {"stations": [
    {"id": None, "name": "En Moillesaulaz 1, 1844 Villeneuve (VD)", "distance": 192.19,
     "coordinate": {"type": "WGS84", "x": 46.406952, "y": 6.955465}},
    {"id": "8594008", "name": "Veytaux, Grandchamp", "distance": 1715, "icon": "bus",
     "coordinate": {"type": "WGS84", "x": 46.409856, "y": 6.931329}},
    {"id": "8593777", "name": "Villeneuve VD, Clos du Moulin", "distance": 1755, "icon": "bus",
     "coordinate": {"type": "WGS84", "x": 46.40583, "y": 6.930233}},
]}

# A point in Fribourg.
FRIBOURG_NEARBY = {"stations": [
    {"id": None, "name": "Chemin de la Motta 1, Fribourg - Freiburg", "distance": 30.54,
     "coordinate": {"type": "WGS84", "x": 46.803452, "y": 7.161397}},
    {"id": "8509224", "name": "Fribourg, Pont de St-Jean", "distance": 40, "icon": "bus",
     "coordinate": {"type": "WGS84", "x": 46.803672, "y": 7.161495}},
]}

# The older shape of the same pseudo-result: no id *and* no coordinate.
ADDRESS_WITHOUT_COORDINATE = {"stations": [
    {"id": None, "name": "Some Street 1, 3005 Bern", "distance": 10,
     "coordinate": {"type": "WGS84", "x": None, "y": None}},
    {"id": "8507000", "name": "Bern", "distance": 900, "icon": "train",
     "coordinate": {"type": "WGS84", "x": 46.948832, "y": 7.439131}},
]}

ONLY_ADDRESS = {"stations": [VILLENEUVE_NEARBY["stations"][0]]}

# ---------------------------------------------------------------------------
# Recorded /locations?query=..&type=station responses (search by name)
# ---------------------------------------------------------------------------

BERN_SEARCH = {"stations": [
    {"id": "8507000", "name": "Bern", "icon": "train", "coordinate": {"x": 46.948831, "y": 7.439129}},
    {"id": "8576646", "name": "Bern, Bahnhof", "icon": "tram", "coordinate": {"x": 46.948107, "y": 7.440207}},
    {"id": "8579896", "name": "Bern, Hirschengraben", "icon": "tram", "coordinate": {"x": 46.946065, "y": 7.437781}},
]}

VILLENEUVE_SEARCH = {"stations": [
    {"id": "8501303", "name": "Villeneuve VD", "icon": "train", "coordinate": {"x": 46.398372, "y": 6.926999}},
    {"id": "8570581", "name": "Villeneuve VD, gare", "icon": "bus", "coordinate": {"x": 46.398427, "y": 6.926774}},
    {"id": "8593777", "name": "Villeneuve VD, Clos du Moulin", "icon": "bus", "coordinate": {"x": 46.40583, "y": 6.930233}},
]}

FRIBOURG_SEARCH = {"stations": [
    {"id": "8504100", "name": "Fribourg/Freiburg", "icon": "train", "coordinate": {"x": 46.803151, "y": 7.151052}},
    {"id": "8592374", "name": "Fribourg/Freiburg, Pl. Gare", "icon": "bus", "coordinate": {"x": 46.802858, "y": 7.151621}},
]}

# Saas-Fee has no railway: cable cars rank first, then the bus terminal.
SAAS_FEE_SEARCH = {"stations": [
    {"id": "8530805", "name": "Saas-Fee (Alpin Express)", "icon": "cableway", "coordinate": {"x": 46.105937, "y": 7.928091}},
    {"id": "8501647", "name": "Saas-Fee Chalbermatten", "icon": "cableway", "coordinate": {"x": 46.102387, "y": 7.926186}},
    {"id": "8501782", "name": "Saas-Fee, Busterminal", "icon": "bus", "coordinate": {"x": 46.108796, "y": 7.929573}},
    {"id": "8571155", "name": "Saas-Fee, Fluh", "icon": "bus", "coordinate": {"x": 46.123087, "y": 7.932142}},
]}

# geo.admin's centroid for each municipality, as the autocomplete sends it.
BERN_CENTROID = (46.954559326171875, 7.420684814453125)
VILLENEUVE_VD_CENTROID = (46.40636, 6.95311)
FRIBOURG_CENTROID = (46.80337, 7.16178)
SAAS_FEE_CENTROID = (46.08540725708008, 7.8983473777771)
# Villeneuve FR, ~45km from Villeneuve VD.
VILLENEUVE_FR_CENTROID = (46.7457, 6.8656)

# ---------------------------------------------------------------------------
# Recorded /connections response (trimmed)
# ---------------------------------------------------------------------------

VEYTAUX_FRIBOURG_CONNECTIONS = {"connections": [
    {"from": {"departure": "2026-10-18T18:57:00+0200",
              "station": {"id": "8594008", "name": "Veytaux, Grandchamp"}},
     "to": {"arrival": "2026-10-18T20:41:00+0200",
            "station": {"id": "8509224", "name": "Fribourg, Pont de St-Jean"}},
     "duration": "00d01:44:00", "transfers": 3,
     "sections": [
         {"journey": {"category": "B", "number": "201", "operator": "VMCV"}},
         {"walk": {"duration": 120}, "journey": None},
         {"journey": {"category": "RE", "number": "33", "operator": "SBB"}},
         {"journey": {"category": "IC", "number": "1", "operator": "SBB"}},
         {"journey": {"category": "B", "number": "4", "operator": "TPF Auto"}},
     ]},
]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def fake_http(monkeypatch):
    """
    Route requests.get by endpoint + params to recorded payloads.
    `nearby` maps (lat, lon) -> payload for coordinate lookups, `search`
    maps a query string -> payload for station searches, and `connections`
    maps (from, to) -> payload. Every call is logged in `calls`.
    """
    class FakeHttp:
        def __init__(self):
            self.nearby = {}
            self.search = {}
            self.connections = {}
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params))
            if url.endswith("/locations") and "query" in params:
                return FakeResponse(self.search.get(params["query"], {"stations": []}))
            if url.endswith("/locations"):
                return FakeResponse(self.nearby.get((params["x"], params["y"]), {"stations": []}))
            if url.endswith("/connections"):
                return FakeResponse(self.connections.get((params["from"], params["to"]), {"connections": []}))
            raise AssertionError(f"unexpected URL {url}")

        def search_queries(self):
            return [p["query"] for url, p in self.calls if url.endswith("/locations") and "query" in p]

    fake = FakeHttp()
    monkeypatch.setattr(transit.requests, "get", fake.get)
    return fake


# ---------------------------------------------------------------------------
# Nearest stop (street addresses and map points)
# ---------------------------------------------------------------------------

def test_nearest_named_station_skips_address_pseudo_result(fake_http):
    fake_http.nearby[VILLENEUVE_VD_CENTROID] = VILLENEUVE_NEARBY

    station = transit.nearest_named_station(*VILLENEUVE_VD_CENTROID)

    assert station == {"id": "8594008", "name": "Veytaux, Grandchamp",
                       "lat": 46.409856, "lon": 6.931329, "distance_m": 1715}


def test_nearest_named_station_skips_address_without_coordinate(fake_http):
    fake_http.nearby[(46.94, 7.44)] = ADDRESS_WITHOUT_COORDINATE

    assert transit.nearest_named_station(46.94, 7.44)["name"] == "Bern"


def test_nearest_named_station_none_when_only_address(fake_http):
    fake_http.nearby[VILLENEUVE_VD_CENTROID] = ONLY_ADDRESS

    assert transit.nearest_named_station(*VILLENEUVE_VD_CENTROID) is None


def test_station_lookups_fail_soft_on_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(transit.requests, "get", boom)

    assert transit.nearest_named_station(46.4, 6.9) is None
    assert transit.station_for_place("Bern (BE)", *BERN_CENTROID) is None
    assert transit.resolve_station(46.4, 6.9, "Bern (BE)") is None


# ---------------------------------------------------------------------------
# A town picked by name -> its train station
# ---------------------------------------------------------------------------

def test_place_resolves_to_main_train_station(fake_http):
    fake_http.search["Villeneuve"] = VILLENEUVE_SEARCH

    station = transit.station_for_place("Villeneuve (VD)", *VILLENEUVE_VD_CENTROID)

    assert station["name"] == "Villeneuve VD"
    assert station["id"] == "8501303"


def test_place_search_drops_canton_suffix(fake_http):
    """With the suffix, the API ranks tram stops and "Wabern bei Bern"
    above Bern station -- so the bare name has to be searched first."""
    fake_http.search["Bern"] = BERN_SEARCH

    station = transit.station_for_place("Bern (BE)", *BERN_CENTROID)

    assert station["name"] == "Bern"
    assert fake_http.search_queries() == ["Bern"]


def test_place_without_railway_gets_bus_stop_not_cable_car(fake_http):
    fake_http.search["Saas-Fee"] = SAAS_FEE_SEARCH

    station = transit.station_for_place("Saas-Fee (VS)", *SAAS_FEE_CENTROID)

    assert station["name"] == "Saas-Fee, Busterminal"


def test_same_named_town_elsewhere_is_ignored(fake_http):
    """Searching "Villeneuve" finds Villeneuve VD's station, but that's
    ~45km from Villeneuve FR -- it must not be returned for it."""
    fake_http.search["Villeneuve"] = VILLENEUVE_SEARCH

    assert transit.station_for_place("Villeneuve (FR)", *VILLENEUVE_FR_CENTROID) is None
    # Retried with the canton, since the bare name found nothing nearby.
    assert fake_http.search_queries() == ["Villeneuve", "Villeneuve (FR)"]


def test_resolve_station_uses_place_station_for_towns(fake_http):
    fake_http.search["Villeneuve"] = VILLENEUVE_SEARCH
    fake_http.nearby[VILLENEUVE_VD_CENTROID] = VILLENEUVE_NEARBY

    assert transit.resolve_station(*VILLENEUVE_VD_CENTROID, place_name="Villeneuve (VD)")["name"] == "Villeneuve VD"
    # An address or map point at the same spot still uses the nearest stop,
    # without searching by name at all.
    fake_http.calls.clear()
    assert transit.resolve_station(*VILLENEUVE_VD_CENTROID)["name"] == "Veytaux, Grandchamp"
    assert fake_http.search_queries() == []


def test_resolve_station_falls_back_to_nearest_stop(fake_http):
    fake_http.nearby[VILLENEUVE_VD_CENTROID] = VILLENEUVE_NEARBY

    station = transit.resolve_station(*VILLENEUVE_VD_CENTROID, place_name="Nowhere (VD)")

    assert station["name"] == "Veytaux, Grandchamp"


# ---------------------------------------------------------------------------
# Connections: operators and booking link
# ---------------------------------------------------------------------------

def test_connections_list_legs_operators_and_booking_link(fake_http):
    fake_http.connections[("Veytaux, Grandchamp", "Fribourg, Pont de St-Jean")] = VEYTAUX_FRIBOURG_CONNECTIONS

    [conn] = transit.get_connections("Veytaux, Grandchamp", "Fribourg, Pont de St-Jean", "2026-10-18T18:53")

    assert conn["departure"] == "2026-10-18T18:57:00+0200"
    assert conn["products"] == ["B", "IC", "RE"]
    assert [(leg["category"], leg["number"], leg["operator"]["code"]) for leg in conn["legs"]] == [
        ("B", "201", "VMCV"), ("RE", "33", "SBB"), ("IC", "1", "SBB"), ("B", "4", "TPF"),
    ]
    # Each company once, in travel order.
    assert [o["name"] for o in conn["operators"]] == ["VMCV", "SBB CFF FFS", "TPF"]
    assert conn["operators"][1]["url"] == "https://www.sbb.ch"
    assert conn["booking_url"] == (
        "https://www.sbb.ch/en?stops=Veytaux,+Grandchamp_I8594008~Fribourg,+Pont+de+St-Jean_I8509224"
        "&day=2026-10-18&time=18_57&moment=dep"
    )
    params = [p for url, p in fake_http.calls if url.endswith("/connections")][0]
    assert (params["date"], params["time"]) == ("2026-10-18", "18:53")


@pytest.mark.parametrize("raw,code", [
    ("SBB", "SBB"), ("BLS-bls", "BLS"), ("MGB-EV", "MGB"), ("TPF Auto", "TPF"),
    ("VBZ    F", "VBZ"), ("RhB", "RHB"), ("NStCM-EV", "NSTCM"),
])
def test_operator_codes_are_normalized(raw, code):
    info = operators.operator_info(raw)
    assert info["code"] == code
    assert info["url"].startswith("https://www.")
    assert info["logo_url"].startswith("https://www.google.com/s2/favicons?domain=")


def test_unknown_operator_keeps_its_code_without_link():
    assert operators.operator_info("XYZ-abc") == {"code": "XYZ", "name": "XYZ", "url": None, "logo_url": None}
    assert operators.operator_info(None) is None
    assert operators.operator_info("  ") is None


# ---------------------------------------------------------------------------
# End to end: the full public-transport alternative
# ---------------------------------------------------------------------------

def test_town_to_town_trip_runs_station_to_station(fake_http):
    """Villeneuve (VD) -> Fribourg (FR) picked as towns: the journey runs
    between their train stations, not the bus stops nearest each
    municipality's centroid."""
    fake_http.search["Villeneuve"] = VILLENEUVE_SEARCH
    fake_http.search["Fribourg"] = FRIBOURG_SEARCH
    fake_http.connections[("Villeneuve VD", "Fribourg/Freiburg")] = VEYTAUX_FRIBOURG_CONNECTIONS

    option = transit.transit_option_for_trip(
        *VILLENEUVE_VD_CENTROID, *FRIBOURG_CENTROID, "2026-10-18T18:53",
        origin_place="Villeneuve (VD)", destination_place="Fribourg (FR)",
    )

    assert option["from_station"] == "Villeneuve VD"
    assert option["to_station"] == "Fribourg/Freiburg"
    assert len(option["connections"]) == 1


def test_address_to_address_trip_skips_address_pseudo_results(fake_http):
    """The original bug: Villeneuve VD -> Fribourg showed "No direct public
    transport option found" because the address pseudo-results were passed
    to /connections as if they were stations."""
    fake_http.nearby[VILLENEUVE_VD_CENTROID] = VILLENEUVE_NEARBY
    fake_http.nearby[FRIBOURG_CENTROID] = FRIBOURG_NEARBY
    fake_http.connections[("Veytaux, Grandchamp", "Fribourg, Pont de St-Jean")] = VEYTAUX_FRIBOURG_CONNECTIONS

    option = transit.transit_option_for_trip(*VILLENEUVE_VD_CENTROID, *FRIBOURG_CENTROID, "2026-10-18T18:53")

    assert option["from_station"] == "Veytaux, Grandchamp"
    assert option["to_station"] == "Fribourg, Pont de St-Jean"
    assert len(option["connections"]) == 1
