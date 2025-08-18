# smart_paster/diff_extractor.py
# Python 3.8+
from __future__ import annotations

import re

from ..errors import ExtractError
from ..models.fence import FenceToken

CONTEXT_LINES = 5


# =============================
# Fence tokenization & helpers
# =============================

def _line_bounds(text: str, idx: int) -> tuple[int, int]:
    if idx < 0:
        idx = 0
    if idx > len(text):
        idx = len(text)
    ls = text.rfind("\n", 0, idx) + 1
    le_pos = text.find("\n", idx)
    le = le_pos if le_pos != -1 else len(text)
    return ls, le


def _line_index_for_charpos(text: str, pos: int) -> int:
    """Return 0-based line index for the given absolute char position."""
    if pos <= 0:
        return 0
    return text.count("\n", 0, pos)


def _first_token(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    return s.split()[0].lower()


def _tokenize_fences(text: str) -> list[FenceToken]:
    """Return all 3+ backtick/tilde runs found anywhere in the text."""
    tokens: list[FenceToken] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("`", "~"):
            j = i + 1
            while j < n and text[j] == ch:
                j += 1
            run = j - i
            if run >= 3:
                ls, le = _line_bounds(text, i)
                before = text[ls:i]
                after = text[j:le]
                info_tok = _first_token(after)
                tokens.append(FenceToken(
                    start=i, end=j, char=ch, length=run,
                    before=before, after=after, info_first_token=info_tok,
                    line_start=ls, line_end=le
                ))
                i = j
                continue
        i += 1
    return tokens


# =============================
# Diff heuristics
# =============================

def _looks_like_diff(text: str) -> bool:
    if "diff --git " in text:
        return True
    if ("--- " in text and "+++ " in text):
        return True
    return bool(re.search(r"^\s*@@\s+-\d+", text, flags=re.MULTILINE))


def _diff_score(text: str) -> float:
    """Heuristic score: higher => more diff-like."""
    if not text.strip():
        return 0.0
    lines = text.splitlines()
    count_diff_git = sum(1 for ln in lines if ln.startswith("diff --git "))
    count_minus_hdr = sum(1 for ln in lines if ln.startswith("--- "))
    count_plus_hdr  = sum(1 for ln in lines if ln.startswith("+++ "))
    count_hunks     = sum(1 for ln in lines if ln.startswith("@@"))
    count_add_rm    = sum(1 for ln in lines if ln.startswith("+") or ln.startswith("-"))
    score = 0.0
    score += 5.0 * count_diff_git
    if count_minus_hdr and count_plus_hdr:
        score += 3.0
    score += 1.0 * count_hunks
    score += min(count_add_rm * 0.05, 3.0)  # cap noisy +/-
    return score


# =============================
# Pairing (outside-in)
# =============================

def _best_close_for_open(text: str, tokens: list[FenceToken], open_idx: int) -> int | None:
    """
    Choose the best closing fence for tokens[open_idx] by scanning from the end backward.
    A valid closer:
      - has same char,
      - length >= open.length,
      - has no non-whitespace AFTER the run on its line (supports '}```' / '}~~~').
    We pick the candidate that maximizes _diff_score(body). Prefer farthest (early exit).
    """
    open_tok = tokens[open_idx]
    best_j = None
    best_score = -1.0
    opener_lang = open_tok.info_first_token

    body_start = open_tok.end  # provisional; corrected by _body_slice_for_open

    for j in range(len(tokens) - 1, open_idx, -1):
        close_tok = tokens[j]
        # if close_tok.char != open_tok.char or close_tok.length < open_tok.length:
        #     continue
        # Allow trailing text ONLY if it starts a new diff/patch (dual-purpose closer+opener)
        after_stripped = close_tok.after.strip()
        is_inline_opener = bool(after_stripped) and close_tok.info_first_token in ("diff", "patch")
        if after_stripped and not is_inline_opener:
            continue

        # Provisional body for scoring only
        body = text[body_start:close_tok.start]
        score = _diff_score(body)
        if opener_lang in ("diff", "patch") and not _looks_like_diff(body):
            score *= 0.2

        # Prefer boundaries that also start another diff on the same line
        if is_inline_opener:
            score += 2.0


        if score > best_score:
            best_score = score
            best_j = j
            # Early-exit only when this candidate also starts the next diff,
            # so we keep adjacent diff blocks neatly separated.
            if is_inline_opener and score >= 5.0:
                break

    return best_j


def _context_before(text: str, idx: int, lines: int = CONTEXT_LINES) -> str:
    snippet = text[:idx]
    parts = snippet.splitlines()[-lines:]
    return "\n".join(parts)


def _body_slice_for_open(text: str, open_tok: FenceToken, close_tok: FenceToken) -> tuple[int, int]:
    """
    Compute [start, end) of the body between the opener and closer.
    - Normally body starts on the NEXT line.
    - If opener has same-line content, include it BUT strip a leading info token
      ('diff' or 'patch') and one space if present, so we don't swallow the info string.
    """
    # Default: start after the newline that ends the opener's line
    start = open_tok.line_end
    if start < len(text) and text[start] == "\n":
        start += 1

    # Same-line content after the fence?
    after = open_tok.after
    if after.strip():
        trimmed = after.lstrip()
        if trimmed.lower().startswith("diff"):
            trimmed = trimmed[4:]
            if trimmed.startswith(" "):
                trimmed = trimmed[1:]
        elif trimmed.lower().startswith("patch"):
            trimmed = trimmed[5:]
            if trimmed.startswith(" "):
                trimmed = trimmed[1:]
        # Move start to the beginning of the trimmed content
        start = open_tok.end + (len(after) - len(trimmed))

    end = close_tok.start
    return start, end


# =============================
# Multi-file splitting
# =============================

def _split_multi_file_diff(diff_text: str) -> list[tuple[str, str]]:
    """
    Split a (possibly multi-file) diff into per-file chunks.
    Keeps 'diff --git' and 'index' lines WITH the file they belong to.
    Returns: List[(file_path (maybe ''), chunk_text)]
    """
    lines = diff_text.splitlines()
    chunks: list[list[str]] = []
    paths: list[str] = []
    cur: list[str] = []
    cur_path: str | None = None
    cur_has_diff_git = False
    cur_has_header = False  # saw '--- ' in current chunk

    def flush():
        nonlocal cur, cur_path, cur_has_diff_git, cur_has_header
        if cur:
            chunks.append(cur[:])
            paths.append(cur_path or "")
        cur = []
        cur_path = None
        cur_has_diff_git = False
        cur_has_header = False

    def extract_path_from_line(line: str) -> str | None:
        # Handles `diff --git a/old/path b/new/path` -> we want `new/path`
        m = re.match(r"^diff --git a/.+? b/(.+)$", line)
        if m:
            return m.group(1).strip().split('\t')[0].replace('\\', '/')

        m = re.match(r"^\+\+\+ (?:b/)?(.+)$", line)
        if m:
            return m.group(1).strip().split('\t')[0].replace('\\', '/')

        m = re.match(r"^--- (?:a/)?(.+)$", line)
        if m:
            path = m.group(1).strip().split('\t')[0]
            if path != '/dev/null':
                return path.replace('\\', '/')
        return None

    for ln in lines:
        if ln.startswith("diff --git "):
            flush()
            cur_has_diff_git = True
            cur_path = extract_path_from_line(ln)
        elif ln.startswith("--- "):
            # In unified diffs w/o 'diff --git', encountering a second '--- ' means new file
            if cur_has_header and not cur_has_diff_git:
                flush()
            cur_has_header = True
            maybe = extract_path_from_line(ln)
            if maybe:
                cur_path = maybe
        elif ln.startswith("+++ "):
            maybe = extract_path_from_line(ln)
            if maybe:
                cur_path = maybe

        cur.append(ln)

    flush()
    return [(paths[i], "\n".join(chunks[i]).strip()) for i in range(len(chunks))]


# =============================
# Public API
# =============================

def extract_diffs_from_text(
    markdown_content: str,
    *,
    allow_bare_fences_that_look_like_diff: bool = True,
    split_per_file: bool = True,
) -> list[dict[str, object]]:
    """
    Robust, "outside-in" diff extractor.

    - Tokenize *all* 3+ backtick or tilde runs.
    - For each potential opener, walk closers from the end backward and score the span.
    - Accept the best-scoring match, resilient to stray backticks/ tildes in the body.
    - Handles inline open/close on the same line and closers attached to last code line.
    - Optionally split multi-file diffs (default: True).

    Returns a list of dicts like:
      {
        "code": <diff text>,
        "lang": "diff",
        "start": <abs index of body start>,
        "end":   <abs index of body end>,
        "open_fence": {"char": "`", "length": 3, "start": ..., "end": ...} or None,
        "close_fence": {...} or None,
        "context": <last CONTEXT_LINES of text before opener>
      }
    """
    text = markdown_content
    tokens = _tokenize_fences(text)
    results: list[dict[str, object]] = []
    consumed_until = -1
    i = 0

    while i < len(tokens):
        tok = tokens[i]
        if tok.end <= consumed_until:
            i += 1
            continue

        is_explicit_diff = tok.info_first_token in ("diff", "patch")
        can_consider = is_explicit_diff or allow_bare_fences_that_look_like_diff
        if not can_consider:
            i += 1
            continue

        j = _best_close_for_open(text, tokens, i)
        if j is None:
            i += 1
            continue

        open_tok = tokens[i]
        close_tok = tokens[j]
        body_start, body_end = _body_slice_for_open(text, open_tok, close_tok)
        if body_end < body_start:
            i += 1
            continue

        body = text[body_start:body_end]
        # If the fence explicitly declares a diff but the body doesn't look like one,
        # treat this as a malformed diff and raise with a helpful location.
        if is_explicit_diff and not _looks_like_diff(body):
            line_no = _line_index_for_charpos(text, open_tok.start) + 1  # 1-based
            raise ExtractError(f"Malformed diff fence near line {line_no}: expected a unified diff body.")

        if not is_explicit_diff and not _looks_like_diff(body) and _diff_score(body) < 4.0:
            i += 1
            continue

        # Per-file split
        file_chunks = _split_multi_file_diff(body) if split_per_file else [("", body)]
        if not file_chunks:
            file_chunks = [("", body)]

        for file_path, chunk_text in file_chunks:
            if not chunk_text.strip():
                continue
            results.append({
                "code": chunk_text.strip("\n"),
                "lang": "diff",
                "file_path": file_path,
                "start": body_start,
                "end": body_end,
                "open_fence": {"char": open_tok.char, "length": open_tok.length,
                               "start": open_tok.start, "end": open_tok.end},
                "close_fence": {"char": close_tok.char, "length": close_tok.length,
                                "start": close_tok.start, "end": close_tok.end},
                "context": _context_before(text, open_tok.start, CONTEXT_LINES),
            })

        # If the closer line also begins the next diff (e.g., ``````diff),
        # let the main loop see this same token again as the opener.
        if close_tok.after.strip() and close_tok.info_first_token in ("diff", "patch"):
            consumed_until = close_tok.start
        else:
            consumed_until = close_tok.end
        while i < len(tokens) and tokens[i].start < consumed_until:
            i += 1

    # Fallback: raw diff with no fences at all
    if not results and _looks_like_diff(text):
        file_chunks = _split_multi_file_diff(text) or [("", text)]
        for file_path, chunk in file_chunks:
            results.append({
                "code": chunk.strip(),
                "lang": "diff",
                "start": 0,
                "file_path": file_path,
                "end": len(text),
                "open_fence": None,
                "close_fence": None,
                "context": "Raw patch provided as input.",
            })

    return results
