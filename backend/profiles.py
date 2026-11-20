"""
Profiles: what a user chooses to say about themselves, to those they may
ride with. See docs/profiles.md.

Everything is optional:
  - a few words about oneself, and a photo (photos.py);
  - five things one may or may not say, each yes, no or left open:
    talkative, likes to sleep in the car, smoker, travels with a pet, likes
    music in the car;
  - for drivers, their car: make, model, year, colour.

Who may see whose profile (may_see):
  - oneself;
  - a passenger sees a driver's profile while that driver is a candidate
    for a ride they want: the drivers a search of theirs found
    (allow_candidates, from then on), and the drivers of the drives
    they asked for a seat on;
  - a driver sees a passenger's profile once that passenger asked to ride
    with them: a booking on one of their drives (whatever became of it), or
    a ride request of theirs the driver was told about. Not before: the
    list of requests in a driver's areas shows names only.
Nobody else, and nothing here is public. Demo drivers have no profile.

To someone else, a profile shows the first name and last initial others
already see, and what was filled in; never the email address.
"""
import secrets
import database as db
import photos

MAX_ABOUT_CHARS = 500
MAX_CAR_CHARS = 40
MIN_CAR_YEAR = 1950

TRAITS = ("talkative", "sleeps", "smoker", "pets", "music")
CAR = ("car_make", "car_model", "car_year", "car_color")
FIELDS = ("about", *TRAITS, *CAR)

def _row(user_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def _shown(user: dict, row: dict | None) -> dict:
    """A profile as the API gives it: every field, None when not said."""
    row = row or {}
    profile = {"user_id": user["id"], "name": user["name"], "about": row.get("about")}
    for trait in TRAITS:
        profile[trait] = None if row.get(trait) is None else bool(row[trait])
    for field in CAR:
        profile[field] = row.get(field)
    profile["has_photo"] = bool(row.get("photo_key")) and photos.enabled()
    profile["photo_version"] = _photo_version(row.get("photo_key"))
    return profile


def _photo_version(photo_key: str | None) -> str | None:
    """Changes with the photo, so a browser never shows an old one."""
    return (photo_key or "")[-20:-4] or None


def own(user: dict) -> dict:
    return _shown(user, _row(user["id"]))


def save(user: dict, values: dict) -> dict:
    """Replace what the user says about themselves with `values` (FIELDS;
    a missing or None one is cleared). The photo is kept."""
    def text(key, limit):
        value = " ".join(str(values.get(key) or "").split())[:limit]
        return value or None

    about = (values.get("about") or "").strip()[:MAX_ABOUT_CHARS] or None
    traits = [None if values.get(t) is None else int(bool(values[t])) for t in TRAITS]
    year = values.get("car_year")
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO profiles (user_id, about, talkative, sleeps, smoker, pets, music,
                                  car_make, car_model, car_year, car_color, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                about = excluded.about, talkative = excluded.talkative, sleeps = excluded.sleeps,
                smoker = excluded.smoker, pets = excluded.pets, music = excluded.music,
                car_make = excluded.car_make, car_model = excluded.car_model, car_year = excluded.car_year,
                car_color = excluded.car_color, updated_at = excluded.updated_at
            """,
            (user["id"], about, *traits, text("car_make", MAX_CAR_CHARS), text("car_model", MAX_CAR_CHARS),
             year, text("car_color", MAX_CAR_CHARS), db.now_iso()),
        )
    return own(user)


# ---------------------------------------------------------------------
# The photo
# ---------------------------------------------------------------------
def set_photo(user: dict, data: bytes) -> dict:
    """Store the user's photo (a JPEG, checked by the caller), replacing
    the one before. Raises photos.PhotoError."""
    store = photos.storage()
    if store is None:
        raise photos.PhotoError("photos are switched off")
    old = (_row(user["id"]) or {}).get("photo_key")
    key = f"profiles/{user['id']}-{secrets.token_hex(8)}.jpg"  # a new name each time: nothing cached goes stale
    store.put(key, data)
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO profiles (user_id, photo_key, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET photo_key = excluded.photo_key, updated_at = excluded.updated_at
            """,
            (user["id"], key, db.now_iso()),
        )
    _forget(store, old)
    return own(user)


def remove_photo(user_id: int) -> None:
    """Delete the user's photo, if any; also before their account goes."""
    row = _row(user_id)
    if not row or not row["photo_key"]:
        return
    with db.get_conn() as conn:
        conn.execute("UPDATE profiles SET photo_key = NULL, updated_at = ? WHERE user_id = ?", (db.now_iso(), user_id))
    _forget(photos.storage(), row["photo_key"])


def _forget(store, key: str | None) -> None:
    if store is None or not photos.valid_key(key):
        return
    try:
        store.delete(key)
    except photos.PhotoError:
        photos.logger.exception("Deleting the photo %s failed", key)  # an orphan object, not a broken page


def photo(user_id: int) -> bytes | None:
    """The user's photo, or None. Raises photos.PhotoError."""
    row = _row(user_id)
    store = photos.storage()
    if not row or store is None or not photos.valid_key(row["photo_key"]):
        return None
    return store.get(row["photo_key"])


# ---------------------------------------------------------------------
# Who may see whom
# ---------------------------------------------------------------------
def allow_candidates(viewer_id: int, driver_ids: list[int]) -> None:
    """A search of the viewer's found these drivers: the viewer may see
    their profiles from now on."""
    driver_ids = sorted({i for i in driver_ids if i != viewer_id})
    if not driver_ids:
        return
    with db.get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO profile_grants (viewer_id, subject_id) VALUES (?, ?)
            ON CONFLICT (viewer_id, subject_id) DO NOTHING
            """,
            [(viewer_id, driver_id) for driver_id in driver_ids],
        )


def with_driver_photos(matches: list[dict], viewer_id: int | None) -> list[dict]:
    """The matches, each with driver_photo: the photo_version of its
    driver's photo, for a viewer whose search found them (allow_candidates),
    else None -- no photo, a demo drive, photos switched off, or a viewer
    who doesn't get to see profiles (viewer_id None)."""
    for match in matches:
        match["driver_photo"] = None
    driver_ids = sorted({m["driver_id"] for m in matches if not m.get("is_demo") and m.get("driver_id")})
    if viewer_id is None or not driver_ids or not photos.enabled():
        return matches
    with db.get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT p.user_id, p.photo_key FROM profiles p JOIN users u ON u.id = p.user_id
            WHERE p.photo_key IS NOT NULL AND u.banned_at IS NULL
              AND p.user_id IN ({", ".join("?" * len(driver_ids))})
            """,
            driver_ids,
        ).fetchall()
    versions = {row["user_id"]: _photo_version(row["photo_key"]) for row in rows}
    for match in matches:
        if not match.get("is_demo"):
            match["driver_photo"] = versions.get(match["driver_id"])
    return matches


def may_see(viewer_id: int, subject_id: int) -> bool:
    """Whether the viewer may see the subject's profile (see the module's
    description)."""
    if viewer_id == subject_id:
        return True
    with db.get_conn() as conn:
        # A driver one of the viewer's searches found.
        if conn.execute(
            "SELECT 1 FROM profile_grants WHERE viewer_id = ? AND subject_id = ?",
            (viewer_id, subject_id),
        ).fetchone():
            return True
        # The two sides of a booking: the passenger sees the driver, and the
        # driver the passenger who asked for a seat.
        if conn.execute(
            """
            SELECT 1 FROM bookings b JOIN drives d ON d.id = b.drive_id
            WHERE (b.passenger_id = ? AND d.driver_id = ?) OR (b.passenger_id = ? AND d.driver_id = ?)
            """,
            (viewer_id, subject_id, subject_id, viewer_id),
        ).fetchone():
            return True
        # A driver told about the passenger's ride request.
        return conn.execute(
            """
            SELECT 1 FROM notifications n, json_each(n.request_ids) r JOIN open_requests o ON o.id = r.value
            WHERE n.kind = 'request_for_driver' AND n.recipient_id = ? AND o.passenger_id = ?
            """,
            (viewer_id, subject_id),
        ).fetchone() is not None


def visible(viewer: dict, subject_id: int) -> dict | None:
    """The subject's profile as the viewer may see it, or None: no such
    account, a demo driver, or not theirs to see."""
    subject = db.get_user(subject_id)
    if not subject or subject["is_demo"] or not subject["email"] or subject["banned_at"]:
        return None
    if not may_see(viewer["id"], subject_id):
        return None
    return _shown(subject, _row(subject_id))
