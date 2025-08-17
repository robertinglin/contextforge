# contextforge/__init__.py
from .core import parse_markdown_string, plan_and_generate_changes
from .errors import PatchFailedError
from .commit import patch_text
from .extract import extract_blocks_from_text
from .context import build_context

__all__ = [
    "plan_and_generate_changes",
    "parse_markdown_string",
    "PatchFailedError",
    "patch_text",
    "extract_blocks_from_text",
    "build_context",
]