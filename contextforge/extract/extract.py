# contextforge/extract/extract.py

from __future__ import annotations
import re
import textwrap
import logging


# Path extraction helpers (retained for feature parity)
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


def _preprocess_fences(text: str) -> str:
    """
    Pre-processes markdown to handle ambiguous fence combinations.
    Specifically, it splits a closing fence followed immediately by an opening
    fence on the same line into two separate lines.
    Example: '``````diff' becomes '```\n```diff'.
    """
    # This regex finds a fence of 3+ backticks or tildes (group 1)
    # followed by another fence of 3+ of the same character plus an info string (group 2).
    return re.sub(r"([`~]{3,})\s*([`~]{3,}[^\n\r]+)", r"\1\n\2", text)

def extract_all_blocks_from_text(markdown_content: str) -> list[dict[str, object]]:
    """
    Extract all **top-level** fenced code blocks. This robust implementation
    correctly handles nested fences, same-line closers, and avoids false
    positives from fence-like sequences inside string literals.
    """
    
    text = _preprocess_fences(markdown_content)
    blocks: list[dict[str, object]] = []

    # Regex to find a potential OPENER at the start of a line.
    opener_re = re.compile(r"(?m)^[ \t]*(?P<fence>(?P<ch>`|~)\2{2,})(?P<info>[^\n\r]*)")
    
    # Regex to find ANY fence-like sequence for scanning inside a block.
    any_fence_re = re.compile(r"(`{3,}|~{3,})")
    
    cursor = 0
    while cursor < len(text):
        m = opener_re.search(text, cursor)
        if not m:
            break

        opener_fence = m.group("fence")
        opener_char = m.group("ch")
        opener_len = len(opener_fence)
        info_string = m.group("info").strip()

        # Start scanning for the closer from the end of the opener's line.
        content_start = m.end()
        if text.startswith("\r\n", content_start):
            content_start += 2
        elif text.startswith("\n", content_start):
            content_start += 1
        
        # Use a stack to manage nesting. Push the opener onto the stack.
        fence_stack = [(opener_char, opener_len)]
        
        content_end = -1
        next_search_start = m.end()

        scan_pos = content_start
        while scan_pos < len(text):
            candidate_match = any_fence_re.search(text, scan_pos)
            if not candidate_match:
                break

            candidate_start, candidate_end = candidate_match.span()
            candidate_fence = text[candidate_start:candidate_end]
            candidate_char = candidate_fence[0]
            candidate_len = len(candidate_fence)
            
            # Find the true end of the candidate's line.
            line_end_pos = text.find('\n', candidate_end)
            if line_end_pos == -1:
                line_end_pos = len(text)
            
            # An "info string" is ONLY the non-whitespace text on the SAME line.
            info_on_same_line = text[candidate_end:line_end_pos].strip()

            # An opener MUST be at the start of its line.
            line_start_pos = text.rfind('\n', 0, candidate_start) + 1
            is_at_line_start = not text[line_start_pos:candidate_start].strip()

            # Determine the candidate's type based on strict rules.
            if is_at_line_start:
                # If it's at the start of a line, it's either a nested opener or a closer.
                if info_on_same_line:
                    # Nested opener
                    fence_stack.append((candidate_char, candidate_len))
                else:
                    # Potential closer
                    if fence_stack:
                        stack_char, stack_len = fence_stack[-1]
                        if candidate_char == stack_char and candidate_len >= stack_len:
                            fence_stack.pop()
            else:
                # If NOT at the start of a line, it can ONLY be a closer, never an opener.
                if fence_stack and not info_on_same_line:
                    stack_char, stack_len = fence_stack[-1]
                    if candidate_char == stack_char and candidate_len >= stack_len:
                        fence_stack.pop()


            if not fence_stack:
                # Stack is empty, so we've closed the top-level block.
                content_end = candidate_start
                next_search_start = candidate_end
                break
            
            # Continue the scan from the end of the current candidate.
            scan_pos = candidate_end
        
        if content_end != -1:
            code = text[content_start:content_end]

            parts = info_string.split()
            lang = parts[0].lower() if parts and "=" not in parts[0] else ""
            file_path_hint = ""
            for part in parts:
                if part.startswith("file="):
                    file_path_hint = part.split("=", 1)[1].strip("'\"")

            if not file_path_hint:
                context_lines = text[:m.start()].splitlines()[-2:]
                file_path_hint = _extract_path_hint_from_lines(context_lines) or ""

            blocks.append({
                "type": "code",
                "language": lang or "plain",
                "code": textwrap.dedent(code),
                "file_path": file_path_hint,
                "start": content_start,
                "end": content_end,
                "context": _context_before(text, m.start()),
            })
        
        cursor = next_search_start

    return blocks