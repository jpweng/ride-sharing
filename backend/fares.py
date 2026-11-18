"""
Swiss public transport fares: what the train costs for a trip, 2nd class,
full fare and half fare (with a Halbtax / half-fare travelcard).

The ride prices in pricing.py are capped at the train's full fare, and the
public-transport option of a search shows it. transport.opendata.ch has no
reliable fares, so there are two sources:

1. The national distance tariff (always available, no network): Alliance
   SwissPass's "T601 Allgemeiner Personentarif", edition of 1 June 2026,
   section 11 (Preise). Its price table for 2nd class, full fare, is
   reproduced exactly by fare_for_tariff_km() below:
     - a rate in centimes per km that falls with distance
       (T601_RATES_RP_PER_KM, section 11.1.3), summed km by km;
     - computed at the last km of the distance's zone (T601_ZONES, 11.1.4:
       4 km zones up to 8 km, 2 km up to 30 km, 3 km up to 60 km...);
     - rounded up to 20 centimes, from 69 km up to the next franc (11.2.1);
     - at least CHF 3.00 (11.2.1).
   Half fare is the full fare halved, not rounded, at least CHF 2.20
   (sections 2.4.1 and 11.2.1). E.g. 30 km: CHF 12.60 full, CHF 6.30 half.

   The table is official, but the result is labelled approximate: true,
   because what goes into it is not. The table is applied to "tariff km",
   the distances of the operators' own tariff tables, which we don't have:
     - for a ride (a drive or the part of it a passenger rides), we use the
       road distance of the car's route (OSRM) as the tariff distance
       (TARIFF_KM_PER_ROAD_KM = 1). Between Swiss towns, rail and road
       distances are usually close: both follow the valleys. Mountain lines
       (with surcharged tariff km) and lake detours cost more by train;
     - between two stations with no road route at hand, the straight-line
       distance times TARIFF_KM_PER_STRAIGHT_KM;
     - some trips are priced otherwise: fare networks (Tarifverbünde, e.g.
       ZVV, Mobilis, Libero) sell zone tickets inside their area, and a few
       main lines have their own route prices. Saver tickets can be cheaper.

2. The real fare from opentransportdata.swiss, when OJP_API_KEY is set: an
   OJP 2.0 trip request between the two stations, then an OJPFare request
   (the beta price service backed by NOVA, the system that sells Swiss
   tickets) for that trip, 2nd class, adult without travelcard. The cheapest
   such ticket is taken as the full fare (approximate: false). Any error,
   timeout or unexpected answer falls back to the distance tariff. Answers
   are cached per station pair (FARE_CACHE_TTL_S), failures more briefly.
"""
import logging
import math
import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

import matching

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------
# The national distance tariff (T601, edition 2026-06-01)
# ---------------------------------------------------------------------
T601_EDITION = "2026-06-01"

# Centimes per km, 2nd class: (last km of the band, rate). Section 11.1.3.
T601_RATES_RP_PER_KM = (
    (4, 46.23),
    (14, 43.93),
    (48, 38.67),
    (150, 27.55),
    (200, 26.70),
    (250, 23.73),
    (300, 21.42),
    (480, 20.87),
    (1500, 20.61),
)

# Zones the price is computed in: (last km, zone width in km). Section 11.1.4.
T601_ZONES = (
    (8, 4),
    (30, 2),
    (60, 3),
    (100, 4),
    (150, 5),
    (300, 10),
    (1500, 20),
)
T601_MAX_KM = 1500

# Section 11.2.1: rounded up to 20 centimes, from this distance up to the franc.
T601_ROUND_CHF = 0.20
T601_ROUND_TO_FRANC_FROM_KM = 69
T601_MIN_FULL_FARE_CHF = 3.00
T601_MIN_HALF_FARE_CHF = 2.20

# Tariff km from the distances we have (see the module docstring).
TARIFF_KM_PER_ROAD_KM = 1.0
TARIFF_KM_PER_STRAIGHT_KM = 1.25


def _round_up(chf: float, step: float) -> float:
    # The epsilon keeps 12.60 (= 63 x 0.20 in floating point) from becoming 12.80.
    return round(math.ceil(chf / step - 1e-9) * step, 2)


def _zone_end_km(km: int) -> int:
    """The last km of the T601 zone `km` falls in (1-4 -> 4, 29-30 -> 30, 31-33 -> 33)."""
    start = 1
    for last_km, width in T601_ZONES:
        if km <= last_km:
            return start + math.ceil((km - start + 1) / width) * width - 1
        start = last_km + 1
    return T601_MAX_KM


def t601_full_fare_chf(tariff_km: float) -> float:
    """2nd class full fare of the T601 price table for a tariff distance."""
    km = min(max(1, math.ceil(tariff_km - 1e-9)), T601_MAX_KM)
    end = _zone_end_km(km)
    centimes, counted = 0.0, 0
    for last_km, rate in T601_RATES_RP_PER_KM:
        in_band = min(end, last_km) - counted
        if in_band <= 0:
            break
        centimes += in_band * rate
        counted += in_band
    step = 1.0 if end >= T601_ROUND_TO_FRANC_FROM_KM else T601_ROUND_CHF
    return max(T601_MIN_FULL_FARE_CHF, _round_up(centimes / 100.0, step))


def half_fare_chf(full_fare_chf: float) -> float:
    """Half fare: the full fare halved, not rounded, at least CHF 2.20 (T601 2.4.1)."""
    return max(T601_MIN_HALF_FARE_CHF, round(full_fare_chf / 2.0, 2))


def _fare(full_chf: float, source: str, approximate: bool, tariff_km: float | None = None) -> dict:
    return {
        "full_chf": full_chf,
        "half_chf": half_fare_chf(full_chf),
        "approximate": approximate,
        "source": source,  # "t601": distance tariff, "ojp": opentransportdata.swiss
        "tariff_km": round(tariff_km, 1) if tariff_km is not None else None,
    }


def fare_for_road_km(road_km: float | None) -> dict | None:
    """Approximate train fare for a trip of `road_km` by road; None if unknown."""
    if road_km is None or road_km <= 0:
        return None
    tariff_km = road_km * TARIFF_KM_PER_ROAD_KM
    return _fare(t601_full_fare_chf(tariff_km), "t601", True, tariff_km)


def fare_for_straight_km(straight_km: float) -> dict:
    """Approximate train fare between two points `straight_km` apart."""
    tariff_km = max(straight_km, 0.1) * TARIFF_KM_PER_STRAIGHT_KM
    return _fare(t601_full_fare_chf(tariff_km), "t601", True, tariff_km)


# ---------------------------------------------------------------------
# Real fares from OJP (opentransportdata.swiss), optional
# ---------------------------------------------------------------------
OJP_TRIP_URL = os.environ.get("OJP_TRIP_URL", "https://api.opentransportdata.swiss/ojp20")
OJP_FARE_URL = os.environ.get("OJP_FARE_URL", "https://api.opentransportdata.swiss/ojpfare/")
OJP_TIMEOUT_S = 4
OJP_REQUESTOR_REF = "ride-and-rail"

OJP_NS = "http://www.vdv.de/ojp"
SIRI_NS = "http://www.siri.org.uk/siri"
ET.register_namespace("", OJP_NS)
ET.register_namespace("siri", SIRI_NS)

FARE_CACHE_TTL_S = 24 * 3600
FARE_CACHE_FAILURE_TTL_S = 10 * 60
FARE_CACHE_MAX_ENTRIES = 5000

_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def _api_key() -> str | None:
    return os.environ.get("OJP_API_KEY") or None


def _fare_api_key() -> str | None:
    # The fare service may need a key of its own on the API manager.
    return os.environ.get("OJP_FARE_API_KEY") or _api_key()


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_xml(url: str, key: str, body: str) -> ET.Element:
    resp = requests.post(
        url,
        data=body.encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/xml; charset=utf-8"},
        timeout=OJP_TIMEOUT_S,
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _service_request(payload: ET.Element) -> str:
    root = ET.Element(_q(OJP_NS, "OJP"), {"version": "2.0"})
    service = ET.SubElement(ET.SubElement(root, _q(OJP_NS, "OJPRequest")), _q(SIRI_NS, "ServiceRequest"))
    ET.SubElement(service, _q(SIRI_NS, "RequestTimestamp")).text = _now_utc()
    ET.SubElement(service, _q(SIRI_NS, "RequestorRef")).text = OJP_REQUESTOR_REF
    service.append(payload)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def ojp_trip_request_xml(from_id: str, to_id: str, when_iso: str | None = None) -> str:
    request = ET.Element(_q(OJP_NS, "OJPTripRequest"))
    ET.SubElement(request, _q(SIRI_NS, "RequestTimestamp")).text = _now_utc()
    for tag, stop_id in (("Origin", from_id), ("Destination", to_id)):
        place = ET.SubElement(ET.SubElement(request, _q(OJP_NS, tag)), _q(OJP_NS, "PlaceRef"))
        ET.SubElement(place, _q(SIRI_NS, "StopPointRef")).text = str(stop_id)
    if when_iso:
        try:
            # Naive times are Swiss local time; OJP accepts an offset-less local time.
            ET.SubElement(request.find(_q(OJP_NS, "Origin")), _q(OJP_NS, "DepArrTime")).text = (
                datetime.fromisoformat(when_iso).replace(tzinfo=None).isoformat(timespec="seconds")
            )
        except ValueError:
            pass
    params = ET.SubElement(request, _q(OJP_NS, "Params"))
    ET.SubElement(params, _q(OJP_NS, "NumberOfResults")).text = "1"
    ET.SubElement(params, _q(OJP_NS, "IncludeIntermediateStops")).text = "true"
    return _service_request(request)


def ojp_fare_request_xml(trip: ET.Element) -> str:
    request = ET.Element(_q(OJP_NS, "OJPFareRequest"))
    ET.SubElement(request, _q(SIRI_NS, "RequestTimestamp")).text = _now_utc()
    ET.SubElement(request, _q(OJP_NS, "TripFareRequest")).append(trip)
    params = ET.SubElement(request, _q(OJP_NS, "Params"))
    ET.SubElement(params, _q(OJP_NS, "FareAuthorityFilter")).text = "ch:1:NOVA"
    ET.SubElement(params, _q(OJP_NS, "PassengerCategory")).text = "Adult"
    ET.SubElement(params, _q(OJP_NS, "FareClass")).text = "secondClass"
    traveller = ET.SubElement(params, _q(OJP_NS, "Traveller"))
    ET.SubElement(traveller, _q(OJP_NS, "PassengerCategory")).text = "Adult"
    return _service_request(request)


def parse_ojp_full_fare(delivery: ET.Element) -> float | None:
    """
    The full fare in an OJPFare delivery: per part of the trip
    (TripFareResult, from leg to leg), the cheapest 2nd class ticket that
    needs no travelcard. A result covering the whole trip wins; otherwise
    the parts are added up. None if there's no usable price.
    """
    parts: dict[tuple[str, str], float] = {}
    for result in delivery.iter(_q(OJP_NS, "TripFareResult")):
        span = (result.findtext(_q(OJP_NS, "FromLegIdRef"), ""), result.findtext(_q(OJP_NS, "ToLegIdRef"), ""))
        for product in result.iter(_q(OJP_NS, "FareProduct")):
            if product.find(_q(OJP_NS, "RequiredCard")) is not None:
                continue  # Halbtax and other travelcard prices
            fare_class = product.findtext(_q(OJP_NS, "FareClass"))
            if fare_class and fare_class != "secondClass":
                continue
            currency = product.findtext(_q(OJP_NS, "Currency"))
            if currency and currency != "CHF":
                continue
            try:
                price = float(product.findtext(_q(OJP_NS, "Price"), ""))
            except ValueError:
                continue
            if price > 0:
                parts[span] = min(price, parts.get(span, math.inf))
    if not parts:
        return None

    def leg_order(leg_id: str):
        return (0, int(leg_id), "") if leg_id.isdigit() else (1, 0, leg_id)

    first = min((span[0] for span in parts), key=leg_order)
    last = max((span[1] for span in parts), key=leg_order)
    if (first, last) in parts:
        return round(parts[(first, last)], 2)
    return round(sum(parts.values()), 2)


def ojp_full_fare(from_id: str, to_id: str, when_iso: str | None = None) -> float | None:
    """The real 2nd class full fare between two stations (by id), or None
    without an API key or on any error."""
    key = _api_key()
    if not key or not from_id or not to_id:
        return None
    try:
        trips = _post_xml(OJP_TRIP_URL, key, ojp_trip_request_xml(from_id, to_id, when_iso))
        trip = next(trips.iter(_q(OJP_NS, "Trip")), None)
        if trip is None:
            return None
        delivery = _post_xml(OJP_FARE_URL, _fare_api_key(), ojp_fare_request_xml(trip))
        return parse_ojp_full_fare(delivery)
    except (requests.RequestException, ET.ParseError, ValueError) as err:
        logger.warning("OJP fare %s -> %s failed: %s", from_id, to_id, err)
        return None


def _cached_ojp_full_fare(from_id: str, to_id: str, when_iso: str | None) -> float | None:
    key = (str(from_id), str(to_id))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    full = ojp_full_fare(from_id, to_id, when_iso)
    ttl = FARE_CACHE_TTL_S if full is not None else FARE_CACHE_FAILURE_TTL_S
    with _cache_lock:
        if len(_cache) >= FARE_CACHE_MAX_ENTRIES:
            for old in [k for k, (expires, _) in _cache.items() if expires <= now] or list(_cache)[: len(_cache) // 2]:
                _cache.pop(old, None)
        _cache[key] = (now + ttl, full)
    return full


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def fare_between_stations(from_station: dict, to_station: dict, when_iso: str | None = None) -> dict | None:
    """
    Train fare between two stations ({id, name, lat, lon}, as transit.py
    resolves them): the real fare from OJP when configured and it answers,
    else the distance tariff on the straight-line distance between them.
    """
    if not from_station or not to_station:
        return None
    if _api_key() and from_station.get("id") and to_station.get("id"):
        full = _cached_ojp_full_fare(from_station["id"], to_station["id"], when_iso)
        if full is not None:
            return _fare(full, "ojp", False)
    try:
        straight_km = matching.haversine_km(from_station["lat"], from_station["lon"], to_station["lat"], to_station["lon"])
    except (KeyError, TypeError):
        return None
    return fare_for_straight_km(straight_km)

