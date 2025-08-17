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
            full_path = os.path.join(base_real_path, *file_path.split('/'))

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

            with open(resolved_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            lang = os.path.splitext(file_path)[1].lstrip('.')
            context_str += f"File: {file_path}\n```{lang}\n{content}\n```\n\n"
        except (IOError, OSError) as e:
            raise ContextError(f"Failed to read file '{file_path}': {e}") from e

    context_str += "</file_contents>\n\n"
    if request.instructions:
        context_str += f"<user_instructions>\n{request.instructions}\n</user_instructions>\n"

    context_str += (
        '\n<format_instruction>\n'
        'When providing code changes, please adhere to the following rules:\n'
        '1.  **Use Git Diffs:** For modifications, always provide changes in the standard git diff format. Start the code block with ```diff.\n'
        '2.  **Handle New/Full Files:** If it is a full replacement, state that it\'s a replacement and the file path right before the code block. If it\'s a new file, mention it\'s new and provide the file path.\n'
        '3.  **Use Markdown:** All code and diff blocks must be enclosed in triple backticks (```). Specify the language (e.g., `python`, `diff`) where applicable.\n'
        '4.  **No XML:** Do not use XML tags in your response.\n'
        '\n'
        '**Example of a Git Diff:**\n'
        '```diff\n'
        '--- a/src/main.js\n'
        '+++ b/src/main.js\n'
        '@@ -1,5 +1,5 @@\n'
        ' function oldFunction() {\n'
        '-    console.log("old");\n'
        '+    console.log("new");\n'
        ' }\n'
        '```\n'
        '</format_instruction>\n'
    )

    return context_str