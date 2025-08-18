# contextforge/__init__.py
from .context import build_context
from .core import parse_markdown_string, plan_and_generate_changes
from .extract import (
    extract_blocks_from_text,
    extract_diffs_from_text,
    extract_file_blocks_from_text,
    extract_file_info_from_context_and_code,
    detect_new_files,
)
from .commit import patch_text, commit_changes
from .system import append_context, copy_to_clipboard, write_tempfile
from .errors import (
    PatchFailedError,
    ExtractError,
    ContextError,
    CommitError,
    PathViolation,
)

__all__ = [
    "build_context",
    "parse_markdown_string",
    "plan_and_generate_changes",
    "extract_blocks_from_text",
    "extract_diffs_from_text",
    "extract_file_blocks_from_text",
    "extract_file_info_from_context_and_code",
    "detect_new_files",
    "patch_text",
    "commit_changes",
    "append_context",
    "copy_to_clipboard",
    "write_tempfile",
    "PatchFailedError",
    "ExtractError",
    "ContextError",
    "CommitError",
    "PathViolation",
]

