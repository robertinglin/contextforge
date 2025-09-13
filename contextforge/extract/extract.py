# Python 3.8+
from __future__ import annotations

import re

# This helper is retained to find file paths in the context
_PATH_UNIX = r"(?:\.?/)?(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,8}"
_PATH_WIN = r"(?:[A-Za-z]:\\)?(?:[\w.\-]+\\)+[\w.\-]+\.[A-Za-z0-9]{1,8}"
_FILENAME = r"[\w.\-]+\.[A-Za-z0-9]{1,8}"
_PATH_ANY = rf"(?:{_PATH_UNIX}|{_PATH_WIN}|{_FILENAME})"

_LABELLED_PATH_RE = re.compile(
    rf'(?i)\b(?:new|create(?:d)?|add(?:ed)?|write|save|file(?:name)?|filepath|path)\b\s*:?\s*["\'`]*'
    rf"(?P<path>{_PATH_ANY})"
)
_UNLABELLED_PATH_RE = re.compile(rf"(?P<path>{_PATH_ANY})")


def _extract_path_hint_from_lines(lines: list[str]) -> str | None:
    buf = "\n".join(lines)
    m = _LABELLED_PATH_RE.search(buf)
    if m:
        return m.group("path").replace("\\", "/")
    m = _UNLABELLED_PATH_RE.search(buf)
    if m:
        return m.group("path").replace("\\", "/")
    return None


def _context_before(text: str, idx: int, lines: int = 5) -> str:
    snippet = text[:idx]
    parts = snippet.splitlines()[-lines:]
    return "\n".join(parts)


def extract_all_blocks_from_text(markdown_content: str) -> list[dict[str, object]]:
    """
    Extract all **top-level** fenced code blocks (backticks or tildes, fence
    length >= 3). Nested fences are treated as plain text inside their parent,
    so only the outermost block is emitted.

    Supports:
      - Backtick (```...```) and tilde (~~~...~~~) fences
      - Long fences (e.g. ````go ... ````) and closers at end of line
      - Attributes in info string (file=, from=, to=)
      - Path hints from nearby prose
    """
    text = markdown_content
    blocks: list[dict[str, object]] = []

    # Top-level opener: start of line, 3+ of the same fence char (` or ~)
    opener_re = re.compile(r"(?m)^[ \t]*(?P<fence>(?P<ch>`|~)\2{2,})(?P<info>[^\n\r]*)")
    i = 0

    while True:
        m = opener_re.search(text, i)
        if not m:
            break

        fence = m.group("fence")
        ch = m.group("ch")
        fence_len = len(fence)
        info_string = (m.group("info") or "").strip()

        # Parse info string
        parts = info_string.split()
        lang = ""
        file_path_hint = ""
        rename_from = ""
        rename_to = ""
        if parts and "=" not in parts[0]:
            lang = parts[0].lower()
        for part in parts:
            if part.startswith("file="):
                file_path_hint = part.split("=", 1)[1].strip("'\"")
            elif part.startswith("from="):
                rename_from = part.split("=", 1)[1].strip("'\"")
            elif part.startswith("to="):
                rename_to = part.split("=", 1)[1].strip("'\"")

        # Code starts right after the opener line break (support CRLF)
        code_start = m.end()
        if code_start < len(text) and text[code_start] in "\r\n":
            if text[code_start] == "\r" and code_start + 1 < len(text) and text[code_start + 1] == "\n":
                code_start += 2
            else:
                code_start += 1

        # Scan forward for the matching closer. A fence with an info string is
        # a nested opener. A fence with no info string is a potential closer.
        same_fence_seq = re.compile(re.escape(ch) + r"{" + str(fence_len) + r",}")
        pos = code_start
        nesting = 0
        code_end = None

        while True:
            c = same_fence_seq.search(text, pos)
            if not c:
                break

            line_end = text.find("\n", c.end())
            if line_end == -1:
                line_end = len(text)

            # Check for info string (any non-whitespace content) after the fence on the same line
            info_present = text[c.end():line_end].strip() != ""

            if info_present:
                nesting += 1
            else:  # No info string, this is a potential closer
                if nesting > 0:
                    nesting -= 1
                else: # Found the top-level closer
                    code_end = c.start()
                    # Advance i to start searching for the next block after this one
                    i = line_end + 1 if line_end < len(text) else len(text)
                    break

            # Continue inner search after this fence sequence
            pos = c.end()

        # If unclosed, skip this block
        if code_end is None:
            i = m.end()
            continue

        code = text[code_start:code_end].rstrip("\r\n")

        # Fallback path hint: check a couple of lines before the opener
        if not file_path_hint:
            context_lines = text[: m.start()].splitlines()[-2:]
            file_path_hint = _extract_path_hint_from_lines(context_lines) or ""

        language = lang if lang else "plain"
        block_data = {
            "type": "code",
            "language": language,
            "code": code,
            "file_path": file_path_hint,
            "start": code_start,
            "end": code_end,
            "body_start": code_start,
            "body_end": code_end,
            "context": _context_before(text, m.start()),
        }
        if rename_from and rename_to:
            block_data["rename_from"] = rename_from
            block_data["rename_to"] = rename_to

        blocks.append(block_data)

    return blocks