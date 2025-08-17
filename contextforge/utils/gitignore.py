# contextforge/utils/gitignore.py
import os
import pathspec

def get_gitignore(path: str) -> pathspec.PathSpec:
    """
    Finds and parses the .gitignore file by searching from the given path upwards.

    This function mimics Git's behavior by starting at `path` and walking up the
    directory tree until it finds a `.gitignore` file. It uses the first one it
    encounters and does not merge ignore rules from other `.gitignore` files in
    parent directories.

    It also includes a default rule to always ignore the '.git/' directory and
    'package-lock.json'.

    Args:
        path: The starting directory path for the search.

    Returns:
        A `pathspec.PathSpec` object compiled from the found .gitignore rules.
    """
    default_ignores = ['.git/', 'package-lock.json']
    ignore_lines = default_ignores

    current_path = os.path.abspath(path)
    while True:
        gitignore_path = os.path.join(current_path, '.gitignore')
        if os.path.exists(gitignore_path):
            with open(gitignore_path, 'r') as f:
                ignore_lines.extend(f.readlines())
            break

        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            break
        current_path = parent_path

    return pathspec.PathSpec.from_lines('gitwildmatch', ignore_lines)