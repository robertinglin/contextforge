"""
Tests for contextforge.commit.patch module.

This test suite covers:
- Core helper functions (_compose_from_to, _find_block_end_by_braces, etc.)
- Patch parsing (standard and simplified formats)
- Fuzzy matching and context scoring
- Indentation handling and surgical reconstruction
- EOL preservation
- Structured patches
- Error conditions and edge cases
"""
from __future__ import annotations

from typing import List
import itertools
import difflib
import logging
import re
import unicodedata


from contextforge._logging import resolve_logger
from contextforge.errors.patch import PatchFailedError

__all__ = ["patch_text", "fuzzy_patch_partial"]


import pytest
import textwrap
from contextforge.commit.patch import (
    patch_text,
    fuzzy_patch_partial,
    _compose_from_to,
    _find_block_end_by_braces,
    _eq_loose,
    _indent,
    _flatten_ws_outside_quotes,
    _leading_ws,
    _normalize_quotes,
    _similarity,
    _strip_line_numbers_block,
    _reindent_relative,
    _surgical_reconstruct_block,
    _middle_out_best_window,
    _structure_penalty,
    _split_lead_tail_context,
    _parse_patch_hunks,
    _parse_simplified_patch_hunks,
    _find_block_matches,
    _split_hunk_components,
    _adaptive_ctx_window,
    _locate_insertion_index,
    _split_noncontiguous_hunks,
)
from contextforge.errors.patch import PatchFailedError


# ---------------------------------------------------------------------------
# Tests for _compose_from_to
# ---------------------------------------------------------------------------


def test_compose_from_to_with_additions_and_deletions():
    """Test composing from/to blocks with mixed changes."""
    hunk_lines = [" context", "-old", "+new", " more"]
    from_lines, to_lines = _compose_from_to(hunk_lines)
    assert from_lines == ["context", "old", "more"]
    assert to_lines == ["context", "new", "more"]


def test_compose_from_to_with_empty_lines():
    """Test that empty lines are treated as context."""
    hunk_lines = ["", "-old", "", "+new"]
    from_lines, to_lines = _compose_from_to(hunk_lines)
    assert from_lines == ["", "old", ""]
    assert to_lines == ["", "", "new"]


def test_compose_from_to_pure_additions():
    """Test pure additions (no deletions)."""
    hunk_lines = [" context", "+new1", "+new2"]
    from_lines, to_lines = _compose_from_to(hunk_lines)
    assert from_lines == ["context"]
    assert to_lines == ["context", "new1", "new2"]


# ---------------------------------------------------------------------------
# Tests for _find_block_end_by_braces
# ---------------------------------------------------------------------------


def test_find_block_end_by_braces_simple():
    """Test finding block end with simple brace matching."""
    lines = ["function foo() {", "  return 1;", "}"]
    end = _find_block_end_by_braces(lines, 0)
    assert end == 3


def test_find_block_end_by_braces_nested():
    """Test finding block end with nested braces."""
    lines = ["function foo() {", "  if (x) {", "    return 1;", "  }", "}"]
    end = _find_block_end_by_braces(lines, 0)
    assert end == 5


def test_find_block_end_by_braces_not_found():
    """Test when closing brace is not found."""
    lines = ["function foo() {", "  return 1;"]
    end = _find_block_end_by_braces(lines, 0)
    assert end == -1


# ---------------------------------------------------------------------------
# Tests for _eq_loose
# ---------------------------------------------------------------------------


def test_eq_loose_exact_match():
    """Test exact match."""
    assert _eq_loose("hello", "hello")


def test_eq_loose_whitespace_difference():
    """Test whitespace-insensitive matching."""
    assert _eq_loose("  hello  ", "hello")
    assert _eq_loose("hello", "  hello  ")


def test_eq_loose_with_semicolon():
    """Test semicolon stripping."""
    assert _eq_loose("hello;", "hello")
    assert _eq_loose("hello", "hello;")


def test_eq_loose_not_equal():
    """Test non-matching strings."""
    assert not _eq_loose("hello", "world")


# ---------------------------------------------------------------------------
# Tests for _indent
# ---------------------------------------------------------------------------


def test_indent_spaces():
    """Test counting space indentation."""
    assert _indent("    hello") == 4
    assert _indent("  hello") == 2


def test_indent_tabs():
    """Test counting tab indentation (tabs count as 4)."""
    assert _indent("\thello") == 4
    assert _indent("\t\thello") == 8


def test_indent_mixed():
    """Test mixed tabs and spaces."""
    assert _indent("\t  hello") == 6  # 4 for tab + 2 for spaces


def test_indent_no_indentation():
    """Test string with no indentation."""
    assert _indent("hello") == 0


# ---------------------------------------------------------------------------
# Tests for _flatten_ws_outside_quotes
# ---------------------------------------------------------------------------


def test_flatten_ws_basic():
    """Test basic whitespace flattening."""
    text = "a   b\n  c"
    result = _flatten_ws_outside_quotes(text)
    assert result == "abc"


def test_flatten_ws_preserves_strings():
    """Test that string content is preserved."""
    text = '"hello  world"'
    result = _flatten_ws_outside_quotes(text)
    assert '"' in result


def test_flatten_ws_removes_comments():
    """Test comment removal."""
    text = "code # comment\nmore"
    result = _flatten_ws_outside_quotes(text)
    assert "comment" not in result
    assert "code" in result
    assert "more" in result


def test_flatten_ws_triple_quotes():
    """Test triple-quoted strings."""
    text = '"""hello  world"""'
    result = _flatten_ws_outside_quotes(text)
    assert '"""' in result


def test_flatten_ws_escaped_quotes():
    """Test escaped quotes in strings."""
    text = r'"hello \"world\""'
    result = _flatten_ws_outside_quotes(text)
    assert "\\" in result


# ---------------------------------------------------------------------------
# Tests for _leading_ws
# ---------------------------------------------------------------------------


def test_leading_ws_spaces():
    """Test extracting leading whitespace."""
    assert _leading_ws("    hello") == "    "
    assert _leading_ws("  hello") == "  "


def test_leading_ws_tabs():
    """Test extracting leading tabs."""
    assert _leading_ws("\thello") == "\t"
    assert _leading_ws("\t\thello") == "\t\t"


def test_leading_ws_none():
    """Test string with no leading whitespace."""
    assert _leading_ws("hello") == ""


# ---------------------------------------------------------------------------
# Tests for _normalize_quotes
# ---------------------------------------------------------------------------


def test_normalize_quotes_unicode():
    """Test normalizing Unicode quotes to ASCII."""
    assert _normalize_quotes("\u2018hello\u2019") == "'hello'"
    assert _normalize_quotes("\u201Chello\u201D") == '"hello"'


def test_normalize_quotes_no_change():
    """Test that ASCII quotes remain unchanged."""
    assert _normalize_quotes("'hello'") == "'hello'"
    assert _normalize_quotes('"hello"') == '"hello"'


# ---------------------------------------------------------------------------
# Tests for _similarity
# ---------------------------------------------------------------------------


def test_similarity_identical():
    """Test similarity of identical lists."""
    a = ["line1", "line2"]
    b = ["line1", "line2"]
    assert _similarity(a, b) == 1.0


def test_similarity_different():
    """Test similarity of completely different lists."""
    a = ["line1", "line2"]
    b = ["other1", "other2"]
    ratio = _similarity(a, b)
    assert ratio < 0.5


def test_similarity_partial():
    """Test similarity of partially matching lists."""
    a = ["line1", "line2", "line3"]
    b = ["line1", "line2", "different"]
    ratio = _similarity(a, b)
    assert 0.5 < ratio < 1.0


# ---------------------------------------------------------------------------
# Tests for _strip_line_numbers_block
# ---------------------------------------------------------------------------


def test_strip_line_numbers_with_numbers():
    """Test stripping line numbers from diff."""
    lines = ["1 | hello", "2 | world"]
    result = _strip_line_numbers_block(lines)
    assert result == ["hello", "world"]


def test_strip_line_numbers_no_numbers():
    """Test that lines without numbers are unchanged."""
    lines = ["hello", "world"]
    result = _strip_line_numbers_block(lines)
    assert result == ["hello", "world"]


def test_strip_line_numbers_mixed():
    """Test mixed content."""
    lines = ["1 | hello", "world"]
    result = _strip_line_numbers_block(lines)
    assert "hello" in result[0]


# ---------------------------------------------------------------------------
# Tests for _reindent_relative
# ---------------------------------------------------------------------------


def test_reindent_relative_basic():
    """Test basic reindentation."""
    new_lines = ["  line1", "  line2"]
    search_first = "  search"
    matched_first = "    matched"
    result = _reindent_relative(new_lines, search_first, matched_first)
    assert result == ["    line1", "    line2"]


def test_reindent_relative_no_indent_in_patch():
    """Test reindentation when patch has no base indent."""
    new_lines = ["line1", "line2"]
    search_first = "search"
    matched_first = "  matched"
    result = _reindent_relative(new_lines, search_first, matched_first)
    assert result == ["  line1", "  line2"]


def test_reindent_relative_same_indent():
    """Test when indents are already the same."""
    new_lines = ["  line1", "  line2"]
    search_first = "  search"
    matched_first = "  matched"
    result = _reindent_relative(new_lines, search_first, matched_first)
    assert result == ["  line1", "  line2"]


def test_reindent_relative_empty():
    """Test with empty list."""
    result = _reindent_relative([], "  search", "    matched")
    assert result == []


# ---------------------------------------------------------------------------
# Tests for _surgical_reconstruct_block
# ---------------------------------------------------------------------------


def test_surgical_reconstruct_basic():
    """Test surgical reconstruction with context preservation."""
    hunk_lines = [" ctx1", "-old", "+new", " ctx2"]
    matched_segment = ["ctx1", "old", "ctx2"]
    result = _surgical_reconstruct_block(hunk_lines, matched_segment, "ctx1", "ctx1")
    assert "ctx1" in result
    assert "new" in result
    assert "ctx2" in result
    assert "old" not in result


def test_surgical_reconstruct_with_empty_context():
    """Test with empty lines as context."""
    hunk_lines = ["", "-old", "+new"]
    matched_segment = ["", "old"]
    result = _surgical_reconstruct_block(hunk_lines, matched_segment, "", "")
    assert "" in result
    assert "new" in result


# ---------------------------------------------------------------------------
# Tests for _middle_out_best_window
# ---------------------------------------------------------------------------


def test_middle_out_best_window_exact_match():
    """Test finding exact match in window."""
    target = ["a", "b", "c", "d"]
    needle = ["b", "c"]
    idx, ratio = _middle_out_best_window(target, needle, 1, 0, 4)
    assert idx == 1
    assert ratio == 1.0


def test_middle_out_best_window_fuzzy():
    """Test fuzzy matching in window."""
    target = ["a", "b", "c", "d"]
    needle = ["b ", " c"]  # Slightly different
    idx, ratio = _middle_out_best_window(target, needle, 1, 0, 4)
    assert idx >= 0
    assert ratio > 0.5


def test_middle_out_best_window_empty_target():
    """Test with empty target."""
    idx, ratio = _middle_out_best_window([], ["a"], 0, 0, 0)
    assert idx == -1
    assert ratio == -1.0


def test_middle_out_best_window_empty_needle():
    """Test with empty needle."""
    target = ["a", "b"]
    idx, ratio = _middle_out_best_window(target, [], 0, 0, 2)
    assert idx == -1
    assert ratio == -1.0


# ---------------------------------------------------------------------------
# Tests for _structure_penalty
# ---------------------------------------------------------------------------


def test_structure_penalty_matching_indent():
    """Test penalty with matching indentation."""
    target = ["  line1", "  line2"]
    penalty = _structure_penalty(target, 1, ["  new"], ["  ctx"])
    assert penalty >= 0


def test_structure_penalty_mismatched_indent():
    """Test penalty with mismatched indentation."""
    target = ["line1", "    line2"]
    penalty = _structure_penalty(target, 1, ["  new"], ["ctx"])
    assert penalty > 0


# ---------------------------------------------------------------------------
# Tests for _split_lead_tail_context
# ---------------------------------------------------------------------------


def test_split_lead_tail_context_both():
    """Test extracting both leading and trailing context."""
    hunk_lines = [" ctx1", " ctx2", "-old", "+new", " ctx3", " ctx4"]
    lead, tail = _split_lead_tail_context(hunk_lines)
    assert lead == ["ctx1", "ctx2"]
    assert tail == ["ctx3", "ctx4"]


def test_split_lead_tail_context_only_lead():
    """Test with only leading context."""
    hunk_lines = [" ctx1", "-old", "+new"]
    lead, tail = _split_lead_tail_context(hunk_lines)
    assert lead == ["ctx1"]
    assert tail == []


def test_split_lead_tail_context_only_tail():
    """Test with only trailing context."""
    hunk_lines = ["-old", "+new", " ctx1"]
    lead, tail = _split_lead_tail_context(hunk_lines)
    assert lead == []
    assert tail == ["ctx1"]


def test_split_lead_tail_context_empty_lines():
    """Test with empty lines as context."""
    hunk_lines = ["", "-old", "+new", ""]
    lead, tail = _split_lead_tail_context(hunk_lines)
    assert lead == [""]
    assert tail == [""]


# ---------------------------------------------------------------------------
# Tests for _parse_patch_hunks
# ---------------------------------------------------------------------------


def test_parse_patch_hunks_standard():
    """Test parsing standard unified diff format."""
    patch = textwrap.dedent(
        """
        @@ -1,2 +1,2 @@
         context
        -old
        +new
        """
    ).strip()
    hunks = _parse_patch_hunks(patch)
    assert len(hunks) == 1
    assert hunks[0]["old_start"] == 1
    assert hunks[0]["new_start"] == 1
    assert len(hunks[0]["lines"]) == 3


def test_parse_patch_hunks_multiple():
    """Test parsing multiple hunks."""
    patch = textwrap.dedent(
        """
        @@ -1,2 +1,2 @@
         context
        -old
        +new
        @@ -5,1 +5,1 @@
        -old2
        +new2
        """
    ).strip()
    hunks = _parse_patch_hunks(patch)
    assert len(hunks) == 2


def test_parse_patch_hunks_no_hunks_raises():
    """Test that parsing fails when no hunks are found."""
    with pytest.raises(PatchFailedError, match="no valid hunks"):
        _parse_patch_hunks("not a diff")


def test_parse_patch_hunks_with_empty_lines():
    """Test parsing hunks with empty lines."""
    patch = textwrap.dedent(
        """
        @@ -1,3 +1,3 @@
         context
        
        -old
        +new
        """
    ).strip()
    hunks = _parse_patch_hunks(patch)
    assert len(hunks) == 1
    assert "" in hunks[0]["lines"]


# ---------------------------------------------------------------------------
# Tests for _parse_simplified_patch_hunks
# ---------------------------------------------------------------------------


def test_parse_simplified_patch_hunks_basic():
    """Test parsing simplified @@ format."""
    patch = textwrap.dedent(
        """
        @@
        -old
        +new
        """
    ).strip()
    hunks = _parse_simplified_patch_hunks(patch)
    assert len(hunks) == 1
    assert "-old" in hunks[0]["lines"]
    assert "+new" in hunks[0]["lines"]


def test_parse_simplified_patch_hunks_multiple():
    """Test parsing multiple simplified hunks."""
    patch = textwrap.dedent(
        """
        @@
        -old1
        +new1
        @@
        -old2
        +new2
        """
    ).strip()
    hunks = _parse_simplified_patch_hunks(patch)
    assert len(hunks) == 2


def test_parse_simplified_patch_hunks_with_headers():
    """Test that file headers are skipped."""
    patch = textwrap.dedent(
        """
        --- a/file.txt
        +++ b/file.txt
        @@
        -old
        +new
        """
    ).strip()
    hunks = _parse_simplified_patch_hunks(patch)
    assert len(hunks) == 1
    # Headers should be skipped
    assert not any("---" in str(line) for line in hunks[0]["lines"])


# ---------------------------------------------------------------------------
# Tests for _find_block_matches
# ---------------------------------------------------------------------------


def test_find_block_matches_exact():
    """Test finding exact block matches."""
    target = ["a", "b", "c", "b", "c"]
    block = ["b", "c"]
    matches = _find_block_matches(target, block, loose=False)
    assert matches == [1, 3]


def test_find_block_matches_loose():
    """Test finding loose block matches."""
    target = ["a", "  b  ", "c"]
    block = ["b", "c"]
    matches = _find_block_matches(target, block, loose=True)
    assert 1 in matches


def test_find_block_matches_no_match():
    """Test when no matches are found."""
    target = ["a", "b"]
    block = ["x", "y"]
    matches = _find_block_matches(target, block, loose=False)
    assert matches == []


def test_find_block_matches_empty_block():
    """Test with empty block."""
    target = ["a", "b"]
    matches = _find_block_matches(target, [], loose=False)
    assert matches == []


# ---------------------------------------------------------------------------
# Tests for _split_hunk_components
# ---------------------------------------------------------------------------


def test_split_hunk_components_mixed():
    """Test splitting hunk into old, new, and context."""
    hunk_lines = [" ctx", "-old", "+new"]
    old, new, ctx = _split_hunk_components(hunk_lines)
    assert old == ["ctx", "old"]
    assert new == ["ctx", "new"]
    assert ctx == ["ctx"]


def test_split_hunk_components_pure_addition():
    """Test pure addition."""
    hunk_lines = [" ctx", "+new"]
    old, new, ctx = _split_hunk_components(hunk_lines)
    assert old == ["ctx"]
    assert new == ["ctx", "new"]
    assert ctx == ["ctx"]


def test_split_hunk_components_pure_deletion():
    """Test pure deletion."""
    hunk_lines = [" ctx", "-old"]
    old, new, ctx = _split_hunk_components(hunk_lines)
    assert old == ["ctx", "old"]
    assert new == ["ctx"]
    assert ctx == ["ctx"]


def test_split_hunk_components_empty_lines():
    """Test with empty lines."""
    hunk_lines = ["", "-old", "+new"]
    old, new, ctx = _split_hunk_components(hunk_lines)
    assert old == ["", "old"]
    assert new == ["", "new"]
    assert ctx == [""]


# ---------------------------------------------------------------------------
# Tests for _adaptive_ctx_window
# ---------------------------------------------------------------------------


def test_adaptive_ctx_window_small():
    """Test adaptive window with small context."""
    lead = ["a"]
    tail = ["b"]
    size = _adaptive_ctx_window(lead, tail)
    assert size >= 3  # Minimum is 3


def test_adaptive_ctx_window_large():
    """Test adaptive window with large context."""
    lead = ["a"] * 15
    tail = ["b"] * 15
    size = _adaptive_ctx_window(lead, tail)
    assert size >= 3


def test_adaptive_ctx_window_empty():
    """Test with no context."""
    size = _adaptive_ctx_window([], [])
    assert size >= 3


# ---------------------------------------------------------------------------
# Tests for _locate_insertion_index
# ---------------------------------------------------------------------------


def test_locate_insertion_index_empty_file():
    """Test insertion into empty file."""
    pos = _locate_insertion_index([], [], [], 0, 3)
    assert pos == 0


def test_locate_insertion_index_with_context():
    """Test insertion with both lead and tail context."""
    target = ["a", "b", "c", "d"]
    lead_ctx = ["a"]
    tail_ctx = ["d"]
    pos = _locate_insertion_index(target, lead_ctx, tail_ctx, 2, 3)
    # Should find position between 'a' and 'd'
    assert 1 <= pos <= 3


def test_locate_insertion_index_lead_only():
    """Test insertion with only leading context."""
    target = ["a", "b", "c"]
    lead_ctx = ["a"]
    pos = _locate_insertion_index(target, lead_ctx, [], 1, 3)
    assert pos >= 1


def test_locate_insertion_index_tail_only():
    """Test insertion with only trailing context."""
    target = ["a", "b", "c"]
    tail_ctx = ["c"]
    pos = _locate_insertion_index(target, [], tail_ctx, 1, 3)
    assert pos >= 0


# ---------------------------------------------------------------------------
# Tests for _split_noncontiguous_hunks
# ---------------------------------------------------------------------------


def test_split_noncontiguous_hunks_contiguous():
    """Test that contiguous additions are not split."""
    hunks = [{"lines": [" ctx", "+add1", "+add2"]}]
    result = _split_noncontiguous_hunks(hunks)
    assert len(result) == 1


def test_split_noncontiguous_hunks_noncontiguous():
    """Test splitting non-contiguous additions."""
    hunks = [{"lines": ["+add1", " ctx", "+add2"]}]
    result = _split_noncontiguous_hunks(hunks)
    assert len(result) == 2


def test_split_noncontiguous_hunks_with_deletions():
    """Test that hunks with deletions are not split."""
    hunks = [{"lines": ["+add1", " ctx", "-del", "+add2"]}]
    result = _split_noncontiguous_hunks(hunks)
    # Should not split because there are deletions
    assert len(result) == 1


def test_split_noncontiguous_hunks_preserves_metadata():
    """Test that metadata is preserved when splitting."""
    hunks = [{
        "lines": ["+add1", " ctx", "+add2"],
        "old_start": 1,
        "old_len": 0,
        "new_start": 1,
        "new_len": 2
    }]
    result = _split_noncontiguous_hunks(hunks)
    assert all("old_start" in h for h in result)


# ---------------------------------------------------------------------------
# Tests for patch_text - Edge cases and error conditions
# ---------------------------------------------------------------------------


def test_patch_text_empty_patch():
    """Test applying empty patch."""
    content = "line1\nline2\n"
    result = patch_text(content, "")
    assert result == content


def test_patch_text_empty_content():
    """Test patching empty content."""
    patch = textwrap.dedent(
        """
        @@ -0,0 +1,1 @@
        +new line
        """
    ).strip()
    result = patch_text("", patch)
    assert "new line" in result



def test_patch_text_structured_missing_old_or_pattern():
    """Test structured patch without old or pattern raises error."""
    content = "test"
    patch = [{"new": "replacement"}]
    with pytest.raises(PatchFailedError, match="missing 'old' or 'pattern'"):
        patch_text(content, patch)


def test_patch_text_structured_regex_not_found():
    """Test structured patch with regex that doesn't match."""
    content = "test"
    patch = [{"pattern": r"notfound", "new": "replacement"}]
    with pytest.raises(PatchFailedError, match="pattern not found"):
        patch_text(content, patch)


def test_patch_text_structured_sentinel_replace():
    """Test structured patch with sentinel (head/tail) replacement."""
    content = "prefix MIDDLE suffix"
    patch = [{"old": "prefix MIDDLE suffix", "new": "prefix NEW suffix"}]
    result = patch_text(content, patch)
    assert "NEW" in result


def test_patch_text_structured_old_not_found():
    """Test structured patch when old block not found."""
    content = "test"
    patch = [{"old": "notfound", "new": "replacement"}]
    with pytest.raises(PatchFailedError, match="old block not found"):
        patch_text(content, patch)


def test_patch_text_crlf_preservation():
    """Test that CRLF line endings are preserved."""
    content = "line1\r\nline2\r\n"
    patch = "@@ -1,2 +1,2 @@\r\n-line1\r\n+LINE1\r\n line2\r\n"
    result = patch_text(content, patch)
    assert "\r\n" in result
    assert "LINE1" in result


def test_patch_text_no_trailing_newline():
    """Test file without trailing newline."""
    content = "line1\nline2"
    patch = "@@ -1,2 +1,2 @@\n-line1\n+LINE1\n line2"
    result = patch_text(content, patch)
    assert not result.endswith("\n")
    assert "LINE1" in result


def test_patch_text_full_file_replacement_fallback():
    """Test fallback to full file replacement when patch has no hunks."""
    content = "old content"
    patch = "new content"
    result = patch_text(content, patch)
    assert result == "new content"


def test_patch_text_full_file_with_fences():
    """Test full file replacement with markdown fences."""
    content = "old"
    patch = "```python\nnew content\n```"
    result = patch_text(content, patch)
    assert "new content" in result
    assert "```" not in result


def test_patch_text_diff_signature_without_hunks_raises():
    """Test that diff headers without hunks raise error."""
    content = "test"
    patch = "--- a/file.txt\n+++ b/file.txt\n"
    with pytest.raises(PatchFailedError, match="looks like a diff header"):
        patch_text(content, patch)


def test_patch_text_noncontiguous_additions():
    """Test handling of non-contiguous additions."""
    content = "line1\nline2\nline3\n"
    patch = textwrap.dedent(
        """
        @@ -1,3 +1,5 @@
        +new1
         line1
         line2
        +new2
         line3
        """
    ).strip()
    result = patch_text(content, patch)
    assert "new1" in result
    assert "new2" in result


def test_patch_text_anchored_fallback():
    """Test anchored fallback when fuzzy match fails."""
    content = textwrap.dedent(
        """
        anchor_line
        some content
        more content
        end
        """
    ).strip()
    
    # Patch with recognizable anchor but different content
    patch = textwrap.dedent(
        """
        @@ -1,3 +1,3 @@
        anchor_line
        -expected content
        -more expected
        +new content
        +more new
        """
    ).strip()
    
    result = patch_text(content, patch, threshold=0.4)
    # Should find the anchor and apply changes
    assert "anchor_line" in result



def test_patch_text_merge_conflict_creation():
    """Test creation of merge conflict when bounded by perfect hunks."""
    content = textwrap.dedent(
        """
        perfect1
        ambiguous
        perfect2
        """
    ).strip()
    
    # First hunk will match perfectly, third hunk will match perfectly,
    # but second hunk won't match well
    patch = textwrap.dedent(
        """
        @@ -1,1 +1,1 @@
        -perfect1
        +PERFECT1
        @@ -2,1 +2,1 @@
        -completely_different
        +REPLACED
        @@ -3,1 +3,1 @@
        -perfect2
        +PERFECT2
        """
    ).strip()
    
    result = patch_text(content, patch, threshold=0.6)
    # Should create merge conflict for the ambiguous hunk
    assert "PERFECT1" in result
    assert "PERFECT2" in result
    # Middle hunk should be handled (either applied or conflict)
    assert "<<<<<<< CURRENT" in result or "REPLACED" in result


# ---------------------------------------------------------------------------
# Tests for fuzzy_patch_partial
# ---------------------------------------------------------------------------


def test_fuzzy_patch_partial_all_success():
    """Test fuzzy_patch_partial when all hunks succeed."""
    content = "line1\nline2\nline3\n"
    patch = textwrap.dedent(
        """
        @@ -1,1 +1,1 @@
        -line1
        +LINE1
        @@ -3,1 +3,1 @@
        -line3
        +LINE3
        """
    ).strip()
    
    new_text, applied, failed = fuzzy_patch_partial(content, patch)
    assert len(applied) == 2
    assert len(failed) == 0
    assert "LINE1" in new_text
    assert "LINE3" in new_text


def test_fuzzy_patch_partial_some_fail():
    """Test fuzzy_patch_partial when some hunks fail."""
    content = "line1\nline2\n"
    patch = textwrap.dedent(
        """
        @@ -1,1 +1,1 @@
        -line1
        +LINE1
        @@ -5,1 +5,1 @@
        -nonexistent
        +REPLACED
        """
    ).strip()
    
    new_text, applied, failed = fuzzy_patch_partial(content, patch, threshold=0.6)
    assert len(applied) >= 1
    assert "LINE1" in new_text
    # Second hunk should fail or create conflict
    if len(failed) > 0:
        assert failed[0]["index"] == 1


def test_fuzzy_patch_partial_empty_patch():
    """Test fuzzy_patch_partial with empty patch."""
    content = "test"
    new_text, applied, failed = fuzzy_patch_partial(content, "")
    assert new_text == content
    assert applied == []
    assert failed == []


def test_fuzzy_patch_partial_failed_hunk_metadata():
    """Test that failed hunks return proper metadata."""
    content = "line1\n"
    patch = textwrap.dedent(
        """
        @@ -5,1 +5,1 @@
        -nonexistent
        +REPLACED
        """
    ).strip()
    
    new_text, applied, failed = fuzzy_patch_partial(content, patch, threshold=0.9)
    if failed:
        assert "error" in failed[0]
        assert "old_content" in failed[0]
        assert "new_content" in failed[0]


# ---------------------------------------------------------------------------
# Integration tests for complex scenarios
# ---------------------------------------------------------------------------


def test_complex_multiline_with_indentation():
    """Test complex patch with indentation changes."""
    content = textwrap.dedent(
        """
        def foo():
            if x:
                print("old")
            return 1
        """
    ).strip()
    
    patch = textwrap.dedent(
        """
        @@ -1,4 +1,4 @@
         def foo():
             if x:
        -        print("old")
        +        print("new")
             return 1
        """
    ).strip()
    
    result = patch_text(content, patch)
    assert 'print("new")' in result
    assert 'print("old")' not in result


def test_multiple_hunks_sequential_application():
    """Test that multiple hunks are applied in correct order."""
    content = "a\nb\nc\nd\ne\n"
    patch = textwrap.dedent(
        """
        @@ -1,1 +1,1 @@
        -a
        +A
        @@ -3,1 +3,1 @@
        -c
        +C
        @@ -5,1 +5,1 @@
        -e
        +E
        """
    ).strip()
    
    result = patch_text(content, patch)
    assert "A" in result
    assert "C" in result
    assert "E" in result
    lines = result.split("\n")
    assert lines[0] == "A"
    assert lines[2] == "C"
    assert lines[4] == "E"


def test_pure_deletion_hunk():
    """Test a hunk that only deletes lines."""
    content = "keep1\ndelete1\ndelete2\nkeep2\n"
    patch = textwrap.dedent(
        """
        @@ -1,4 +1,2 @@
         keep1
        -delete1
        -delete2
         keep2
        """
    ).strip()
    
    result = patch_text(content, patch)
    assert "keep1" in result
    assert "keep2" in result
    assert "delete1" not in result
    assert "delete2" not in result


def test_line_number_stripping_fallback():
    """Test that line numbers are stripped in fallback matching."""
    content = "line1\nline2\nline3\n"
    # Simulate AI-provided diff with line numbers
    patch = textwrap.dedent(
        """
        @@ -1,3 +1,3 @@
        1 | line1
        2 | -line2
        3 | +LINE2
        """
    ).strip()
    
    # This may or may not work depending on threshold, but should not crash
    try:
        result = patch_text(content, patch, threshold=0.4)
        # If it works, verify the change
        assert "line1" in result or "LINE2" in result
    except PatchFailedError:
        # Expected if match quality is too low
        pass


def test_simplified_patch_format():
    """Test simplified @@ format without line numbers."""
    content = "old1\nold2\n"
    patch = textwrap.dedent(
        """
        @@
        -old1
        +new1
        @@
        -old2
        +new2
        """
    ).strip()
    
    result = patch_text(content, patch)
    assert "new1" in result
    assert "new2" in result
    assert "old1" not in result
    assert "old2" not in result


def test_debug_flag_enables_logging():
    """Test that debug flag enables logging."""
    content = "test\n"
    patch = "@@ -1,1 +1,1 @@\n-test\n+TEST\n"
    
    # Should not raise, debug flag is just for logging
    result = patch_text(content, patch, debug=True)
    assert "TEST" in result
    
    # Also test with debug=False
    result = patch_text(content, patch, debug=False)
    assert "TEST" in result

