# contextforge/__init__.py
from .context import build_context
from .extract import (
    extract_blocks_from_text,
    extract_diffs_from_text,
    extract_file_blocks_from_text,
)
from .commit import patch_text, commit_changes
from .system import append_context, copy_to_clipboard, write_tempfile
from .errors import PatchFailedError

__all__ = [
    "build_context",
    "extract_blocks_from_text",
    "extract_diffs_from_text",
    "extract_file_blocks_from_text",
    "patch_text",
    "commit_changes",
    "append_context",
    "copy_to_clipboard",
    "write_tempfile",
    "PatchFailedError",
]

