"""Database helpers for the worker process.

The canonical implementation lives in the repository-root ``shared`` package
(``shared/db.py``); this module resolves the DSN with the worker's own config
loader and exposes the familiar ``query`` / ``execute`` / ``engine`` names so
existing import sites keep working unchanged.
"""
import sys
from pathlib import Path

from config import require_config

# Make the repository-root ``shared`` package importable whether this module is
# running from the local checkout (worker/src/) or from a container mount
# (worker/src is copied to /app, with /app/shared mounted alongside it).
def _find_shared_root() -> Path:
    current = Path(__file__).resolve().parent
    while True:
        if (current / "shared").is_dir():
            return current
        if current.parent == current:
            raise ImportError("Cannot locate the shared/ package from " + str(__file__))
        current = current.parent


_shared_root = _find_shared_root()
if str(_shared_root) not in sys.path:
    sys.path.insert(0, str(_shared_root))

from shared.db import Database, MaterializedResult  # noqa: E402

_db = Database(require_config("DB_DSN"))

engine = _db.engine
query = _db.query
execute = _db.execute
transaction = _db.transaction

__all__ = ["engine", "query", "execute", "transaction", "MaterializedResult", "Database"]
