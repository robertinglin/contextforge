# tests/contextforge/commit/test_patch_basics.py
import logging
import textwrap

import pytest

from contextforge.commit.patch import (
    PatchFailedError,
    _parse_simplified_patch_hunks,
    fuzzy_patch_partial,
    patch_text,
)


def test_simplified_patch_parser():
    """Test that simplified @@ syntax (no line numbers) is parsed correctly."""
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
    """Test that different line ending styles (CRLF, LF) are preserved."""
    # CRLF (Windows)
    crlf_content = "line1\r\nline2\r\n"
    crlf_patch = "@@\r\n-line2\r\n+line two\r\n"
    expected_crlf = "line1\r\nline two\r\n"
    assert patch_text(crlf_content, crlf_patch) == expected_crlf

    # LF without trailing newline
    lf_content = "line1\nline2"
    lf_patch = "@@\n-line2\n+line two\n"
    expected_lf = "line1\nline two"
    assert patch_text(lf_content, lf_patch) == expected_lf


def test_partial_patch_reporting():
    """Test that fuzzy_patch_partial reports which hunks succeeded vs failed."""
    content = "alpha\nbeta\ngamma\ndelta\n"
    patch_str = textwrap.dedent("""
        @@ -1,3 +1,3 @@
         alpha
        -beta
        +BETA
         gamma
        @@ -4,1 +4,1 @@
        -nonexistent
        +NEX
    """)
    new_text, applied, failed = fuzzy_patch_partial(content, patch_str)

    # First hunk should succeed
    assert "BETA" in new_text
    assert 0 in applied
    
    # Second hunk should fail
    assert len(failed) == 1
    failure = failed[0]
    assert failure["index"] == 1
    # New error message from phase 2 assignment failure
    assert failure["error"] == "No valid assignment"
    assert failure["old_content"] == ["nonexistent"]
    assert failure["new_content"] == ["NEX"]


def test_no_hunks_raises():
    """Test that a patch with no valid hunks raises an error."""
    with pytest.raises(PatchFailedError, match="no valid hunks"):
        patch_text("content", "--- a/file\n+++ b/file\n")


def test_patch_text_with_logging(caplog):
    """Test that debug logging works and contains expected phase markers."""
    content = "hello\nworld"
    patch = "@@\n-hello\n+goodbye\n"
    
    with caplog.at_level(logging.DEBUG, logger="contextforge.commit.patch"):
        result = patch_text(content, patch, log=True)
    
    assert result == "goodbye\nworld"
    
    # Check for new four-phase logging structure
    log_text = caplog.text
    assert "PHASE 1: FIND ALL CANDIDATES" in log_text
    assert "PHASE 2: ASSIGN HUNKS TO CANDIDATES" in log_text
    assert "PHASE 3: REFINE USING ANCHORS" in log_text
    assert "PHASE 4: APPLY CHANGES" in log_text
    assert "Applying Hunk #1" in log_text  # Not all-caps


def test_duplicate_lines_at_start():
    """
    Test that when duplicate target lines exist at the start of a file,
    the patch correctly chooses the right one based on context.
    """
    content = "line1\ntarget\ntarget\nafter\nend\n"
    patch = textwrap.dedent("""
        @@ -1,5 +1,4 @@
         line1
        -target
         target
         after
         end
    """)
    expected = "line1\ntarget\nafter\nend\n"
    result = patch_text(content, patch)
    assert result == expected


def test_duplicate_lines_with_context_scoring():
    """
    Test that when duplicate lines exist, the one with better matching
    context is chosen, not the one artificially boosted by EOF proximity.
    """
    # Two identical blocks at different locations
    content = textwrap.dedent("""
        def func1():
            check_something()
            return process()
        
        def func2():
            check_something()
            return process()
    """).strip()
    
    # Patch should apply to func2 based on context
    patch = textwrap.dedent("""
        @@ -1,3 +1,3 @@
         def func2():
             check_something()
        -    return process()
        +    return process_with_logging()
    """)
    
    result = patch_text(content, patch, log=True)
    
    # Should only change func2, not func1
    assert "def func1():\n    check_something()\n    return process()" in result
    assert "def func2():\n    check_something()\n    return process_with_logging()" in result


def test_merge_conflict_generation():
    """
    Test that when a hunk fails but is bounded by perfect matches,
    a merge conflict marker is inserted.
    """
    content = textwrap.dedent("""
        start
        alpha
        beta
        gamma
        end
    """).strip()
    
    # Three hunks: first and third succeed, middle fails
    patch = textwrap.dedent("""
        @@ -1,1 +1,1 @@
        -start
        +START
        @@ -2,1 +2,1 @@
        -nonexistent
        +REPLACED
        @@ -5,1 +5,1 @@
        -end
        +END
    """)
    
    result = patch_text(content, patch)
    
    # Should have merge conflict markers
    assert "START" in result
    assert "END" in result
    assert "<<<<<<< CURRENT" in result
    assert "=======" in result
    assert ">>>>>>> PATCH" in result


def test_empty_file_patch():
    """Test patching an empty file (pure addition)."""
    content = ""
    patch = "@@\n+new line\n+another line\n"
    expected = "new line\nanother line"
    result = patch_text(content, patch)
    assert result == expected


def test_context_only_hunk():
    """Test a hunk with only context lines (no changes)."""
    content = "line1\nline2\nline3\n"
    patch = "@@\n line1\n line2\n line3\n"
    # Should return unchanged
    result = patch_text(content, patch)
    assert result == content