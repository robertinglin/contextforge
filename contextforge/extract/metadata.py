# contextforge/extract/metadata.py
import re
from typing import Dict, Optional

from ..utils.parsing import _contains_truncation_marker


def extract_file_info_from_context_and_code(context: str, code: str, lang: str) -> Optional[Dict[str, str]]:
    """
    Deterministically extract file path and change type without LLM.
    Returns None if cannot be determined.
    """
    # Combine context and code for analysis
    full_text = f"{context}\n{code}"

    # Pattern 1: File path patterns - ordered by specificity
    file_patterns = [
        # Backticked filename: `path/to/file.ext` (stop at closing backtick)
        r'`([^`]+\.[a-zA-Z0-9]+)`',
        # "File: path/to/file.ext" - capture until space or end of reasonable filename chars
        r'(?:^|\n)\s*[Ff]ile:\s*([^\s\n]+\.[a-zA-Z0-9]+)',
        # "File path/to/file.ext" - capture until space or end of reasonable filename chars
        r'(?:^|\n)\s*[Ff]ile\s+([^\s\n]+\.[a-zA-Z0-9]+)',
        # Quoted filename: "path/to/file.ext"
        r'"([^"\s]+\.[a-zA-Z0-9]+)"',
        # Markdown header: ## file.ext or # file.ext
        r'(?:^|\n)\s*##?\s*([^\s\n]+\.[a-zA-Z0-9]+)',
        # Path-like pattern at start of line or after whitespace
        r'(?:^|\s)([a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_.-]+)+\.[a-zA-Z0-9]+)(?=\s|$|[^a-zA-Z0-9_./\-])',
    ]

    file_path = None
    for i, pattern in enumerate(file_patterns):
        matches = re.findall(pattern, full_text, re.MULTILINE)
        for match in matches:
            # Clean up the match and validate
            potential_path = match.strip()
            # Basic validation: must have extension, reasonable length, valid chars
            if (potential_path and
                '.' in potential_path and
                len(potential_path) > 3 and
                not potential_path.startswith('.') and
                re.match(r'^[a-zA-Z0-9_./\-]+$', potential_path)):
                file_path = potential_path
                break
        if file_path:
            break

    # Pattern 2: Diff headers (highest priority - override any file_path found above)
    # Order is important: +++ is preferred over ---
    diff_indicators = [
        r'^\s*\+\+\+\s+(?:b/)?(.+)$',
        r'^\s*---\s+(?:a/)?(.+)$',
        r'^\s*diff\s+--git\s+a/(\S+)', # This one is git-specific, stays the same
        r'^\s*Index:\s*(.+)$'
    ]
    for pattern in diff_indicators:
        match = re.search(pattern, code, re.MULTILINE)
        if match:
            path_candidate = match.group(1).strip()
            # Ignore '--- a//dev/null'
            if "--- a/" in match.string and "dev/null" in path_candidate:
                continue
            file_path = path_candidate
            return {"file_path": file_path, "change_type": "diff"}

    # Pattern 3: Check if code contains diff markers
    has_diff_markers = bool(re.search(r'^\s*@@.*@@', code, re.MULTILINE))
    has_add_remove = bool(re.search(r'^\s*[+-]', code, re.MULTILINE))

    if has_diff_markers or (has_add_remove and any(x in code for x in ['--- a/', '+++ b/'])):
        return {"file_path": file_path, "change_type": "diff"}

    # Pattern 4: Full file indicators
    if file_path:
        # Check for truncation markers
        if _contains_truncation_marker(code):
            return {"file_path": file_path, "change_type": "full_replacement"}

        # Check for common file structure patterns
        file_structure_indicators = [
            r'^\s*(?:import|from|#include|package|namespace)',  # Import statements
            r'^\s*(?:class|def|function|var|let|const)\s+\w+',  # Declarations
            r'^\s*<!DOCTYPE|<html|<\?xml',                      # Markup
            r'^\s*\{[\s\n]*"',                                  # JSON
        ]

        for pattern in file_structure_indicators:
            if re.search(pattern, code, re.MULTILINE | re.IGNORECASE):
                return {"file_path": file_path, "change_type": "full_replacement"}

    return None