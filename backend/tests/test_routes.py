"""
The routes package (backend/routes/): every module has a router, main.py
includes them all, and the API's paths are all still there.
"""
import importlib
import pkgutil

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import main
import routes

MODULES = sorted(info.name for info in pkgutil.iter_modules(routes.__path__))

# (method, path) of the API, as in the OpenAPI description.
API_ROUTES = {
    ("get", "/api/health"),
    ("get", "/api/auth/providers"),
    ("post", "/api/auth/link"),
    ("post", "/api/auth/verify"),
    ("post", "/api/auth/code"),
    ("get", "/api/auth/me"),
    ("post", "/api/auth/profile"),
    ("post", "/api/auth/sign_out"),
    ("get", "/api/auth/demo_drivers"),
    ("post", "/api/auth/demo"),
    ("delete", "/api/account"),
    ("get", "/api/profile"),
    ("put", "/api/profile"),
    ("put", "/api/profile/photo"),
    ("delete", "/api/profile/photo"),
    ("get", "/api/profiles/{user_id}"),
    ("get", "/api/profiles/{user_id}/photo"),
    ("get", "/api/areas"),
    ("post", "/api/areas"),
    ("put", "/api/areas/{area_id}"),
    ("delete", "/api/areas/{area_id}"),
    ("get", "/api/areas/requests"),
    ("post", "/api/drives"),
    ("get", "/api/drives"),
    ("get", "/api/drives/{drive_id}"),
    ("delete", "/api/drives/{drive_id}"),
    ("post", "/api/drives/{drive_id}/bookings"),
    ("get", "/api/bookings"),
    ("post", "/api/bookings/{booking_id}/accept"),
    ("post", "/api/bookings/{booking_id}/decline"),
    ("post", "/api/bookings/{booking_id}/cancel"),
    ("post", "/api/bookings/{booking_id}/rating"),
    ("post", "/api/bookings/{booking_id}/report"),
    ("get", "/api/price_band"),
    ("post", "/api/match"),
    ("post", "/api/match/transit"),
    ("post", "/api/match/combined"),
    ("get", "/api/ride_requests"),
    ("post", "/api/requests"),
    ("get", "/api/requests"),
    ("delete", "/api/requests/{request_id}"),
    ("post", "/api/requests/{request_id}/accept"),
    ("get", "/api/notifications"),
    ("post", "/api/notifications/read"),
    ("post", "/api/notifications/{notification_id}/action"),
    ("get", "/api/notifications/{notification_id}/preview"),
    ("put", "/api/notification_settings"),
    ("get", "/api/contact"),
    ("post", "/api/contact"),
    ("get", "/api/payments/config"),
    ("post", "/api/bookings/{booking_id}/pay"),
    ("post", "/api/bookings/{booking_id}/pay/check"),
    ("post", "/api/payments/webhook"),
}


def test_modules_found():
    assert {"auth", "drives", "match", "ride_requests", "inbox", "pricing", "pwa", "i18n", "health"} <= set(MODULES)


@pytest.mark.parametrize("name", MODULES)
def test_every_module_has_a_router(name):
    module = importlib.import_module(f"routes.{name}")
    assert isinstance(module.router, APIRouter), name


def test_main_includes_every_module_in_name_order():
    assert [module.__name__ for module in main.route_modules()] == [f"routes.{name}" for name in MODULES]


def test_api_routes_registered():
    paths = main.app.openapi()["paths"]
    registered = {(method, path) for path, operations in paths.items() for method in operations}
    assert registered == API_ROUTES


@pytest.mark.parametrize("path", ["/sw.js", "/.well-known/assetlinks.json", "/i18n/en.json"])
def test_routes_outside_the_api_description(path):
    # Not in OpenAPI (include_in_schema=False), and answered by their route,
    # not the static files after them.
    resp = TestClient(main.app).get(path)
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "") or path.startswith("/.well-known")


def test_static_frontend_mounted_last():
    assert main.app.routes[-1].name == "frontend"
    assert TestClient(main.app).get("/").status_code == 200
