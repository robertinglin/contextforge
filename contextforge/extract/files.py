# contextforge/extract/files.py
# Python 3.8+
from __future__ import annotations

import re
import sys
from typing import List, Dict, Optional, Tuple

from .diffs import CONTEXT_LINES
from ..models.fence import FenceToken


# =============================
# Fence tokenization
# =============================

def _line_bounds(text: str, idx: int) -> Tuple[int, int]:
    if idx < 0: idx = 0
    if idx > len(text): idx = len(text)
    ls = text.rfind("\n", 0, idx) + 1
    le_pos = text.find("\n", idx)
    le = le_pos if le_pos != -1 else len(text)
    return ls, le


def _first_token(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    return s.split()[0].lower()


def _tokenize_fences(text: str) -> List[FenceToken]:
    tokens: List[FenceToken] = []
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


def _context_before(text: str, idx: int, lines: int = CONTEXT_LINES) -> str:
    snippet = text[:idx]
    parts = snippet.splitlines()[-lines:]
    return "\n".join(parts)


def _line_index_for_charpos(text: str, pos: int) -> int:
    # number of '\n' before pos
    return text.count("\n", 0, pos)


# =============================
# Path hint extraction (2-line lookback)
# =============================

# NOTE: CHANGE — allow bare filenames like README.md (no slashes)
_PATH_UNIX = r'(?:\.?/)?(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,8}'
_PATH_WIN  = r'(?:[A-Za-z]:\\)?(?:[\w.\-]+\\)+[\w.\-]+\.[A-Za-z0-9]{1,8}'
_FILENAME  = r'[\w.\-]+\.[A-Za-z0-9]{1,8}'  # NEW
_PATH_ANY  = rf'(?:{_PATH_UNIX}|{_PATH_WIN}|{_FILENAME})'  # UPDATED

_LABELLED_PATH_RE = re.compile(
    rf'(?i)\b(?:new|create(?:d)?|add(?:ed)?|write|save|file(?:name)?|filepath|path)\b\s*:?\s*["\'`]*'
    rf'(?P<path>{_PATH_ANY})'
)
_UNLABELLED_PATH_RE = re.compile(rf'(?P<path>{_PATH_ANY})')


def _extract_path_hint_from_lines(lines: List[str]) -> Optional[str]:
    buf = "\n".join(lines)
    m = _LABELLED_PATH_RE.search(buf)
    if m:
        return m.group("path").replace('\\', '/')
    m = _UNLABELLED_PATH_RE.search(buf)
    if m:
        return m.group("path").replace('\\', '/')
    return None


# =============================
# File-block pairing (outside-in)
# =============================

def _file_score(body: str, language: str) -> float:
    """
    Heuristic score for how much 'body' looks like a coherent file block.
    Rewards Markdown structure if language is md/markdown, otherwise generic source cues.
    """
    if not body.strip():
        return 0.0

    n = len(body.splitlines())
    score = min(n * 0.25, 30.0)

    lang = (language or "").lower()
    text = body

    if lang in ("md", "markdown"):
        score += 2.0 * len(re.findall(r"^#{1,6}\s", text, re.MULTILINE))
        score += 1.0 * len(re.findall(r"^\s*[-*+]\s", text, re.MULTILINE))
        score += 1.5 * len(re.findall(r"\[[^\]]+\]\([^)]+\)", text))
        score += 2.0 * len(re.findall(r"(^|\n)```", text))  # inner fences
    else:
        score += 1.0 * len(re.findall(r"^\s*#\!", text, re.MULTILINE))
        score += 0.5 * (text.count("{") + text.count("}"))
        score += 1.0 * len(re.findall(r"^\s*(def|class|import|package)\b", text, re.MULTILINE))
        score += 1.0 * len(re.findall(r'^\s*"(?:\\.|[^"])*"\s*:', text, re.MULTILINE))  # naive JSON-ish

    return score


def _body_slice_for_open_file(text: str, open_tok: FenceToken, close_tok: FenceToken) -> Tuple[int, int]:
    """
    Compute [start, end) for a generic code fence body.
    - Prefer starting after the opener line's newline.
    - If opener has same-line content after the fence, strip a single leading language token
      + one space if present, then include the rest.
    """
    # default to next line
    start = open_tok.line_end
    if start < len(text) and start >= 1 and text[start - 1] == "\n":
        pass

    after = open_tok.after
    if after.strip():
        trimmed = after.lstrip()
        lang_tok = _first_token(trimmed)
        if lang_tok:
            trimmed2 = trimmed[len(lang_tok):]
            if trimmed2.startswith(" "):
                trimmed2 = trimmed2[1:]
            start = open_tok.end + (len(after) - len(trimmed2))
        else:
            start = open_tok.end

    end = close_tok.start
    return start, end
def _best_close_for_open_file(text: str, tokens: List[FenceToken], open_idx: int) -> Optional[int]:
    """
    Outside-in pairing for generic file blocks.
    Valid closer:
      - same char
      - len >= open.len
      - no trailing text on the close line (supports '};```' / '};~~~')
    Choose the candidate that maximizes a file score over the spanned body.
    """
    open_tok = tokens[open_idx]
    best_j = None
    best_score = -1.0
    language = open_tok.info_first_token or ""

    for j in range(len(tokens) - 1, open_idx, -1):
        close_tok = tokens[j]
        if close_tok.char != open_tok.char or close_tok.length < open_tok.length:
            continue
        if close_tok.after.strip():
            continue

        body_start, body_end = _body_slice_for_open_file(text, open_tok, close_tok)
        if body_end < body_start:
            continue

        body = text[body_start:body_end]
        score = _file_score(body, language)

        if score > best_score:
            best_score = score
            best_j = j
            # "good enough" -> prefer the farthest we hit first (since scanning bottom-up)
            if score >= 8.0:
                break

    return best_j


def _find_matching_open_for_close(tokens: List[FenceToken], close_idx: int) -> Optional[int]:
    """
    Scan *backwards* from a closer and match the correct opener using a depth counter.
    - Opener: same fence char, len >=, and has trailing info (lang/id) on the line.
    - Closer: same fence char, len >=, and has NO trailing info on the line.
    Depth parity/odd-even naturally falls out of this counter: when depth returns to 0, we've hit the opener.
    """
    close_tok = tokens[close_idx]
    # Only treat as a valid closer if there's no info after the fence.
    if close_tok.after.strip():
        return None

    depth = 1
    for i in range(close_idx - 1, -1, -1):
        t = tokens[i]
        if t.char != close_tok.char or t.length < close_tok.length:
            continue
        is_open = bool(t.after.strip())  # lang/info present => opener
        if is_open:
            depth -= 1
            if depth == 0:
                return i
        else:
            depth += 1
    return None


# =============================
# Public API: File blocks
# =============================

def extract_file_blocks_from_text(markdown_content: str) -> List[Dict[str, object]]:
    """
    Extract *full file* fenced code blocks (non-diff) using bottom-up, backwards pairing.
    Also exposes body_start/body_end for test compatibility.
    """
    text = markdown_content
    results: List[Dict[str, object]] = []

    # Use the robust bottom-up spans (handles nesting; keeps siblings separate).
    spans = _extract_file_spans_bottom_up(text)
    for sp in spans:
        open_tok = sp["open_tok"]
        close_tok = sp["close_tok"]
        lang = (open_tok.info_first_token or "")
        if lang in ("diff", "patch"):
            # leave these to the diff extractor
            continue

        body_start, body_end = sp["body_start"], sp["body_end"]
        body = text[body_start:body_end]
        file_path = sp["file_path"] or ""
        language = lang if lang else "plain"

        # For bare fences with no path: keep if markdown-ish or source-ish
        if lang == "" and file_path == "":
            snippet = body[:2000]
            looks_like_markdown = bool(re.search(r"(^|\n)#{1,6}\s|(^|\n)[-*+]\s|\[[^\]]+\]\([^)]+\)", snippet))
            looks_like_source = bool(re.search(
                r'(^\s*#\!|^\s*(def|class|import|package)\b|[{;}])', snippet, re.MULTILINE))
            if not (looks_like_source or looks_like_markdown):
                continue

        results.append({
            "type": "file",
            "language": language,
            "code": body.strip("\n"),
            "file_path": file_path,
            "start": body_start,
            "end": body_end,
            "body_start": body_start,   # alias for tests
            "body_end": body_end,       # alias for tests
            "open_fence": {"char": open_tok.char, "length": open_tok.length,
                           "start": open_tok.start, "end": open_tok.end},
            "close_fence": {"char": close_tok.char, "length": close_tok.length,
                            "start": close_tok.start, "end": close_tok.end},
            "context": _context_before(text, open_tok.start, CONTEXT_LINES),
        })

    return results


def _extract_file_spans_bottom_up(text: str) -> List[dict]:
    """
    Extract fenced file/code blocks from `text`.
    Bottom-up strategy:
      1) Tokenize all fences.
      2) Traverse tokens from bottom to top; for each *closer*, scan backwards to find the matching opener
         using depth counting over same-char fences with len>=.
      3) Mark the whole span as covered so we skip any inner fences subsequently.
    """
    tokens = _tokenize_fences(text)
    results: List[dict] = []

    # Keep covered character spans to avoid producing overlapping blocks.
    covered: List[Tuple[int, int]] = []

    def _is_covered(pos: int) -> bool:
        for a, b in covered:
            if a <= pos < b:
                return True
        return False

    # Walk bottom-up so large, outer blocks are claimed first; inner fences get skipped.
    for j in range(len(tokens) - 1, -1, -1):
        close_tok = tokens[j]
        if close_tok.after.strip():
            # has info => it's an opener, not a closer
            continue
        if _is_covered(close_tok.line_start):
            continue

        i = _find_matching_open_for_close(tokens, j)
        if i is None:
            continue
        open_tok = tokens[i]
        if _is_covered(open_tok.line_start):
            continue

        # Build block boundaries
        body_start, body_end = _body_slice_for_open_file(text, open_tok, close_tok)
        if body_end <= body_start:
            continue

        # Compute metadata
        language = (open_tok.info_first_token or "plain")
        opener_line_idx = _line_index_for_charpos(text, open_tok.line_start)
        all_lines = text.splitlines()
        look_from = max(0, opener_line_idx - 2)
        context_lines = all_lines[look_from:opener_line_idx]
        file_path = _extract_path_hint_from_lines(context_lines) or ""

        results.append({
            "type": "file",
            "open_tok": open_tok,
            "close_tok": close_tok,
            "language": language,
            "file_path": file_path,
            "body_start": body_start,
            "body_end": body_end,
        })

        # Mark the entire span (including the fences) as covered.
        # We expand to include the full lines of the fences via helper:
        span_start, span_end = _span_slice_including_fences(text, open_tok, close_tok)
        covered.append((span_start, span_end))

    # Because we walked bottom-up, results are reverse-order; keep original order by start offset.
    results.sort(key=lambda r: r["body_start"])
    return results


def _span_slice_including_fences(text: str, open_tok: FenceToken, close_tok: FenceToken) -> Tuple[int, int]:
    """
    Like _body_slice_for_open_file, but returns the absolute span including the opener/closer fence lines.
    Used to 'cover' a region so nested inner fences aren't re-processed.
    """
    # Start at beginning of the opener line; end at end of the closer line.
    start = open_tok.line_start
    # Move to end-of-line for the closer; tokens already store line_end if available.
    end = getattr(close_tok, "line_end", None)
    if end is None:
        # Fallback: consume the whole line by finding the next newline after the fence.
        # (Safe: if not found, take len(text))
        nl = text.find("\n", close_tok.line_start)
        end = len(text) if nl == -1 else nl + 1
    return start, end