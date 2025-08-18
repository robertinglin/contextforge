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


class NoopLogger:
    def debug(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        pass

    info = warning = error = exception = critical = debug


def _ensure_default_handler(lg: logging.Logger) -> None:
    # Let logs bubble to the root so pytest's caplog can capture them.
    # Avoid attaching our own handler to prevent duplicate output.
    lg.propagate = True


def resolve_logger(
    logger: logging.Logger | None = None,
    *,
    enabled: bool = False,
    name: str | None = None,
    level: int = logging.INFO,
) -> logging.Logger | NoopLogger:
    """
    Return a usable logger according to opt-in policy.

    - If `logger` is provided, use it.
    - Else if `enabled` is True, create/get a named logger.
    - Else return a NoopLogger that ignores calls.
    """
    # Duck-typed: if the caller gave us an object with .debug(...), use it.
    if logger is not None:
        return logger
    if enabled:
        lg = logging.getLogger(name or "contextforge")
        lg.setLevel(level)
        _ensure_default_handler(lg)
        return lg
    return NoopLogger()
