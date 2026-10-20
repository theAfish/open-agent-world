"""Timed startup stages for the launcher log and development terminal."""
from contextlib import contextmanager
import logging
from time import perf_counter


_log = logging.getLogger(__name__)


@contextmanager
def startup_stage(label: str):
    # Labels describe operations, never credentials or persisted user content.
    started = perf_counter()
    _log.info("Startup: %s ...", label)
    try:
        yield
    except BaseException:
        _log.error("Startup: %s failed after %.1fs", label, perf_counter() - started)
        raise
    else:
        _log.info("Startup: %s complete (%.1fs)", label, perf_counter() - started)
