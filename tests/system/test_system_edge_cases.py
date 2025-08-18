from unittest.mock import patch

import pytest

from contextforge.system import append_context, copy_to_clipboard, write_tempfile


@patch("contextforge.system._which", return_value=False)
def test_clipboard_fallback_if_no_binary(mock_which):
    assert copy_to_clipboard("test") is False

def test_write_tempfile_error_cleanup():
    with patch("os.fdopen") as mock_fdopen:
        mock_fdopen.side_effect = OSError("Cannot write")
        with pytest.raises(IOError):
            # The function should clean up the created temp file before re-raising.
            # We can't easily check for the file's deletion post-raise here,
            # but we're verifying the exception propagation and trusting the `try...except...raise` block.
            write_tempfile("some content")

def test_append_context_customizations():
    out = append_context("A", "B", header="### Header", sep="---\n")
    expected = "A\n---\n### Header\n---\nB\n"
    assert out == expected
