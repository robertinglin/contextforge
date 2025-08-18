# contextforge/utils/paths.py
import os
import re
from typing import List, Tuple


def _resolve_bare_filename(file_path: str, codebase_dir: str) -> Tuple[str, List[str]]:
    """
    If file_path is a bare filename and not found at the root, search the codebase.
    Returns (resolved_path, log_messages).
    """
    logs = []
    # A bare filename should not contain path separators.
    if (
        file_path
        and not os.path.isabs(file_path)
        and "/" not in file_path
        and "\\" not in file_path
    ):
        # Also check if it looks like a real filename and not just junk.
        if not re.match(r"^[\w.\-]+$", file_path):
            return file_path, logs

        potential_path = os.path.join(codebase_dir, file_path)
        if not os.path.exists(potential_path):
            logs.append(f"  - File '{file_path}' not found at root. Searching codebase...")
            found_paths = []
            for root, dirs, files in os.walk(codebase_dir):
                # Prune the search to avoid descending into .git
                dirs[:] = [d for d in dirs if d != ".git"]
                if file_path in files:
                    full_path = os.path.join(root, file_path)
                    # Normalize to forward slashes for consistency.
                    relative_path = os.path.relpath(full_path, codebase_dir).replace(os.sep, "/")
                    found_paths.append(relative_path)

            if len(found_paths) == 1:
                new_path = found_paths[0]
                logs.append(f"  - Found unique match: '{new_path}'. Updating path.")
                return new_path, logs
            elif len(found_paths) > 1:
                logs.append(
                    f"  - WARNING: Found multiple files for '{file_path}': {found_paths}. Using original path due to ambiguity."
                )
    return file_path, logs
