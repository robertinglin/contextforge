# Python 3.8+
from __future__ import annotations

import re

# This helper is retained from the previous implementation to find file paths
# in the context preceding a code block.
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
    Extracts all fenced code blocks that are not diffs.

    This implementation correctly handles nested code blocks by looking ahead.
    For each potential start fence, it expands its search window past subsequent
    start fences until it finds a chunk that contains a closing fence. This ensures
    that nested blocks are consumed as content of their parent block.
    """
    text = markdown_content
    blocks = []
    start_pattern = re.compile(r"```(.*)", re.MULTILINE)
    matches = list(start_pattern.finditer(text))

    i = 0
    while i < len(matches):
        current_match = matches[i]
        info_string = current_match.group(1).strip()

        parts = info_string.split()
        lang = ""
        file_path_hint = ""
        rename_from = ""
        rename_to = ""

        if parts:
            # lang is the first part if it doesn't contain '='
            if "=" not in parts[0]:
                lang = parts[0].lower()

        # Parse attributes for rename/move/delete
        for part in parts:
            if part.startswith("file="):
                file_path_hint = part.split("=", 1)[1].strip("'\"")
            elif part.startswith("from="):
                rename_from = part.split("=", 1)[1].strip("'\"")
            elif part.startswith("to="):
                rename_to = part.split("=", 1)[1].strip("'\"")

        # Find the search window for this block's content. We need to look
        # ahead past any nested blocks.
        search_end_offset = -1
        next_top_level_idx = i

        while True:
            next_top_level_idx += 1
            if next_top_level_idx < len(matches):
                prospective_end = matches[next_top_level_idx].start()
            else:
                prospective_end = len(text)

            # The chunk is from the end of the current fence to the start of the next one.
            chunk = text[current_match.end() : prospective_end]

            if "```" in chunk:
                # This chunk contains a closer, so this is our block boundary.
                search_end_offset = prospective_end
                break

            if next_top_level_idx >= len(matches):
                # We've reached the end of the file without finding a closer.
                break  # search_end_offset remains -1

        if search_end_offset == -1:
            # Unclosed block, ignore it.
            i += 1
            continue

        # The block content starts after the opening fence line.
        code_start_offset = current_match.end()
        nl_after_fence = text.find("\n", code_start_offset)
        if nl_after_fence != -1 and nl_after_fence < search_end_offset:
            code_start_offset = nl_after_fence + 1

        # We have a valid window. Find the last '```' in it to get the code.
        content_window = text[code_start_offset:search_end_offset]
        end_fence_pos_in_window = content_window.rfind("```")

        if end_fence_pos_in_window == -1:
            # This should not happen due to the check above, but as a safeguard:
            i = next_top_level_idx
            continue

        code = content_window[:end_fence_pos_in_window].rstrip("\r\n")
        code_end_offset = code_start_offset + end_fence_pos_in_window

        # Extract file path from context before the block as a fallback
        if not file_path_hint:
            context_lines = text[: current_match.start()].splitlines()[-2:]
            file_path_hint = _extract_path_hint_from_lines(context_lines) or ""
        language = lang if lang else "plain"

        block_data = {
            "type": "code",  # Generic type, will be classified later
            "language": language,
            "code": code,
            "file_path": file_path_hint,
            "start": code_start_offset,
            "end": code_end_offset,
            "body_start": code_start_offset,
            "body_end": code_end_offset,
            "context": _context_before(text, current_match.start()),
        }

        if rename_from and rename_to:
            block_data["rename_from"] = rename_from
            block_data["rename_to"] = rename_to

        blocks.append(block_data)

        # Move the main loop index to the start of the next top-level block.
        i = next_top_level_idx

    return blocks