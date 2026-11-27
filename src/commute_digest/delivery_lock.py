"""Coordinate cloud upgrades with actual in-flight mail jobs on the same host."""

import os
from contextlib import contextmanager


@contextmanager
def delivery_in_progress():
    path = os.getenv("COMMUTE_DELIVERY_LOCK")
    if not path:
        yield
        return
    # The AutoDL launcher sets this path; other hosts need no platform-specific lock.
    import fcntl

    with open(path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        yield
