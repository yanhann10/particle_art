"""File lock around lineage.json writes.
Guards concurrent mutation workers from clobbering each other.
Unix-only (fcntl). No-op on Windows.
"""
import contextlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
_LOCK_FILE = REPO / ".lineage.lock"


@contextlib.contextmanager
def lineage_write_lock():
    if sys.platform == "win32":
        yield
        return
    import fcntl
    lf = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()
