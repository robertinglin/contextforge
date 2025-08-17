import pytest
import textwrap
import logging
from contextforge.commit.patch import patch_text, fuzzy_patch_partial, PatchFailedError, _parse_simplified_patch_hunks

def test_simplified_patch_parser():
    patch_str = textwrap.dedent("""
        File: a/b/c.py
        Some text.
        @@
        - old line 1
        + new line 1
          context
        @@
        - another hunk
        + replacement
    """).strip()
    hunks = _parse_simplified_patch_hunks(patch_str)
    assert len(hunks) == 2
    assert hunks[0]["lines"] == ["- old line 1", "+ new line 1", "  context"]
    assert hunks[1]["lines"] == ["- another hunk", "+ replacement"]

def test_eol_preservation():
    crlf_content = "line1\r\nline2\r\n"
    crlf_patch = "@@\r\n-line2\r\n+line two\r\n"
    expected_crlf = "line1\r\nline two\r\n"
    assert patch_text(crlf_content, crlf_patch) == expected_crlf
    
    lf_content = "line1\nline2" # No trailing newline
    lf_patch = "@@\n-line2\n+line two\n"
    expected_lf = "line1\nline two"
    assert patch_text(lf_content, lf_patch) == expected_lf

def test_partial_patch_reporting():
    content = "alpha\nbeta\ngamma\ndelta\n"
    patch_str = textwrap.dedent("""
        @@ -1,3 +1,3 @@
         alpha
        -beta
        +BETA
         gamma
        @@ -1,1 +1,1 @@
         -nonexistent
        +NEX
    """)
    new_text, applied, failed = fuzzy_patch_partial(content, patch_str)

    assert new_text == "alpha\nBETA\ngamma\ndelta\n"
    assert applied == [0]
    assert len(failed) == 1
    failure = failed[0]
    assert failure["index"] == 1
    assert "Best match ratio" in failure["error"]
    assert failure["old_content"] == ["nonexistent"]

def test_no_hunks_raises():
    with pytest.raises(PatchFailedError, match="no valid hunks"):
        patch_text("content", "--- a/file\n+++ b/file\n")

def test_patch_text_with_logging(caplog):
    content = "hello world"
    patch = "@@\n-hello\n+goodbye\n"
    with caplog.at_level(logging.DEBUG, logger="contextforge.commit.patch"):
        patch_text(content, patch, log=True)
    assert "APPLYING HUNK" in caplog.text