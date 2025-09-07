# contextforge/context/builder.py
import os
from typing import Any

from ..errors import ContextError
from ..utils.gitignore import get_gitignore
from ..utils.tree import _generate_tree_string


def _build_context_string(request: Any) -> str:
    """Helper function to construct the full context string."""
    context_str = "SYSTEM INSTRUCTIONS: read ENTIRE content and follow <user_instructions> at end"

    if request.include_file_tree:
        base_path = request.base_path
        if os.path.isdir(base_path):
            spec = get_gitignore(base_path)
            tree_string = _generate_tree_string(base_path, spec)
            context_str += "<file_tree>\n" + tree_string + "\n</file_tree>\n\n"

    context_str += "<file_contents>\n"
    # Resolve the real, absolute path of the base directory to prevent path traversal.
    base_real_path = os.path.realpath(request.base_path)

    for file_path in request.files:
        try:
            # Build absolute path and enforce that it stays within the project directory.
            full_path = os.path.join(base_real_path, *file_path.split("/"))

            # SECURITY CHECK: Ensure the resolved path is within the base project directory.
            resolved_path = os.path.realpath(full_path)
            common_path = os.path.commonpath([base_real_path, resolved_path])

            if common_path != base_real_path:
                # Security violation: do not read the file; record an explicit error message instead.
                context_str += (
                    f"File: {file_path}\n"
                    "---\n"
                    "Error: Security violation - file path is outside the project directory.\n"
                    "---\n\n"
                )
                continue

            with open(resolved_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lang = os.path.splitext(file_path)[1].lstrip(".")
            context_str += f"File: {file_path}\n```{lang}\n{content}\n```\n\n"
        except OSError as e:
            raise ContextError(f"Failed to read file '{file_path}': {e}") from e

    context_str += "</file_contents>\n\n"
    if request.instructions:
        context_str += f"<user_instructions>\n{request.instructions}\n</user_instructions>\n"

    # Stable, copy-pasteable formatting guidance for downstream tools.
    context_str += (
        "\n<format_instruction>\n"
        "Output code changes like this:\n"
        "1) Use standard Git unified diffs for all changes, including renames and deletions.\n"
        "2) For new files, you can use a diff against /dev/null or provide a full code block with a commented path on the first line.\n"
        "3) Wrap all code/diffs in triple backticks (```diff or ```<language>).\n"
        "4) Do not use XML-like tags in responses.\n"
        "\n"
        "Example (new file with comment):\n"
        "```python\n"
        "# src/new_module.py\n"
        "def new_function():\n"
        "    pass\n"
        "```\n"
        "\n"
        "Example (git diff):\n"
        "```diff\n"
        "--- a/src/main.js\n"
        "+++ b/src/main.js\n"
        "@@ -1,3 +1,3 @@\n"
        "-console.log('old')\n"
        "+console.log('new')\n"
        "```\n"
        "</format_instruction>\n"
    )

    return context_str
