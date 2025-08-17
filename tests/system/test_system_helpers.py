import os
from contextforge.system import write_tempfile, copy_to_clipboard, append_context


def test_write_tempfile_creates_persistent_file():
    path = write_tempfile("hello world", suffix=".ctx")
    try:
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "hello world"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_copy_to_clipboard_returns_bool():
    # Environment may lack a clipboard binary; just assert it returns a boolean.
    ok = copy_to_clipboard("ping")
    assert isinstance(ok, bool)


def test_append_context_basic():
    s = append_context("a", "b")
    assert s.strip().endswith("b")
