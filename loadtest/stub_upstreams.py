"""
Stand-ins for the app's upstream APIs during a load test, so a test never
loads someone else's server: OSRM (/route/v1/driving/...) and
transport.opendata.ch (/v1/locations, /v1/connections).

    uv run python loadtest/stub_upstreams.py [--port 8081]
        [--locations-delay 0.15] [--connections-delay 0.5]

then start the app with OSRM_URL=http://127.0.0.1:8081 and
TRANSPORT_API_URL=http://127.0.0.1:8081/v1 (see docs/load-test.md).

Routes look like real OSRM ones to the app: a gently winding line about 1.3
times the straight-line distance, a point every ~25 m (the size of the
geometries OSRM returns), at 75 km/h on average. Transit answers come after
the given delays, roughly transport.opendata.ch's response times, since
each match waits for up to ~14 of them.
"""
import argparse
import json
import math
import random
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

KM_PER_DEGREE = 111.32
POINT_SPACING_KM = 0.025
DETOUR = 1.3
SPEED_KMH = 75.0


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def fake_route(lon1, lat1, lon2, lat2):
    crow_km = haversine_km(lat1, lon1, lat2, lon2)
    n = max(2, int(crow_km * DETOUR / POINT_SPACING_KM))
    # A sideways wiggle, a few % of the length, back to zero at both ends.
    amplitude = 0.04 * crow_km / KM_PER_DEGREE
    dx, dy = lon2 - lon1, lat2 - lat1
    norm = math.hypot(dx, dy) or 1.0
    px, py = -dy / norm, dx / norm
    coords = []
    for i in range(n + 1):
        t = i / n
        side = amplitude * math.sin(math.pi * t) * math.sin(6 * math.pi * t)
        coords.append([round(lon1 + dx * t + px * side, 6), round(lat1 + dy * t + py * side, 6)])
    distance_m = crow_km * DETOUR * 1000
    return {
        "code": "Ok",
        "routes": [{
            "distance": distance_m,
            "duration": distance_m / 1000 / SPEED_KMH * 3600,
            "geometry": {"type": "LineString", "coordinates": coords},
        }],
    }


def fake_stations(lat, lon):
    rng = random.Random(f"{lat:.3f},{lon:.3f}")
    stations = []
    for i in range(3):
        s_lat = lat + rng.uniform(-0.004, 0.004)
        s_lon = lon + rng.uniform(-0.006, 0.006)
        stations.append({
            "id": str(8500000 + rng.randrange(99999)),
            "name": f"Stop {s_lat:.3f}/{s_lon:.3f}",
            "icon": "train" if i == 0 else "bus",
            "coordinate": {"type": "WGS84", "x": s_lat, "y": s_lon},
            "distance": int(haversine_km(lat, lon, s_lat, s_lon) * 1000),
        })
    return {"stations": sorted(stations, key=lambda s: s["distance"])}


def fake_connections(from_name, to_name, date, time_):
    try:
        start = datetime.fromisoformat(f"{date}T{time_}")
    except ValueError:
        start = datetime.now()
    connections = []
    for i in range(3):
        dep = start + timedelta(minutes=7 + 30 * i)
        arr = dep + timedelta(minutes=42)
        connections.append({
            "from": {"departure": dep.strftime("%Y-%m-%dT%H:%M:%S+0100"),
                     "station": {"id": "8507000", "name": from_name}},
            "to": {"arrival": arr.strftime("%Y-%m-%dT%H:%M:%S+0100"),
                   "station": {"id": "8503000", "name": to_name}},
            "duration": "00d00:42:00",
            "transfers": 1,
            "sections": [
                {"journey": {"category": "IR", "number": "15", "operator": "SBB"}},
                {"journey": {"category": "B", "number": "10", "operator": "BERNMOBIL"}},
            ],
        })
    return {"connections": connections}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like the real servers
    locations_delay = 0.0
    connections_delay = 0.0

    def do_GET(self):
        url = urlsplit(self.path)  # not urlparse: it cuts the path at ";"
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path.startswith("/route/v1/driving/"):
                (lon1, lat1), (lon2, lat2) = [
                    map(float, p.split(",")) for p in url.path.rsplit("/", 1)[1].split(";")[:2]
                ]
                body = fake_route(lon1, lat1, lon2, lat2)
            elif url.path == "/v1/locations":
                time.sleep(self.locations_delay)
                if "x" in q:
                    body = fake_stations(float(q["x"]), float(q["y"]))
                else:
                    body = {"stations": []}  # by name: no match, callers fall back
            elif url.path == "/v1/connections":
                time.sleep(self.connections_delay)
                body = fake_connections(q.get("from", ""), q.get("to", ""), q.get("date", ""), q.get("time", ""))
            else:
                self.send_error(404)
                return
        except (ValueError, KeyError):
            self.send_error(400)
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    # The default listen backlog of 5 drops connections under load: the
    # app then waits out TCP retries, and the stub, not the app, would set
    # the response times.
    request_queue_size = 1024
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--locations-delay", type=float, default=0.15)
    parser.add_argument("--connections-delay", type=float, default=0.5)
    args = parser.parse_args()
    Handler.locations_delay = args.locations_delay
    Handler.connections_delay = args.connections_delay
    server = Server(("127.0.0.1", args.port), Handler)

    print(f"Stub OSRM + transport API on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
