"""
Database schema changes, one file per change, applied in filename order by
database.init_db() at every start.

A file is named YYYYMMDDHHMM_short_name.py (when the change was made) and
has `def apply(conn)`, which receives an open sqlite3 connection. There is
no record of which files have run: every one of them runs at every start,
on new databases and on existing ones alike, so each must be idempotent
(CREATE ... IF NOT EXISTS, add a column only when it's missing, ...) and
only make additive, backward-compatible changes (new tables, indexes,
nullable or defaulted columns). backend/tests/test_migrations.py checks
that running them on an existing database gives the same schema as on a
new one.

A new schema change is a new file here; files already merged are not
edited, except to keep them working.
"""
import importlib
import pkgutil
import re

NAME = re.compile(r"^\d{12}_[a-z0-9_]+$")


def modules() -> list:
    """Every migration module, in filename order."""
    names = sorted(info.name for info in pkgutil.iter_modules(__path__))
    for name in names:
        if not NAME.match(name):
            raise RuntimeError(f"backend/migrations/{name}.py: expected YYYYMMDDHHMM_short_name.py")
    return [importlib.import_module(f"{__name__}.{name}") for name in names]
