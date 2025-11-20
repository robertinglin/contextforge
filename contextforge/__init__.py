from .commit import commit_changes, patch_text, fuzzy_patch_partial
from .context import build_context
from .core import parse_markdown_string, plan_and_generate_changes
from .plan import plan_changes
from .transform import apply_change_smartly
from .utils.fs import resolve_filename
from .utils.text import cleanup_llm_output
from .errors import (
    CommitError,
    ContextError,
    ExtractError,
    PatchFailedError,
    PathViolation,
)
from .extract import (
    detect_new_files,
    extract_blocks_from_text,
    extract_diffs_from_text,
    extract_file_info_from_context_and_code,
)
from .system import append_context, copy_to_clipboard, write_tempfile

__all__ = [
    "build_context",
    "parse_markdown_string",
    "plan_and_generate_changes",
    "plan_changes",             
    "apply_change_smartly",     
    "resolve_filename",         
    "cleanup_llm_output",       
    "extract_blocks_from_text",
    "extract_diffs_from_text",
    "extract_file_info_from_context_and_code",
    "parse_markdown_string",
    "detect_new_files",
    "fuzzy_patch_partial",
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
