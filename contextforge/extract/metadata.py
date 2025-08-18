# contextforge/extract/metadata.py
import re
from typing import Dict, List, Optional

from ..utils.parsing import _contains_truncation_marker
from .diffs import extract_diffs_from_text


def extract_file_info_from_context_and_code(
    context: str, code: str, lang: str
) -> Optional[Dict[str, str]]:
    """
    Deterministically extract file path and change type without LLM.
    Returns None if cannot be determined.
    """
    # Combine context and code for analysis
    full_text = f"{context}\n{code}"

    # Pattern 1: File path patterns - ordered by specificity
    file_patterns = [
        r"`([^`]+\.[a-zA-Z0-9]+)`",  # backticked filename
        r"(?:^|\n)\s*[Ff]ile:\s*([^\s\n]+\.[a-zA-Z0-9]+)",  # "File: path/to.ext"
        r"(?:^|\n)\s*[Ff]ile\s+([^\s\n]+\.[a-zA-Z0-9]+)",  # "File path/to.ext"
        r'"([^"\s]+\.[a-zA-Z0-9]+)"',  # quoted filename
        r"(?:^|\n)\s*##?\s*([^\s\n]+\.[a-zA-Z0-9]+)",  # Markdown header with filename
        r"(?:^|\s)([a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_.-]+)+\.[a-zA-Z0-9]+)(?=\s|$|[^a-zA-Z0-9_./\-])",
    ]

    file_path = None
    for pattern in file_patterns:
        matches = re.findall(pattern, full_text, re.MULTILINE)
        for match in matches:
            potential_path = match.strip()
            if (
                potential_path
                and "." in potential_path
                and len(potential_path) > 3
                and not potential_path.startswith(".")
            ):
                file_path = potential_path
                break
        if file_path:
            break

    # Pattern 2: Diff indicators in code
    diff_indicators = [
        r"^\s*\+\+\+\s+(?:b/)?(.+)$",
        r"^\s*---\s+(?:a/)?(.+)$",
        r"^\s*diff\s+--git\s+a/(\S+)",
        r"^\s*Index:\s*(.+)$",
    ]
    for pattern in diff_indicators:
        match = re.search(pattern, code, re.MULTILINE)
        if match:
            path_candidate = match.group(1).strip()
            if "--- a/" in match.string and "dev/null" in path_candidate:
                continue
            file_path = path_candidate
            return {"file_path": file_path, "change_type": "diff"}

    # Pattern 3: Raw diff markers
    has_diff_markers = bool(re.search(r"^\s*@@.*@@", code, re.MULTILINE))
    has_add_remove = bool(re.search(r"^\s*[+-]", code, re.MULTILINE))
    if has_diff_markers or (has_add_remove and any(x in code for x in ["--- a/", "+++ b/"])):
        return {"file_path": file_path, "change_type": "diff"}

    # Pattern 4: Full file indicators (non-diff)
    if file_path:
        if _contains_truncation_marker(code):
            return {"file_path": file_path, "change_type": "full_replacement"}

        file_structure_indicators = [
            r"^\s*(?:import|from|#include|package|namespace)",
            r"^\s*(?:class|def|function|var|let|const)\s+\w+",
            r"^\s*<!DOCTYPE|<html|<\?xml",
            r'^\s*\{[\s\n]*"',  # naive JSON
        ]
        for pattern in file_structure_indicators:
            if re.search(pattern, code, re.MULTILINE | re.IGNORECASE):
                return {"file_path": file_path, "change_type": "full_replacement"}

    return None


def detect_new_files(markdown_content: str) -> List[str]:
    """
    Detect files that are newly created according to diff blocks in the text.

    Rules handled:
      - `diff --git` blocks that include `new file mode`.
      - Unified diffs with `--- /dev/null` paired with `+++ b/<path>` (or `+++ <path>`).
    Returns a sorted, de-duplicated list of file paths.
    """
    candidates = extract_diffs_from_text(
        markdown_content,
        allow_bare_fences_that_look_like_diff=True,
        split_per_file=True,
    )

    new_files = set()
    for blk in candidates:
        code = blk.get("code", "")
        # Case 1: Explicit new file mode after a diff --git header
        if re.search(r"^\s*new file mode\s+\d+", code, re.MULTILINE):
            m = re.search(r"^diff\s+--git\s+a/\S+\s+b/(\S+)", code, re.MULTILINE)
            if m:
                new_files.add(m.group(1))
                continue

        # Case 2: /dev/null old header + +++ new path
        has_devnull = re.search(r"^\s*---\s+(?:a/)?/dev/null\s*$", code, re.MULTILINE)
        if has_devnull:
            m2 = re.search(r"^\s*\+\+\+\s+(?:b/)?(\S+)", code, re.MULTILINE)
            if m2 and m2.group(1) != "/dev/null":
                new_files.add(m2.group(1))

    return sorted(new_files)
