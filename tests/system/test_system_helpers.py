import contextlib
import os

from contextforge.system import append_context, copy_to_clipboard, write_tempfile


def test_write_tempfile_creates_persistent_file():
    path = write_tempfile("hello world", suffix=".ctx")
    try:
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "hello world"
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def test_copy_to_clipboard_returns_bool():
    # Environment may lack a clipboard binary; just assert it returns a boolean.
    ok = copy_to_clipboard("ping")
    assert isinstance(ok, bool)


def test_append_context_basic():
    s = append_context("a", "b")
    assert s.strip().endswith("b")
