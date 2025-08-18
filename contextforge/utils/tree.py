# contextforge/utils/tree.py
import os

import pathspec


def _generate_tree_string(path: str, spec: pathspec.PathSpec) -> str:
    """Generates a string representation of the file tree, respecting .gitignore."""
    tree_lines = []

    def build_string_tree(current_path, prefix=""):
        try:
            dirs, files = [], []
            for item in os.listdir(current_path):
                full_path = os.path.join(current_path, item)
                # Normalize to POSIX-style paths for consistent PathSpec matching
                relative_path = os.path.relpath(full_path, path).replace(os.sep, '/')
                # Append a trailing '/' for directories so patterns like 'dir/' match
                probe = "/" + relative_path + ("/" if os.path.isdir(full_path) else "")
                if not spec.match_file(probe):
                    if os.path.isdir(full_path):
                        dirs.append(item)
                    else:
                        files.append(item)

            valid_items = sorted(dirs) + sorted(files)

            for i, item in enumerate(valid_items):
                connector = "└── " if i == len(valid_items) - 1 else "├── "
                tree_lines.append(f"{prefix}{connector}{item}")

                full_item_path = os.path.join(current_path, item)
                if os.path.isdir(full_item_path):
                    new_prefix = prefix + ("    " if i == len(valid_items) - 1 else "│   ")
                    build_string_tree(full_item_path, new_prefix)
        except OSError:
            pass

    build_string_tree(path)
    return "\n".join(tree_lines)
