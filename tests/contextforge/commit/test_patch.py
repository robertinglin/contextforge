import logging
import pytest
import textwrap
from unittest.mock import MagicMock

from contextforge.commit.patch import (
    _flatten_ws_outside_quotes,
    _reindent_relative,
    _strip_line_numbers_block,
    _middle_out_best_window,
    _structure_penalty,
    _find_block_end_by_braces,
    _compose_from_to,
    _find_block_end_by_braces,
    _indent,
    _parse_patch_hunks,
    _parse_simplified_patch_hunks,
    _find_block_matches,
    _split_hunk_components,
    _adaptive_ctx_window,
    _locate_insertion_index,
    _apply_hunk_block_style,
    patch_text,
    fuzzy_patch_partial,
    _locate_insertion_index,
)
from contextforge.errors.patch import PatchFailedError

# Helper to create a mock logger
@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)

# Tests for helper functions

def test_compose_from_to_with_empty_lines():
    hunk_lines = ["- old", "", "+ new"]
    from_lines, to_lines = _compose_from_to(hunk_lines)
    assert from_lines == [" old", ""]
    assert to_lines == ["", " new"]


def test_parse_patch_hunks_no_hunks():
    patch_str = "--- a/file.txt\n+++ b/file.txt"
    with pytest.raises(PatchFailedError, match="contains no valid hunks"):
        _parse_patch_hunks(patch_str)

def test_parse_simplified_patch_hunks_finds_diff_lines_before_at_at():
    patch_str = "--- a/file.txt\n+++ b/file.txt\n- removed line\n+ added line\n@@\n- other"
    hunks = _parse_simplified_patch_hunks(patch_str)
    assert len(hunks) == 2
    assert hunks[0]["lines"] == ["- removed line", "+ added line"]
    assert hunks[1]["lines"] == ["- other"]

def test_find_block_matches_empty_block():
    assert _find_block_matches(["a", "b"], []) == []

def test_split_hunk_components_with_empty_and_unknown_lines():
    hunk_lines = ["- old", "", "+ new", "! unknown"]
    old, new, context = _split_hunk_components(hunk_lines)
    assert old == ["old", ""]
    assert new == ["", "new"]
    assert context == [""]

def test_adaptive_ctx_window_large_context():
    lead_ctx = [""] * 15
    tail_ctx = [""] * 15
    assert _adaptive_ctx_window(lead_ctx, tail_ctx) == 10

# --- Tests for _apply_hunk_block_style ---

def test_apply_hunk_js_brace_fallback(mock_logger):
    # Success case
    target_ok = [
        "function updateParentCheckboxState(checkbox) {",
        "  // old implementation",
        "}",
    ]
    hunk = {
        "lines": [
            " function updateParentCheckboxState(checkbox) {",
            "-   // old implementation",
            "+   // new implementation",
            " }",
        ]
    }
    new_lines, _ = _apply_hunk_block_style(target_ok, hunk, 0.6, 0, mock_logger)
    assert new_lines == [
        "function updateParentCheckboxState(checkbox) {",
        "  // new implementation",
        "}",
    ]
    
    # Failure case (unbalanced braces)
    target_fail = [
        "function updateParentCheckboxState(checkbox) {",
        "  // implementation",
    ]
    hunk_fail = {
        "lines": [
            " function updateParentCheckboxState(checkbox) {",
            "-   // some other implementation",
            "+   // new implementation",
            " }",
        ]
    }
    # It will fail brace matching and fall through, eventually raising PatchFailedError
    with pytest.raises(PatchFailedError):
        _apply_hunk_block_style(target_fail, hunk_fail, 0.9, 0, mock_logger)

def test_apply_hunk_exact_match_scoring(mock_logger):
    target = ["a", "b", "c", "d", "e", "f", "a", "b", "c"]
    hunk = {"lines": [" lead", "- a", "- b", "- c", "+ x", " tail"]}
    # hint is near the end, so the second match should be chosen
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.6, 8, mock_logger)
    assert new_lines == ["a", "b", "c", "d", "e", "f", "x"]

def test_apply_hunk_loose_match_scoring(mock_logger):
    target = [" a", "b", " c", " d", "a", "b", "c "]
    hunk = {"lines": ["- a", "- b", "- c", "+ x"]}
    # Second match is better due to hint
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.6, 5, mock_logger)
    assert new_lines == [" a", "b", " c", " d", "x"]

def test_apply_hunk_empty_target(mock_logger):
    target = []
    hunk = {"lines": ["- a", "+ b"]}
    new_lines, cursor = _apply_hunk_block_style(target, hunk, 0.6, 0, mock_logger)
    assert new_lines == ["b"]
    assert cursor == 1

def test_apply_hunk_perfect_fuzzy_match_break(mock_logger):
    target = ["noise", "a", "b", "noise", "a", "b"]
    hunk = {"lines": ["- a", "- b", "+ c"]}
    # This should find the match at index 1 and stop searching due to perfect match
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.6, 0, mock_logger)
    assert new_lines == ["noise", "c", "noise", "a", "b"]

def test_apply_hunk_log_many_old_lines(mock_logger):
    target = ["a"] * 10
    old_content = [f"line {i}" for i in range(10)]
    hunk = {"lines": [f"- {line}" for line in old_content] + ["+ new content"]}
    
    with pytest.raises(PatchFailedError):
        _apply_hunk_block_style(target, hunk, threshold=0.9, start_hint=0, log=mock_logger)
    
    mock_logger.debug.assert_any_call("    ... and 5 more lines")

def test_apply_hunk_surgical_fallback(mock_logger):
    target = [
        "first line;",
        "  second_line(arg1, arg2);",
        "third line;",
    ]
    hunk = {
        "lines": [
            "- first line;",
            "- second_line(arg1,  arg2);", # Note extra space
            "+ replacement line;",
        ]
    }
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.9, 0, mock_logger) # high threshold to force fallback
    assert new_lines == ["replacement line;", "third line;"]
    mock_logger.debug.assert_any_call("  ✅ Fallback success: Surgically matched 2 file lines from anchor 0.")

def test_apply_hunk_unique_anchor_fallback(mock_logger):
    target = [
        "start",
        "middle 1",
        "end",
        "start",
        "middle 2",
        "end_is_unique",
    ]
    hunk = {
        "lines": [
            "- start",
            "- middle 2",
            "- end_is_unique",
            "+ replacement",
        ]
    }
    # high threshold to force fallback, and content differs enough that surgical fails
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.9, 4, mock_logger)
    assert new_lines == [
        "start",
        "middle 1",
        "end",
        "replacement",
    ]

def test_apply_hunk_fails_and_creates_conflict(mock_logger):
    target = ["start", "middle", "end"]
    hunk = {"lines": ["- start", "- something else", "+ replacement"]}
    new_lines, _ = _apply_hunk_block_style(target, hunk, 0.9, 0, mock_logger)
    assert new_lines == [
        "<<<<<<< CURRENT CHANGE",
        "start",
        "middle",
        "=======",
        "replacement",
        ">>>>>>> INCOMING CHANGE (from patch)",
        "end",
    ]

def test_apply_hunk_fails_conflict_threshold(mock_logger):
    target = ["a", "b", "c"]
    hunk = {"lines": ["- x", "- y", "+ z"]}
    with pytest.raises(PatchFailedError, match="below conflict threshold"):
        _apply_hunk_block_style(target, hunk, 0.9, 0, mock_logger)

def test_apply_hunk_fail_below_threshold(mock_logger):
    target = ["a", "b", "c"]
    hunk = {"lines": ["- x", "- y", "+ z"]}
    with pytest.raises(PatchFailedError, match="is below threshold"):
        # We need to make it fail the conflict generation too. Let's make the anchor not found.
        hunk = {"lines": ["- completely different", "+ z"]}
        _apply_hunk_block_style(target, hunk, 0.6, 0, mock_logger)

# --- Tests for patch_text ---

def test_patch_text_structured_regex():
    content = "hello world"
    patch = [{"pattern": r"world", "new": "pytest"}]
    result = patch_text(content, patch, log=True)
    assert result == "hello pytest"

def test_patch_text_structured_regex_not_found():
    content = "hello world"
    patch = [{"pattern": r"galaxy", "new": "pytest"}]
    with pytest.raises(PatchFailedError, match="pattern not found"):
        patch_text(content, patch)

def test_patch_text_structured_old_not_found():
    content = "hello world"
    patch = [{"old": "hello galaxy", "new": "bye pytest"}]
    with pytest.raises(PatchFailedError, match="old block not found"):
        patch_text(content, patch)

def test_patch_text_empty_patch_string():
    content = "some content"
    assert patch_text(content, "  \n  ") == content

def test_patch_text_eol_detection():
    content_cr = "line1\rline2"
    patch_cr = "@@ -1,1 +1,1 @@\n-line1\r\n+line one"
    result_cr = patch_text(content_cr, patch_cr)
    assert result_cr == "line one\rline2"

def test_patch_text_no_valid_hunks():
    content = "content"
    patch = "--- a/file\n+++ b/file"
    with pytest.raises(PatchFailedError, match="no valid hunks"):
        patch_text(content, patch)

# --- Tests for fuzzy_patch_partial ---

def test_fuzzy_patch_partial_empty_patch():
    content = "content"
    result, applied, failed = fuzzy_patch_partial(content, "")
    assert result == content
    assert applied == []
    assert failed == []

def test_fuzzy_patch_partial_eol():
    patch = "@@ -1,1 +1,1 @@\n-line1\n+line one"
    
    content_crlf = "line1\r\nline2"
    result_crlf, _, _ = fuzzy_patch_partial(content_crlf, patch)
    assert result_crlf.startswith("line one\r\n")

    content_cr = "line1\rline2"
    result_cr, _, _ = fuzzy_patch_partial(content_cr, patch)
    assert result_cr.startswith("line one\r")


# ---------- Additional coverage tests ----------

def test_find_block_end_by_braces_balanced():
    lines = ["function f() {", "  doWork();", "}", "next();"]
    assert _find_block_end_by_braces(lines, 0) == 3  # exclusive end index

def test_find_block_end_by_braces_unbalanced_returns_minus_one():
    lines = ["function f() {", "  // missing closing brace"]
    assert _find_block_end_by_braces(lines, 0) == -1

def test_indent_counts_tabs_as_four():
    assert _indent("\t  x") == 6  # 4 (tab) + 2 (spaces)
    assert _indent("    x") == 4
    assert _indent("x") == 0

def test_flatten_ws_outside_quotes_comments_and_strings():
    text = (
        "a // comment trimmed\n"
        "b # hash comment\n"
        "t = 'a b\\n c'\n"
        'u = "x\\t y"\n'
        "v = '''A B\\nC'''"
    )
    out = _flatten_ws_outside_quotes(text)
    # outside comments/whitespace removed; escapes preserved inside strings; quotes kept
    assert out == "abt='ab\\nc'u=\"x\\ty\"v='''AB\\nC'''"

def test_strip_line_numbers_block_changes_only_when_present():
    lines_with = [" 12 | foo", "  7| bar", "baz"]
    stripped = _strip_line_numbers_block(lines_with)
    assert stripped == ["foo", "bar", "baz"]

    lines_no = ["alpha", "beta"]
    same_ref = _strip_line_numbers_block(lines_no)
    # When nothing changes, function returns original list object to avoid loops
    assert same_ref is lines_no

def test_reindent_relative_empty_noop():
    # When new_lines is empty, function should return empty without modification
    assert _reindent_relative([], "    x", "\tx") == []

def test_structure_penalty_uses_new_content_and_caps():
    target = ["prev", "  line"]  # pos will read indentation from target[pos-1]
    pos = 1
    # want=20 spaces, have=0 -> capped at 8
    new_content = [" " * 20 + "x"]
    assert _structure_penalty(target, pos, new_content, lead_ctx=[]) == 8

def test_structure_penalty_uses_lead_context_when_new_empty():
    target = ["noindent", "line"]
    pos = 1
    # new_content empty -> fall back to lead_ctx indentation (4)
    assert _structure_penalty(target, pos, [], lead_ctx=["    anchor"]) == 4

def test_middle_out_best_window_invalid_range_returns_negative():
    # hi==lo -> effective window length 0 -> (-1, -1.0)
    idx, ratio = _middle_out_best_window(["a", "b", "c"], ["x"], start_hint=0, lo=3, hi=3)
    assert (idx, ratio) == (-1, -1.0)

def test_locate_insertion_index_with_both_anchors_prefers_hint_inside_range():
    target = ["A", "B", "C", "D", "E", "F"]
    lead_ctx = ["B"]  # matches at index 1
    tail_ctx = ["E"]  # matches at index 4
    # L_end=2, T=4; start_hint inside -> pick start_hint (3)
    pos = _locate_insertion_index(target, lead_ctx, tail_ctx, start_hint=3, ctx_probe=1)
    assert pos == 3

def test_locate_insertion_index_lead_only_and_tail_only_and_empty_target():
    # lead only
    target = ["a", "lead", "x"]
    lead_ctx = ["lead"]
    tail_ctx = []
    pos = _locate_insertion_index(target, lead_ctx, tail_ctx, start_hint=10, ctx_probe=1)
    # insert right after lead slice
    assert pos == 2

    # tail only
    target = ["tail", "b", "c"]
    lead_ctx = []
    tail_ctx = ["tail"]
    pos = _locate_insertion_index(target, lead_ctx, tail_ctx, start_hint=0, ctx_probe=1)
    assert pos == 0  # before the tail slice

    # empty target
    assert _locate_insertion_index([], ["x"], ["y"], start_hint=5, ctx_probe=3) == 0

def test_apply_hunk_fuzzy_perfect_match_break_replaces_window(mock_logger):
    # target shorter than old_content -> step0/exact/loose all fail, fuzzy gets ratio==1.0 and breaks
    target = ["A", "B"]
    hunk = {"lines": ["- A", "- B", "- C", "+ AX", "+ BX", "+ CX"]}
    new_lines, cursor = _apply_hunk_block_style(target, hunk, threshold=0.6, start_hint=0, log=mock_logger)
    assert new_lines == ["AX", "BX", "CX"]
    assert cursor == 3

def test_apply_hunk_number_stripping_fallback(mock_logger):
    # Old block has "NN | " prefixes; file doesn't. Should match after stripping, then replace.
    target = ["line a", "line b", "line c"]
    hunk = {"lines": ["- 10 | line a", "- 11 | line b", "+ LINE A", "+ LINE B"]}
    new_lines, _ = _apply_hunk_block_style(target, hunk, threshold=0.95, start_hint=0, log=mock_logger)
    assert new_lines == ["LINE A", "LINE B", "line c"]

def test_patch_text_wraps_failed_hunk_error_message():
    content = "a\nb\n"
    bad_patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
        - x
        + y
        """
    )
    with pytest.raises(PatchFailedError, match=r"^Failed to apply hunk #1:"):
        patch_text(content, bad_patch)

def test_fuzzy_patch_partial_collects_failed_metadata():
    content = "a\nb\n"
    bad_patch = textwrap.dedent(
        """\
        @@ -1,2 +1,2 @@
        - x
        + y
        """
    )
    new_text, applied, failed = fuzzy_patch_partial(content, bad_patch)
    assert new_text == content
    assert applied == []
    assert len(failed) == 1
    entry = failed[0]
    assert entry["index"] == 0
    # basic keys present
    assert {"error", "lead_ctx", "tail_ctx", "old_content", "new_content", "header_hint"} <= set(entry.keys())

def test_structured_patch_missing_old_or_pattern_raises():
    with pytest.raises(PatchFailedError, match="missing 'old' or 'pattern'"):
        patch_text("content", [{}])

def test_locate_insertion_index_both_anchors_multiple_hits_choose_nearest_hint():
    # Multiple occurrences of anchors; ensure selection uses nearest within [L_end, T]
    target = ["s", "L", "x", "L", "y", "T", "end"]
    lead_ctx = ["L"]
    tail_ctx = ["T"]
    # lead hits at 1 and 3; tail hit at 5; for ctx_probe=1, L_end in {2,4}. Range choices: [2,5] or [4,5]
    # start_hint=4 falls inside both; should pick start_hint (4)
    pos = _locate_insertion_index(target, lead_ctx, tail_ctx, start_hint=4, ctx_probe=1)
    assert pos == 4
    
def test_duplicate_at_beginning():
    initial = "line1\ntarget\ntarget\nafter\nend\n"
    patch = textwrap.dedent("""
    @@ -1,5 +1,4 @@
     line1
    -target
     target
     after
     end
    """)
    expected = "line1\ntarget\nafter\nend\n"
    out = patch_text(initial, patch)
    assert out == expected


def test_duplicate_near_end():
    initial = "begin\nalpha\nbeta\nbeta\nomega\nend\n"
    patch = textwrap.dedent("""
    @@ -1,6 +1,5 @@
     begin
     alpha
    -beta
     beta
     omega
     end
    """)
    expected = "begin\nalpha\nbeta\nomega\nend\n"
    out = patch_text(initial, patch)
    assert out == expected


def test_pure_addition_in_middle():
    initial = "a\nb\nd\ne\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +c
    """)
    expected = "a\nb\nc\nd\ne\n"
    out = patch_text(initial, patch)
    assert out == expected


def test_pure_addition_append():
    initial = "x\ny\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +z
    """)
    expected = "x\ny\nz\n"
    out = patch_text(initial, patch)
    assert out == expected


def test_guarded_delete_non_existent():
    initial = "p\nq\nr\n"
    patch = textwrap.dedent("""
    @@ -1,3 +1,4 @@
     p
     q
    -miss
     r
    +s
    """)
    expected = "p\nq\nr\ns\n"
    out = patch_text(initial, patch)
    assert out == expected


def test_popup_js_like_qr_removal():
    before = (
        "const views = {\n"
        "  unpaired: U,\n"
        "  paired: P,\n"
        "  qrScanner: Q,\n"
        "  contextBuilder: C,\n"
        "};\n"
        "let qrVideoStream = null;\n"
        "let qrAnimationId = null;\n"
        "unpairButton.style.display = (viewName === 'paired') ? 'block' : 'none';\n"
        "mainFooter.style.display = (viewName === 'qrScanner') ? 'none' : 'flex';\n"
        "scanQrButton.addEventListener('click', startQrScanner);\n"
    )
    patch = textwrap.dedent("""
    @@ -1,11 +1,9 @@
     const views = {
       unpaired: U,
       paired: P,
    -  qrScanner: Q,
       contextBuilder: C,
     };
    -let qrVideoStream = null;
    -let qrAnimationId = null;
     unpairButton.style.display = (viewName === 'paired') ? 'block' : 'none';
    -mainFooter.style.display = (viewName === 'qrScanner') ? 'none' : 'flex';
    +mainFooter.style.display = 'flex'; // Always visible now
    -scanQrButton.addEventListener('click', startQrScanner);
    """)
    after = patch_text(before, patch)
    assert "qrScanner:" not in after
    assert "qrVideoStream" not in after
    assert "qrAnimationId" not in after
    assert "viewName === 'qrScanner'" not in after
    assert "scanQrButton.addEventListener" not in after


def test_block_first_contiguous_replacement():
    initial = "one\nalpha\nbeta\ngamma\nend\n"
    patch = textwrap.dedent("""
    @@ -1,5 +1,5 @@
     one
     alpha
    -beta
    +BETA
     gamma
     end
    """)
    expected = "one\nalpha\nBETA\ngamma\nend\n"
    out = patch_text(initial, patch)
    assert out == expected

# --- Tests for fuzzy_patch_partial ---

def test_fuzzy_patch_partial_empty_patch():
    content = "content"
    result, applied, failed = fuzzy_patch_partial(content, "")
    assert result == content
    assert applied == []
    assert failed == []

def test_fuzzy_patch_partial_eol():
    patch = "@@ -1,1 +1,1 @@\n-line1\n+line one"
    
    content_crlf = "line1\r\nline2"
    result_crlf, _, _ = fuzzy_patch_partial(content_crlf, patch)
    assert result_crlf.startswith("line one\r\n")

    content_cr = "line1\rline2"
    result_cr, _, _ = fuzzy_patch_partial(content_cr, patch)
    assert result_cr.startswith("line one\r")