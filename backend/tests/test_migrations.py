"""
The schema migrations (backend/migrations/): well-formed, uniquely named
files, and idempotent: a database that already has today's schema (the
production case), migrated once or twice, ends up with exactly the schema
a new database gets, which is the schema the code created before the
migrations moved to their own files.
"""
import re
import sqlite3
from pathlib import Path

import pytest

import database as db
import migrations

MIGRATIONS_DIR = Path(migrations.__file__).parent
BEFORE = Path(__file__).parent / "schema_before_migrations.sql"


def _schema(path) -> list[tuple]:
    """Every table, index and trigger with its SQL, and every table's
    columns, in a form that doesn't depend on creation order."""
    conn = sqlite3.connect(path)
    try:
        objects = sorted(conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name != 'sqlite_sequence'"
        ))
        columns = [
            (table, list(conn.execute(f"PRAGMA table_info({table})")))
            for kind, table, _, _ in objects if kind == "table"
        ]
        return objects + columns
    finally:
        conn.close()


def _init(monkeypatch, path, times=1):
    monkeypatch.setattr(db, "DB_PATH", str(path))
    for _ in range(times):
        db.init_db()


def _from_snapshot(path):
    conn = sqlite3.connect(path)
    conn.executescript(BEFORE.read_text())
    conn.close()


def test_filenames_well_formed_and_unique():
    files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.py") if p.name != "__init__.py")
    assert files
    for name in files:
        assert re.fullmatch(r"\d{12}_[a-z0-9_]+\.py", name), name
        stamp = name[:12]
        # YYYYMMDDHHMM: a real month, day, hour and minute.
        assert 1 <= int(stamp[4:6]) <= 12 and 1 <= int(stamp[6:8]) <= 31, name
        assert int(stamp[8:10]) <= 23 and int(stamp[10:12]) <= 59, name
    stamps = [name[:12] for name in files]
    assert len(set(stamps)) == len(stamps), "two migrations with the same timestamp"
    # Nothing else in the folder that wouldn't be applied.
    assert not [p.name for p in MIGRATIONS_DIR.iterdir() if p.suffix not in (".py", "") and p.name != "__pycache__"]


def test_modules_in_filename_order_each_with_apply():
    modules = migrations.modules()
    names = [m.__name__.rsplit(".", 1)[1] for m in modules]
    assert names == sorted(names)
    assert len(names) == len(list(MIGRATIONS_DIR.glob("[0-9]*.py")))
    for module in modules:
        assert callable(module.apply), module.__name__


# The last migration whose change is in the snapshot (schema_before_migrations.sql).
# Later ones add to it; the tests below check them against a new database.
SNAPSHOT_UNTIL = "202611181637"


def test_new_database_matches_the_schema_before_migrations(tmp_path, monkeypatch):
    # Exactly what the old init_db() created, statement text included: the
    # migrations up to the snapshot, on a new database.
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "new.db"))
    with db.get_conn() as conn:
        for module in migrations.modules():
            if module.__name__.rsplit(".", 1)[1][:12] <= SNAPSHOT_UNTIL:
                module.apply(conn)
    _from_snapshot(tmp_path / "before.db")
    assert _schema(tmp_path / "new.db") == _schema(tmp_path / "before.db")


def test_existing_database_migrated_twice_matches_a_new_one(tmp_path, monkeypatch):
    existing = tmp_path / "existing.db"
    _from_snapshot(existing)
    conn = sqlite3.connect(existing)
    conn.execute("INSERT INTO users (name, email) VALUES ('Marie M.', 'marie@example.ch')")
    conn.commit()
    conn.close()
    _init(monkeypatch, existing, times=2)
    _init(monkeypatch, tmp_path / "new.db")
    assert _schema(existing) == _schema(tmp_path / "new.db")
    # And the data is still there.
    conn = sqlite3.connect(existing)
    assert conn.execute("SELECT name, email, notify_requests FROM users").fetchall() == [
        ("Marie M.", "marie@example.ch", 1)
    ]
    conn.close()


def test_new_database_migrated_again_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "new.db"
    _init(monkeypatch, path)
    first = _schema(path)
    _init(monkeypatch, path, times=2)
    assert _schema(path) == first


@pytest.mark.parametrize("module", migrations.modules(), ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_each_migration_idempotent(tmp_path, monkeypatch, module):
    # Applying any one migration again on an up-to-date database changes nothing.
    path = tmp_path / "db.db"
    _init(monkeypatch, path)
    before = _schema(path)
    with db.get_conn() as conn:
        module.apply(conn)
    assert _schema(path) == before
