# contextforge/utils/gitignore.py
import os
import pathspec
from typing import List

def get_gitignore(path: str) -> pathspec.PathSpec:
    """
    Return a PathSpec compiled from the nearest .gitignore found by walking
    upward from `path` (file or directory). Always ignores '.git/' by default.
    If no .gitignore exists or it cannot be read, still return a valid spec
    that at least ignores '.git/'.
    """
    defaults: List[str] = ['.git/']
    lines: List[str] = list(defaults)

    # Normalize base (allow either a file or a directory)
    base = os.path.abspath(path or ".")
    if os.path.isfile(base):
        base = os.path.dirname(base)

    cur = base
    while True:
        gi = os.path.join(cur, ".gitignore")
        try:
            if os.path.exists(gi):
                with open(gi, "r", encoding="utf-8", errors="ignore") as f:
                    # Splitlines to avoid carrying raw newline characters
                    lines.extend(f.read().splitlines())
                break
        except OSError:
            # Ignore unreadable .gitignore and keep walking upward
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # Always return a valid spec; fall back to defaults only if necessary.
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except Exception:
        return pathspec.PathSpec.from_lines("gitwildmatch", defaults)