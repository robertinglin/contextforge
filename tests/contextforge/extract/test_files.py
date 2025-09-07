import pytest
from contextforge.extract.files import (
    _line_bounds,
    _extract_path_hint_from_lines,
    _file_score,
    _body_slice_for_open_file,
    _best_close_for_open_file,
    _find_matching_open_for_close,
    _extract_file_spans_bottom_up,
    _span_slice_including_fences,
    _tokenize_fences,
    extract_file_blocks_from_text,
)
from contextforge.models.fence import FenceToken

# Test _line_bounds
@pytest.mark.parametrize(
    "text, idx, expected",
    [
        ("line1\nline2\nline3", -5, (0, 5)),
        ("line1\nline2\nline3", 100, (12, 17)),
    ]
)
def test_line_bounds_edge_cases(text, idx, expected):
    """Tests _line_bounds with out-of-bounds indices."""
    assert _line_bounds(text, idx) == expected

# Test _extract_path_hint_from_lines
def test_extract_path_hint_from_lines_unlabelled():
    """Tests extraction of a path that is not explicitly labelled."""
    lines = ["Here is a file path:", "src/my_app/main.py"]
    assert _extract_path_hint_from_lines(lines) == "src/my_app/main.py"

# Test _file_score
@pytest.mark.parametrize(
    "body, language, min_expected_score",
    [
        ("", "python", 0.0),
        ("   \n \t ", "python", 0.0),
        ("one line", None, 0.25),
        ("# Title\n- item", "md", 5.0),
        ("[link](http://a.com)\n```\ncode\n```", "markdown", 5.5),
        ("#!/bin/bash\n echo 'hi'", "bash", 1.0),
        ('{\n  "key": "value"\n}', "json", 2.0),
        ("def main():\n  pass", "python", 2.0),
    ]
)
def test_file_score(body, language, min_expected_score):
    """Tests the heuristic scoring for file block content."""
    score = _file_score(body, language)
    if min_expected_score == 0.0:
        assert score == 0.0
    else:
        assert score >= min_expected_score
    assert isinstance(score, float)

# Test _body_slice_for_open_file
def test_body_slice_for_open_file_edge_cases():
    """Tests edge cases for calculating the body slice of a fenced block."""
    text_default = "```\ncontent\n```"
    tokens_default = _tokenize_fences(text_default)
    start, end = _body_slice_for_open_file(text_default, tokens_default[0], tokens_default[1])
    assert text_default[start:end] == "content\n"

    text_space = "```python # comment\nprint('hello')\n```"
    tokens_space = _tokenize_fences(text_space)
    start, end = _body_slice_for_open_file(text_space, tokens_space[0], tokens_space[1])
    assert text_space[start:end] == "# comment\nprint('hello')\n"

    text_no_lang = "``` \ncontent\n```"
    tokens_no_lang = _tokenize_fences(text_no_lang)
    start, end = _body_slice_for_open_file(text_no_lang, tokens_no_lang[0], tokens_no_lang[1])
    assert start == tokens_no_lang[0].end # Should default to right after the fence
    assert text_no_lang[start:end] == " \ncontent\n"

# Test _best_close_for_open_file
@pytest.mark.parametrize(
    "text, open_idx, expected_close_idx",
    [
        ("```python\ncode\n```", 0, 1),
        ("```python\ncode\n~~~", 0, None),
        ("````python\ncode\n```", 0, None),
        ("```python\ncode\n``` info", 0, None),
        ("```python ```\n", 0, None),
        ("````py\n" + "import os\n" * 10 + "````\n````", 0, 1),
    ]
)
def test_best_close_for_open_file(text, open_idx, expected_close_idx):
    """Tests the outside-in pairing logic for finding the best closing fence."""
    tokens = _tokenize_fences(text)
    result = _best_close_for_open_file(text, tokens, open_idx)
    assert result == expected_close_idx


# Test _find_matching_open_for_close
@pytest.mark.parametrize(
    "text, close_idx, expected_open_idx",
    [
        ("```python\n``` info", 1, None),
        ("~~~python\n```", 1, None),
        ("no opener\n```", 0, None),
        ("```A\n  ```B\n  ```\n```", 3, 0),
    ]
)
def test_find_matching_open_for_close(text, close_idx, expected_open_idx):
    """Tests the depth-counting logic for finding a matching opening fence."""
    tokens = _tokenize_fences(text)
    result = _find_matching_open_for_close(tokens, close_idx)
    assert result == expected_open_idx


# Test extract_file_blocks_from_text for bare fence filtering
@pytest.mark.parametrize(
    "content, should_be_extracted",
    [
        ("```\n# A Title\n- A list item\n```", True),
        ("```\nimport os\ndef main():\n  pass\n```", True),
        ("```\nJust some plain text that doesn't look like code or markdown.```", False),
    ]
)
def test_extract_file_blocks_from_text_bare_fence_filtering(content, should_be_extracted):
    """Tests filtering of bare fences based on content analysis."""
    blocks = extract_file_blocks_from_text(content)
    assert len(blocks) == (1 if should_be_extracted else 0)


# Test _extract_file_spans_bottom_up for uncovered branches
@pytest.mark.parametrize(
    "text, expected_span_count",
    [
        ("no opener here\n```", 0),
        ("```python ```", 0),
        ("```A\n  ```B\n  ```C\n  ```\n```A", 1),
    ]
)
def test_extract_file_spans_bottom_up_uncovered(text, expected_span_count):
    """Tests uncovered branches in the bottom-up span extraction logic."""
    spans = _extract_file_spans_bottom_up(text)
    assert len(spans) == expected_span_count


# Test _span_slice_including_fences
def test_span_slice_including_fences_no_trailing_newline():
    """Tests the fallback for finding span end when fence is at EOF."""
    text = "```python\ncode\n```"
    tokens = _tokenize_fences(text)
    open_tok, close_tok = tokens[0], tokens[1]

    # To ensure the fallback is hit, we manually unset line_end, as the tokenizer
    # would normally set it to len(text).
    mock_close_tok = FenceToken(**{**close_tok.__dict__, "line_end": None})

    start, end = _span_slice_including_fences(text, open_tok, mock_close_tok)
    assert start == 0
    assert end == len(text)
# Test _line_bounds
@pytest.mark.parametrize(
    "text, idx, expected",
    [
        ("line1\nline2\nline3", -5, (0, 5)), # ❌ line 17: idx < 0
        ("line1\nline2\nline3", 100, (12, 17)), # ❌ line 19: idx > len(text)
    ]
)
def test_line_bounds_edge_cases(text, idx, expected):
    """Tests _line_bounds with out-of-bounds indices."""
    assert _line_bounds(text, idx) == expected

# Test _extract_path_hint_from_lines
def test_extract_path_hint_from_lines_unlabelled():
    """Tests extraction of a path that is not explicitly labelled."""
    # ❌ line 102: test case for unlabelled path regex
    lines = ["Here is a file path:", "src/my_app/main.py"]
    assert _extract_path_hint_from_lines(lines) == "src/my_app/main.py"

# Test _file_score
@pytest.mark.parametrize(
    "body, language, min_expected_score",
    [
        ("", "python", 0.0),                              # ❌ line 116, 117: empty body
        ("   \n \t ", "python", 0.0),                      # ❌ line 116, 117: whitespace body
        ("one line", None, 0.25),                          # ❌ line 119, 120, 122: None language, basic score
        ("# Title\n- item", "md", 5.0),                    # ❌ line 125-127: markdown scoring
        ("[link](http://a.com)\n```\ncode\n```", "markdown", 5.5), # ❌ line 128-129: markdown link/fence scoring
        ("#!/bin/bash\n echo 'hi'", "bash", 1.0),         # ❌ line 131: shebang scoring
        ('{\n  "key": "value"\n}', "json", 2.0),           # ❌ line 132, 135: brace and json-ish scoring
        ("def main():\n  pass", "python", 2.0),            # ❌ line 133: keyword scoring
    ]
)
def test_file_score(body, language, min_expected_score):
    """Tests the heuristic scoring for file block content."""
    score = _file_score(body, language)
    if min_expected_score == 0.0:
        assert score == 0.0
    else:
        assert score >= min_expected_score
    # ❌ line 138: return score
    assert isinstance(score, float)

# Test _body_slice_for_open_file
def test_body_slice_for_open_file_edge_cases():
    """Tests edge cases for calculating the body slice of a fenced block."""
    # ❌ line 153: test default case where body starts on next line
    text_default = "```\ncontent\n```"
    tokens_default = _tokenize_fences(text_default)
    start, end = _body_slice_for_open_file(text_default, tokens_default[0], tokens_default[1])
    assert text_default[start:end] == "content\n"

    # ❌ line 162: language token followed by a space
    text_space = "```python # comment\nprint('hello')\n```"
    tokens_space = _tokenize_fences(text_space)
    start, end = _body_slice_for_open_file(text_space, tokens_space[0], tokens_space[1])
    assert text_space[start:end] == "# comment\nprint('hello')\n"

    # ❌ line 165: same-line content with no language token (e.g., just whitespace)
    text_no_lang = "``` \ncontent\n```"
    tokens_no_lang = _tokenize_fences(text_no_lang)
    start, end = _body_slice_for_open_file(text_no_lang, tokens_no_lang[0], tokens_no_lang[1])
    assert start == tokens_no_lang[0].end # Should default to right after the fence
    assert text_no_lang[start:end] == " \ncontent\n"

# Test _best_close_for_open_file
@pytest.mark.parametrize(
    "text, open_idx, expected_close_idx",
    [
        # ❌ lines 180-206: Whole function test - successful simple match
        ("```python\ncode\n```", 0, 1),
        # ❌ line 188: Mismatched char
        ("```python\ncode\n~~~", 0, None),
        # ❌ line 188: Closer too short
        ("````python\ncode\n```", 0, None),
        # ❌ line 190: Closer has info text, making it invalid
        ("```python\ncode\n``` info", 0, None),
        # ❌ line 194: Body slice would be invalid (end < start)
        ("```python ```\n", 0, None),
        # ❌ line 203, 204: High score on first valid closer from end causes early break
        ("````py\n" + "import os\n" * 10 + "````\n````", 0, 1),
    ]
)
def test_best_close_for_open_file(text, open_idx, expected_close_idx):
    """Tests the outside-in pairing logic for finding the best closing fence."""
    tokens = _tokenize_fences(text)
    result = _best_close_for_open_file(text, tokens, open_idx)
    assert result == expected_close_idx


# Test _find_matching_open_for_close
@pytest.mark.parametrize(
    "text, close_idx, expected_open_idx",
    [
        # ❌ line 219: Closer has info, so it's not a valid closer.
        ("```python\n``` info", 1, None),
        # ❌ line 225: Token has mismatched char, should be skipped.
        ("~~~python\n```", 1, None),
        # ❌ line 233: Scanned all tokens, no matching opener found.
        ("no opener\n```", 0, None),
        # Nested case where inner opener is skipped due to depth counting
        ("```A\n  ```B\n  ```\n```", 3, 0),
    ]
)
def test_find_matching_open_for_close(text, close_idx, expected_open_idx):
    """Tests the depth-counting logic for finding a matching opening fence."""
    tokens = _tokenize_fences(text)
    result = _find_matching_open_for_close(tokens, close_idx)
    assert result == expected_open_idx


# Test extract_file_blocks_from_text for bare fence filtering
@pytest.mark.parametrize(
    "content, should_be_extracted",
    [
        # ❌ lines 267, 275-276: Keep markdown-like bare fence
        ("```\n# A Title\n- A list item\n```", True),
        # ❌ lines 270, 275-276: Keep source-like bare fence
        ("```\nimport os\ndef main():\n  pass\n```", True),
        # ❌ lines 275-276: Discard generic-looking bare fence
        ("```\nJust some plain text that doesn't look like code or markdown.```", False),
    ]
)
def test_extract_file_blocks_from_text_bare_fence_filtering(content, should_be_extracted):
    """Tests filtering of bare fences based on content analysis."""
    blocks = extract_file_blocks_from_text(content)
    assert len(blocks) == (1 if should_be_extracted else 0)


# Test _extract_file_spans_bottom_up for uncovered branches
@pytest.mark.parametrize(
    "text, expected_span_count",
    [
        # ❌ line 336: Closer with no matching opener.
        ("no opener here\n```", 0),
        # ❌ line 344: Body end is not after body start.
        ("```python ```", 0),
        # ❌ line 339: Opener is already covered by a larger, previously found span.
        # The outer block (A...A) is found first. The inner closer (B) is in a
        # covered region and skipped. The inner opener for C is also skipped.
        ("```A\n  ```B\n  ```C\n  ```\n```A", 1),
    ]
)
def test_extract_file_spans_bottom_up_uncovered(text, expected_span_count):
    """Tests uncovered branches in the bottom-up span extraction logic."""
    spans = _extract_file_spans_bottom_up(text)
    assert len(spans) == expected_span_count


# Test _span_slice_including_fences
def test_span_slice_including_fences_no_trailing_newline():
    """Tests the fallback for finding span end when fence is at EOF."""
    # ❌ lines 390-391: Fallback when fence is at end of file without newline
    text = "```python\ncode\n```"
    tokens = _tokenize_fences(text)
    open_tok, close_tok = tokens[0], tokens[1]

    # To ensure the fallback is hit, we manually unset line_end, as the tokenizer
    # would normally set it to len(text).
    mock_close_tok = FenceToken(**{**close_tok.__dict__, "line_end": None})

    start, end = _span_slice_including_fences(text, open_tok, mock_close_tok)
    assert start == 0
    assert end == len(text)