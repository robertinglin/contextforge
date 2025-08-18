import logging

from contextforge._logging import NoopLogger, resolve_logger


def test_resolve_logger_default_noop():
    lg = resolve_logger()
    assert isinstance(lg, NoopLogger)
    # Should not raise:
    lg.debug("hello")
    lg.info("world")


def test_resolve_logger_enabled_creates_logger(caplog):
    with caplog.at_level(logging.INFO):
        lg = resolve_logger(enabled=True, name="contextforge.test")
        lg.info("test message")
    assert any("test message" in rec.message for rec in caplog.records)


def test_resolve_logger_uses_passed_logger(caplog):
    logging.getLogger("x").handlers.clear()
    with caplog.at_level(logging.DEBUG, logger="x"):
        custom = logging.getLogger("x")
        # Ensure it has a handler so messages show up in caplog
        if not custom.handlers:
            handler = logging.StreamHandler()
            custom.addHandler(handler)
            custom.propagate = False
        lg = resolve_logger(logger=custom)
        lg.debug("from custom")
    assert any("from custom" in rec.message for rec in caplog.records)
