# contextforge/utils/parsing.py
import re
from typing import Dict, Optional


def _contains_truncation_marker(code: str) -> bool:
    """Checks if a code block contains common truncation markers."""
    # Pattern looks for lines starting with optional whitespace, a comment character,
    # then any characters, then '...', then any characters.
    # It covers: # ..., // ..., /* ... */, <!-- ... -->, etc.
    truncation_pattern = re.compile(
        r"^\s*("
        r"#.*\.\.\..*|"  # Python, Ruby, Shell, etc.
        r"\/\/.*\.\.\..*|"  # C-style single line
        r"<!--.*\.\.\..*-->|"  # HTML, XML
        r"\/\*.*\.\.\..*\*\/|"  # C-style multi-line on one line
        r"--.*\.\.\..*"  # SQL, Haskell
        r")",
        re.MULTILINE
    )
    return truncation_pattern.search(code) is not None
    
def _try_parse_comment_header(content: str) -> Optional[Dict[str, str]]:
    """
    Checks if the first line of content is a comment containing a file path.
    Returns a dict with 'file_path' and the remaining 'code' if found.
    """
    lines = content.splitlines()
    if not lines:
        return None

    # A robust regex for various file paths within comments.
    path_pattern = re.compile(
        r"^\s*(?://|#|--|/\*|<!--)\s*(?P<path>[\w./-]+)[\s*/-]*$"
    )
    match = path_pattern.match(lines[0])
    if match:
        found_path = match.group("path")
        if found_path and '.' in found_path:  # Basic sanity check for an extension
            return {"file_path": found_path, "code": "\n".join(lines[1:])}
    return None