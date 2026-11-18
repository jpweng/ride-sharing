"""
Tests for fares.py: the T601 distance tariff against the official price
table, and the optional OJP fare lookup (faked: no network) with its
fallback and cache.
"""
import os
import xml.etree.ElementTree as ET

import pytest
import requests

import fares

# T601 "Allgemeiner Personentarif", edition 2026-06-01, section 12
# (Preistabelle): 2nd class, single, full fare, by tariff km.
T601_TABLE = """
1-4 3.00; 5-8 3.80; 9-10 4.60; 11-12 5.40; 13-14 6.40; 15-16 7.20; 17-18 7.80; 19-20 8.60;
21-22 9.40; 23-24 10.20; 25-26 11.00; 27-28 11.80; 29-30 12.60; 31-33 13.60; 34-36 14.80;
37-39 16.00; 40-42 17.20; 43-45 18.40; 46-48 19.40; 49-51 20.40; 52-54 21.20; 55-57 22.00;
58-60 22.80; 61-64 23.80; 65-68 25.00; 69-72 27; 73-76 28; 77-80 29; 81-84 30; 85-88 31;
89-92 32; 93-96 33; 97-100 34; 101-105 36; 106-110 37; 111-115 38; 116-120 40; 121-125 41;
126-130 42; 131-135 44; 136-140 45; 141-145 47; 146-150 48; 151-160 51; 161-170 53;
171-180 56; 181-190 59; 191-200 61; 201-210 64; 211-220 66; 221-230 68; 231-240 71;
241-250 73; 251-260 75; 261-270 77; 271-280 80; 281-290 82; 291-300 84; 301-320 88;
321-340 92; 341-360 96; 361-380 101; 381-400 105; 401-420 109; 421-440 113; 441-460 117;
461-480 121; 481-500 126; 501-520 130; 521-540 134; 541-560 138; 561-580 142; 581-600 146;
601-620 150; 621-640 154; 641-660 159; 661-680 163; 681-700 167; 701-720 171; 721-740 175;
741-760 179; 761-780 183; 781-800 187; 801-820 192; 821-840 196; 841-860 200; 861-880 204;
881-900 208; 901-920 212; 921-940 216; 941-960 220; 961-980 225; 981-1000 229
"""


def _table_rows():
    for row in T601_TABLE.split(";"):
        zone, price = row.split()
        first, last = map(int, zone.split("-"))
        yield first, last, float(price)


@pytest.mark.parametrize("first,last,price", list(_table_rows()))
def test_distance_tariff_reproduces_the_official_table(first, last, price):
    for km in range(first, last + 1):
        assert fares.t601_full_fare_chf(km) == price, km


def test_part_kilometres_count_as_the_next_kilometre():
    assert fares.t601_full_fare_chf(30.0) == 12.60
    assert fares.t601_full_fare_chf(30.2) == 13.60
    assert fares.t601_full_fare_chf(0.3) == 3.00


def test_half_fare_is_half_with_a_minimum():
    assert fares.half_fare_chf(12.60) == 6.30
    assert fares.half_fare_chf(27.00) == 13.50
    assert fares.half_fare_chf(3.00) == fares.T601_MIN_HALF_FARE_CHF


def test_fare_for_road_km_is_labelled_approximate():
    fare = fares.fare_for_road_km(30)
    assert fare == {"full_chf": 12.60, "half_chf": 6.30, "approximate": True, "source": "t601", "tariff_km": 30.0}
    assert fares.fare_for_road_km(None) is None


LAUSANNE = {"id": "8501120", "name": "Lausanne", "lat": 46.5167, "lon": 6.6291}
GENEVE = {"id": "8501008", "name": "Genève", "lat": 46.2102, "lon": 6.1424}

OJP = 'xmlns="http://www.vdv.de/ojp" xmlns:siri="http://www.siri.org.uk/siri"'
TRIP_DELIVERY = f"""<?xml version="1.0" encoding="UTF-8"?>
<OJP {OJP} version="2.0"><OJPResponse><siri:ServiceDelivery><OJPTripDelivery>
  <TripResult><Id>1</Id><Trip><Id>T1</Id><Duration>PT36M</Duration>
    <Leg><Id>1</Id><TimedLeg><LegBoard><siri:StopPointRef>8501120</siri:StopPointRef></LegBoard>
      <LegAlight><siri:StopPointRef>8501008</siri:StopPointRef></LegAlight></TimedLeg></Leg>
  </Trip></TripResult>
</OJPTripDelivery></siri:ServiceDelivery></OJPResponse></OJP>"""


def _fare_delivery(*results):
    body = "".join(
        f"<TripFareResult><FromLegIdRef>{start}</FromLegIdRef><ToLegIdRef>{end}</ToLegIdRef>{products}</TripFareResult>"
        for start, end, products in results
    )
    return (f'<?xml version="1.0"?><OJP {OJP} version="2.0"><OJPResponse><siri:ServiceDelivery>'
            f"<OJPFareDelivery><FareResult><Id>T1</Id>{body}</FareResult></OJPFareDelivery>"
            "</siri:ServiceDelivery></OJPResponse></OJP>")


def _product(price, fare_class="secondClass", card=None, currency="CHF"):
    card_xml = f"<RequiredCard>{card}</RequiredCard>" if card else ""
    return (f"<FareProduct><FareProductId>1</FareProductId><Price>{price}</Price><Currency>{currency}</Currency>"
            f"{card_xml}<FareClass>{fare_class}</FareClass></FareProduct>")


FARE_DELIVERY = _fare_delivery(("1", "1", _product("27.20") + _product("13.60", card="HTA")
                                + _product("46.20", fare_class="firstClass") + _product("19.90")))


class FakeOjp:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data.decode("utf-8"), headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        resp = requests.Response()
        resp.status_code = 200
        resp._content = answer.encode("utf-8")
        return resp


@pytest.fixture
def ojp(monkeypatch):
    def install(*answers):
        fake = FakeOjp(answers)
        monkeypatch.setenv("OJP_API_KEY", "test-key")
        monkeypatch.setattr(fares.requests, "post", fake.post)
        return fake
    return install


def test_without_an_api_key_no_request_is_made(monkeypatch):
    monkeypatch.setattr(fares.requests, "post", lambda *a, **k: pytest.fail("OJP called without a key"))
    fare = fares.fare_between_stations(LAUSANNE, GENEVE)
    assert fare["source"] == "t601"
    assert fare["approximate"] is True
    # ~51 km apart in a straight line, x 1.25 tariff km.
    assert 60 < fare["tariff_km"] < 66


def test_ojp_fare_is_the_cheapest_second_class_ticket_without_a_travelcard(ojp):
    fake = ojp(TRIP_DELIVERY, FARE_DELIVERY)
    fare = fares.fare_between_stations(LAUSANNE, GENEVE, "2026-12-01T08:00")
    assert fare == {"full_chf": 19.90, "half_chf": 9.95, "approximate": False, "source": "ojp", "tariff_km": None}

    (trip_url, trip_xml, headers), (fare_url, fare_xml, _) = fake.calls
    assert trip_url == fares.OJP_TRIP_URL and fare_url == fares.OJP_FARE_URL
    assert headers["Authorization"] == "Bearer test-key"
    assert "8501120" in trip_xml and "8501008" in trip_xml
    request = ET.fromstring(fare_xml)
    ns = {"ojp": fares.OJP_NS}
    assert request.find(".//ojp:TripFareRequest/ojp:Trip/ojp:Id", ns).text == "T1"
    assert request.find(".//ojp:Params/ojp:FareClass", ns).text == "secondClass"


def test_ojp_fare_is_cached_per_station_pair(ojp):
    fake = ojp(TRIP_DELIVERY, FARE_DELIVERY)
    fares.fare_between_stations(LAUSANNE, GENEVE)
    assert fares.fare_between_stations(LAUSANNE, GENEVE)["full_chf"] == 19.90
    assert len(fake.calls) == 2  # one trip request, one fare request


@pytest.mark.parametrize("answers", [
    [requests.Timeout("slow")],
    [requests.ConnectionError("down")],
    ["<not xml"],
    [TRIP_DELIVERY.replace("<Trip>", "<NoTrip>").replace("</Trip>", "</NoTrip>")],
    [TRIP_DELIVERY, _fare_delivery()],
    [TRIP_DELIVERY, _fare_delivery(("1", "1", _product("10.00", currency="EUR")))],
])
def test_ojp_errors_fall_back_to_the_distance_tariff(ojp, answers):
    fake = ojp(*answers)
    fare = fares.fare_between_stations(LAUSANNE, GENEVE)
    assert fare["source"] == "t601"
    assert fare["approximate"] is True
    # The failure is cached too, briefly: no second try right away.
    calls = len(fake.calls)
    fares.fare_between_stations(LAUSANNE, GENEVE)
    assert len(fake.calls) == calls


def test_ojp_fare_parts_are_added_up():
    delivery = ET.fromstring(_fare_delivery(("1", "1", _product("8.00")), ("3", "3", _product("4.20"))))
    assert fares.parse_ojp_full_fare(delivery) == 12.20
    whole = ET.fromstring(_fare_delivery(("1", "3", _product("11.00")), ("1", "1", _product("8.00"))))
    assert fares.parse_ojp_full_fare(whole) == 11.00


def test_env_without_key_by_default():
    # conftest's no_ojp fixture: tests never reach the real service.
    assert "OJP_API_KEY" not in os.environ
