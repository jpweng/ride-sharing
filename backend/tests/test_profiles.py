"""
Profiles (profiles.py, photos.py, routes/profiles.py): what one can say
about oneself, the photo, and above all who may see whose profile. Isolated
temp DB and haversine routing, photos in a temporary folder -- no network.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import areas
import auth
import database as db
import main
import maintenance
import photos
import profiles
from routes import auth as auth_routes
from routes import bookings as booking_routes
from routes import drives as drive_routes
from routes import match as match_routes
from routes import profiles as profile_routes
from routes import ride_requests as request_routes

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
IN_TWO_DAYS = (date.today() + timedelta(days=2)).isoformat()
LAUSANNE = {"name": "Lausanne", "lat": 46.5197, "lon": 6.6323}
GENEVE = {"name": "Genève", "lat": 46.2044, "lon": 6.1432}
JPEG = b"\xff\xd8\xff\xe0" + b"a photo" * 20 + b"\xff\xd9"


@pytest.fixture
def users(isolated_db, force_haversine, fake_transit):
    """Accounts (with an email address), as real users are."""
    def make(name, email):
        user = db.create_account(email, f"~{name}", "en")
        first, _, last = name.partition(" ")
        return db.set_profile(user["id"], auth.CONDUCT_VERSION, name, first, last)
    return {name.split()[0].lower(): make(name, f"{name.split()[0].lower()}@example.ch")
            for name in ("Jean D.", "Marie M.", "Paul P.", "Zoe Z.")}


@pytest.fixture
def photo_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PHOTO_BUCKET", raising=False)
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))
    return tmp_path / "photos"


def _session(user):
    return {"user": user, "session": {"is_demo": 0}, "extended": False}


def _drive(driver, at="08:00"):
    return drive_routes.post_drive(drive_routes.DriveCreate(
        origin=LAUSANNE, destination=GENEVE, departure_time=f"{TOMORROW}T{at}", seats_available=3,
    ), driver)


def _search(passenger):
    return match_routes.match_ride(
        match_routes.MatchRequest(origin=LAUSANNE, destination=GENEVE, requested_time=f"{TOMORROW}T08:00"),
        _session(passenger),
    )["driver_matches"]


def _sees(viewer, subject):
    try:
        return profile_routes.get_profile(subject["id"], viewer)
    except HTTPException as e:
        assert (e.status_code, e.detail["code"]) == (404, "profile_not_found")
        return None


# --- One's own profile -------------------------------------------------------------

def test_a_profile_starts_empty_and_everything_is_optional(users, monkeypatch):
    monkeypatch.delenv("PHOTO_BUCKET", raising=False)
    monkeypatch.delenv("PHOTO_DIR", raising=False)
    jean = users["jean"]
    empty = profile_routes.get_own_profile(jean)
    assert empty == {
        "user_id": jean["id"], "name": "Jean D.", "about": None,
        "talkative": None, "sleeps": None, "smoker": None, "pets": None, "music": None,
        "car_make": None, "car_model": None, "car_year": None, "car_color": None,
        "has_photo": False, "photo_version": None, "photos_enabled": False,
    }
    # Saving nothing at all is fine.
    assert profile_routes.save_own_profile(profile_routes.ProfileUpdate(), jean) == empty


def test_saving_and_clearing_fields(users):
    jean = users["jean"]
    saved = profile_routes.save_own_profile(profile_routes.ProfileUpdate(
        about="  I commute to Geneva three days a week.  ", talkative=True, smoker=False,
        car_make=" Škoda ", car_model="Octavia", car_year=2019, car_color="grey",
    ), jean)
    assert saved["about"] == "I commute to Geneva three days a week."
    assert (saved["talkative"], saved["smoker"], saved["sleeps"], saved["pets"], saved["music"]) == (True, False, None, None, None)
    assert (saved["car_make"], saved["car_model"], saved["car_year"], saved["car_color"]) == ("Škoda", "Octavia", 2019, "grey")
    # A later save replaces everything: what's left out is cleared.
    again = profile_routes.save_own_profile(profile_routes.ProfileUpdate(sleeps=True), jean)
    assert (again["about"], again["talkative"], again["sleeps"], again["car_make"]) == (None, None, True, None)


def test_what_isnt_accepted():
    for bad in ({"about": "x" * 501}, {"car_year": 1900}, {"car_year": 2101}, {"car_make": "x" * 41}):
        with pytest.raises(ValidationError):
            profile_routes.ProfileUpdate(**bad)


# --- Who may see whose profile -----------------------------------------------------

def test_nobody_sees_a_profile_without_a_reason(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    profile_routes.save_own_profile(profile_routes.ProfileUpdate(about="Driver"), jean)
    _drive(jean)  # a posted drive alone opens nothing
    assert _sees(marie, jean) is None
    assert _sees(jean, marie) is None
    assert _sees(paul, {"id": 9999}) is None
    assert _sees(jean, jean)["about"] == "Driver"


def test_a_passenger_sees_the_drivers_their_search_found(users):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    profile_routes.save_own_profile(profile_routes.ProfileUpdate(about="Driver", car_make="Škoda", music=True), jean)
    _drive(jean)
    assert [m["driver_name"] for m in _search(marie)] == ["Jean D."]

    seen = _sees(marie, jean)
    assert (seen["name"], seen["about"], seen["car_make"], seen["music"]) == ("Jean D.", "Driver", "Škoda", True)
    assert "email" not in seen and "photos_enabled" not in seen
    # Only Marie, who searched; and not the other way round.
    assert _sees(paul, jean) is None
    assert _sees(jean, marie) is None


def test_a_search_without_signing_in_opens_nothing(users):
    jean = users["jean"]
    _drive(jean)
    match_routes.match_ride(match_routes.MatchRequest(origin=LAUSANNE, destination=GENEVE, requested_time=f"{TOMORROW}T08:00"), None)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM profile_grants").fetchone()[0] == 0


def test_a_candidate_stays_visible_for_two_days(users, monkeypatch):
    jean, marie = users["jean"], users["marie"]
    _drive(jean)
    _search(marie)
    later = datetime.now(timezone.utc) + profiles.CANDIDATE_TTL
    monkeypatch.setattr(profiles, "_now", lambda: later - timedelta(minutes=1))
    assert _sees(marie, jean) is not None
    monkeypatch.setattr(profiles, "_now", lambda: later + timedelta(minutes=1))
    assert _sees(marie, jean) is None
    assert maintenance.purge_old_data(datetime.fromisoformat(f"{TOMORROW}T00:00"))["profile_grants"] == 1


def test_a_driver_sees_a_passenger_once_they_ask_for_a_seat(users):
    jean, marie = users["jean"], users["marie"]
    profile_routes.save_own_profile(profile_routes.ProfileUpdate(about="Passenger", sleeps=True), marie)
    drive = _drive(jean)
    assert _sees(jean, marie) is None
    booking = booking_routes.request_booking(drive["id"], booking_routes.BookingCreate(), marie)
    assert _sees(jean, marie)["about"] == "Passenger"
    # And the passenger the driver, even after the search's two days.
    assert _sees(marie, jean) is not None
    # Still so once the driver has answered.
    booking_routes.decline_booking(booking["id"], jean)
    assert _sees(jean, marie) is not None
    # The booking's sides are told who to ask the profile of.
    [as_driver] = booking_routes.get_bookings(marie)["as_passenger"]
    assert (as_driver["passenger_id"], as_driver["drive"]["driver_id"]) == (marie["id"], jean["id"])


def test_a_driver_told_about_a_ride_request_sees_the_passenger(users):
    zoe, paul, marie = users["zoe"], users["paul"], users["marie"]
    assert areas.add(zoe["id"], "Lausanne", LAUSANNE["lat"], LAUSANNE["lon"], 5)
    result = request_routes.create_ride_request(match_routes.MatchRequest(
        origin=LAUSANNE, destination=GENEVE, requested_time=f"{IN_TWO_DAYS}T08:00",
    ), marie)
    assert result["drivers_told"] == 1
    # Zoe was told; Paul only sees it in a list, if at all.
    assert _sees(zoe, marie) is not None
    assert _sees(paul, marie) is None
    # The request in Zoe's inbox says whose profile to ask for.
    from routes import inbox as inbox_routes
    [n] = inbox_routes.get_notifications({**zoe, "is_demo": False})["notifications"]
    assert n["request"]["passenger_id"] == marie["id"]


def test_demo_drivers_and_banned_accounts_have_no_profile_to_show(users):
    jean, marie = users["jean"], users["marie"]
    _drive(jean)
    _search(marie)
    db.set_banned("jean@example.ch", True)
    assert _sees(marie, jean) is None
    demo_id = db.get_or_create_user("Demo Driver", is_demo=True)
    profiles.allow_candidates(marie["id"], [demo_id])
    assert _sees(marie, {"id": demo_id}) is None


# --- The photo ---------------------------------------------------------------------

@pytest.fixture
def client(users, photo_dir, monkeypatch):
    """Signed in as Jean, through HTTP."""
    def as_user(user):
        c = TestClient(main.app, base_url="https://testserver")
        token = auth.start_session(user["id"])
        c.cookies.set("__Host-session", token)
        return c
    return as_user


def test_adding_replacing_and_removing_a_photo(users, client, photo_dir):
    jean = client(users["jean"])
    assert jean.get("/api/profile").json()["photos_enabled"] is True
    r = jean.put("/api/profile/photo", content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["has_photo"] is True
    first = r.json()["photo_version"]
    assert len(list(photo_dir.rglob("*.jpg"))) == 1

    photo = jean.get(f"/api/profiles/{users['jean']['id']}/photo")
    assert photo.status_code == 200 and photo.content == JPEG
    assert photo.headers["content-type"] == "image/jpeg" and "private" in photo.headers["cache-control"]

    # A new one replaces it, under a new name; the old file is gone.
    other = JPEG[:-2] + b"more" + JPEG[-2:]
    second = jean.put("/api/profile/photo", content=other).json()["photo_version"]
    assert second != first and len(list(photo_dir.rglob("*.jpg"))) == 1
    assert jean.get(f"/api/profiles/{users['jean']['id']}/photo").content == other

    assert jean.delete("/api/profile/photo").json()["has_photo"] is False
    assert list(photo_dir.rglob("*.jpg")) == []
    assert jean.get(f"/api/profiles/{users['jean']['id']}/photo").status_code == 404


def test_only_a_small_jpeg_is_taken(users, client):
    jean = client(users["jean"])
    assert jean.put("/api/profile/photo", content=b"GIF89a not a jpeg").json()["detail"]["code"] == "photo_invalid"
    too_large = b"\xff\xd8\xff" + b"x" * photos.MAX_BYTES + b"\xff\xd9"
    r = jean.put("/api/profile/photo", content=too_large)
    assert (r.status_code, r.json()["detail"]["code"]) == (413, "photo_too_large")
    assert jean.get("/api/profile").json()["has_photo"] is False


def test_a_photo_is_seen_by_those_who_may_see_the_profile(users, client):
    jean, marie, paul = users["jean"], users["marie"], users["paul"]
    client(jean).put("/api/profile/photo", content=JPEG)
    _drive(jean)
    _search(marie)
    assert client(marie).get(f"/api/profiles/{jean['id']}/photo").content == JPEG
    assert client(marie).get(f"/api/profiles/{jean['id']}").json()["has_photo"] is True
    r = client(paul).get(f"/api/profiles/{jean['id']}/photo")
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "profile_not_found")
    signed_out = TestClient(main.app, base_url="https://testserver").get(f"/api/profiles/{jean['id']}/photo")
    assert signed_out.status_code == 401


def test_without_storage_there_are_no_photos(users, client, monkeypatch):
    monkeypatch.delenv("PHOTO_DIR")
    jean = client(users["jean"])
    assert jean.get("/api/profile").json()["photos_enabled"] is False
    r = jean.put("/api/profile/photo", content=JPEG)
    assert (r.status_code, r.json()["detail"]["code"]) == (503, "photos_unavailable")
    # The rest of the profile works.
    assert jean.put("/api/profile", json={"about": "Hello"}).json()["about"] == "Hello"


def test_a_failing_storage_doesnt_break_the_profile(users, client, monkeypatch):
    jean = client(users["jean"])
    jean.put("/api/profile/photo", content=JPEG)

    def broken(*args):
        raise photos.PhotoError("the bucket is unreachable")
    monkeypatch.setattr(photos.storage(), "get", broken)
    assert jean.get(f"/api/profiles/{users['jean']['id']}/photo").status_code == 404
    assert jean.get("/api/profile").status_code == 200
    monkeypatch.setattr(photos.storage(), "put", broken)
    assert jean.put("/api/profile/photo", content=JPEG).json()["detail"]["code"] == "photos_unavailable"


def test_deleting_the_account_deletes_the_profile_and_the_photo(users, client, photo_dir):
    jean, marie = users["jean"], users["marie"]
    client(jean).put("/api/profile/photo", content=JPEG)
    profile_routes.save_own_profile(profile_routes.ProfileUpdate(about="Driver"), jean)
    _drive(jean)
    _search(marie)

    class _Response:
        def delete_cookie(self, *args, **kwargs):
            pass
    auth_routes.delete_account(_Response(), {"user": jean, "session": {"is_demo": 0}})

    assert list(photo_dir.rglob("*.jpg")) == []
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM profile_grants").fetchone()[0] == 0
    assert _sees(marie, jean) is None


def test_the_s3_storage_talks_to_the_bucket(monkeypatch):
    calls = []

    class Client:
        def put_object(self, **kw):
            calls.append(("put", kw["Bucket"], kw["Key"], kw["ContentType"]))

        def get_object(self, **kw):
            calls.append(("get", kw["Bucket"], kw["Key"]))

            class Body:
                def read(self):
                    return JPEG
            return {"Body": Body()}

        def delete_object(self, **kw):
            calls.append(("delete", kw["Bucket"], kw["Key"]))

    import boto3
    monkeypatch.setattr(boto3, "client", lambda service, region_name=None: Client())
    monkeypatch.setenv("PHOTO_BUCKET", "ride-photos")
    monkeypatch.setenv("PHOTO_DIR", "/ignored/when/a/bucket/is/set")
    store = photos.storage()
    assert isinstance(store, photos.S3Storage) and photos.enabled()
    store.put("profiles/1-0123456789abcdef.jpg", JPEG)
    assert store.get("profiles/1-0123456789abcdef.jpg") == JPEG
    store.delete("profiles/1-0123456789abcdef.jpg")
    assert calls == [
        ("put", "ride-photos", "profiles/1-0123456789abcdef.jpg", "image/jpeg"),
        ("get", "ride-photos", "profiles/1-0123456789abcdef.jpg"),
        ("delete", "ride-photos", "profiles/1-0123456789abcdef.jpg"),
    ]


@pytest.mark.parametrize("method, path", [
    ("get", "/api/profile"), ("put", "/api/profile"), ("put", "/api/profile/photo"),
    ("delete", "/api/profile/photo"), ("get", "/api/profiles/1"), ("get", "/api/profiles/1/photo"),
])
def test_profiles_need_a_session(isolated_db, method, path):
    resp = TestClient(main.app, base_url="https://testserver").request(method, path, json={} if method == "put" else None)
    assert resp.status_code == 401
