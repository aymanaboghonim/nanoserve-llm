"""Structured logging helpers (stdlib only — zero extra dependencies).

Every runner (doctor CLI, Locust, evals) uses the same formatter so logs stay
consistent across phases. Log records render as::

    INFO  [nanoserve_llm.config] loaded settings (ram=12.0GB, vram=8.0GB)
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a configured logger for ``name``.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.
        level: Logging level name (INFO, DEBUG, ...).

    Returns:
        A stdlib :class:`logging.Logger` writing to stderr.
    """
    logger = logging.getLogger(name)
    if logger.handlers:  # already configured (e.g. reload in tests)
        return logger

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger
