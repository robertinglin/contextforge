"""
Lightweight, opt-in logging utilities for the library.

Usage in library code:
    from contextforge._logging import resolve_logger

    def do_thing(..., logger=None, log: bool = False):
        log = resolve_logger(logger=logger, enabled=log, name=__name__)
        log.debug("starting do_thing")  # no-op unless enabled or logger passed
        ...

Design goals:
- No stdout/stderr prints in library code.
- Zero-noise by default; consumers opt in by passing a logger or enabled flag.
- Safe to import without configuring global logging.
"""
from __future__ import annotations

import logging
from typing import Optional


class NoopLogger:
    def debug(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        pass

    info = warning = error = exception = critical = debug


def _ensure_default_handler(lg: logging.Logger) -> None:
    """Attach a basic stream handler only if the logger has no handlers."""
    if lg.handlers:
        return
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    lg.addHandler(handler)
    # Avoid duplicate messages if root handlers exist
    lg.propagate = False


def resolve_logger(
    logger: Optional[logging.Logger] = None,
    *,
    enabled: bool = False,
    name: Optional[str] = None,
    level: int = logging.INFO,
) -> logging.Logger | NoopLogger:
    """
    Return a usable logger according to opt-in policy.

    - If `logger` is provided, use it.
    - Else if `enabled` is True, create/get a named logger.
    - Else return a NoopLogger that ignores calls.
    """
    if isinstance(logger, logging.Logger):
        return logger
    if enabled:
        lg = logging.getLogger(name or "contextforge")
        _ensure_default_handler(lg)
        lg.setLevel(level)
        return lg
    return NoopLogger()
