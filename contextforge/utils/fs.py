import os
from typing import List, Tuple

def resolve_filename(file_path: str, root_dir: str) -> Tuple[str, List[str]]:
    """
    Resolves a file path relative to a root directory.
    If the path is a bare filename and not found at the root, it searches
    the directory tree for a unique match.
    
    Returns:
        Tuple[str, List[str]]: (resolved_path, log_messages)
    """
    logs = []
    if not file_path:
        return file_path, logs

    # If it looks like a path (has separators) or is absolute, assume it's correct/relative.
    if os.path.isabs(file_path) or '/' in file_path or '\\' in file_path:
        return file_path, logs

    # If it's a bare filename, check existence at root first.
    potential_path = os.path.join(root_dir, file_path)
    if os.path.exists(potential_path):
        return file_path, logs

    # It's a bare filename that doesn't exist at root. Search the codebase.
    logs.append(f"  - File '{file_path}' not found at root. Searching codebase...")
    found_paths = []
    
    for root, dirs, files in os.walk(root_dir):
        # Optimization: Don't descend into .git
        if '.git' in dirs:
            dirs.remove('.git')
            
        if file_path in files:
            full_path = os.path.join(root, file_path)
            # Return path relative to the codebase root
            rel_path = os.path.relpath(full_path, root_dir).replace(os.sep, '/')
            found_paths.append(rel_path)

    if len(found_paths) == 1:
        new_path = found_paths[0]
        logs.append(f"  - Found unique match: '{new_path}'. Updating path.")
        return new_path, logs
    elif len(found_paths) > 1:
        logs.append(f"  - WARNING: Found multiple files for '{file_path}': {found_paths}. Using original path due to ambiguity.")
    
    return file_path, logs