"""Logging setup for canary processes.

Every canary process logs to stderr; agents add their own handlers where
they run. Error paths always log — no silent failures.
"""
import logging

from .config import LOG_LEVEL


def setup_logging(name='canary', level=None):
    """Configure root logging and return the named logger."""
    logging.basicConfig(
        level=level or LOG_LEVEL,
        format='%(asctime)s %(name)s %(levelname)s %(message)s')
    return logging.getLogger(name)
