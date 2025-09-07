# contextforge/core.py
import logging
import os
from typing import Dict, Generator, List

from patch import fromstring as patch_fromstring

from .commit import Change, patch_text
from .errors import PatchFailedError
from .extract import extract_blocks_from_text
from .extract.metadata import extract_file_info_from_context_and_code
from .utils.parsing import _contains_truncation_marker

logger = logging.getLogger(__name__)


def parse_markdown_string(markdown_content: str) -> Generator[Dict[str, str], None, None]:
    """
    Parses markdown by delegating to the robust block_extractor.
    """
    blocks = extract_blocks_from_text(markdown_content)
    for i, b in enumerate(blocks):
        b["block_id"] = i
        yield b


def plan_and_generate_changes(planned_changes: List[Dict], codebase_dir: str) -> List[Change]:
    """
    Orchestrates the generation of file content from a list of planned changes.
    This is a non-LLM, non-streaming function that applies patches and prepares content.
    Returns a list of Change objects for the commit function.
    """
    final_changes: List[Change] = []
    for _i, plan in enumerate(planned_changes):
        # Be flexible: the plan might be the block itself or nested.
        metadata = plan.get("metadata", plan)
        block = plan.get("block", plan)
        change_type = metadata.get("type")

        # Handle rename and delete operations which don't involve content patching
        if change_type == "rename":
            final_changes.append(
                Change(action="rename", path=metadata["to_path"], from_path=metadata["from_path"])
            )
            continue
        if change_type == "delete":
            original_content = ""
            target_path = os.path.join(codebase_dir, metadata["file_path"])
            if os.path.exists(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    original_content = f.read()
            final_changes.append(Change(action="delete", path=metadata["file_path"], original_content=original_content))
            continue

        file_path = metadata.get("file_path")
        if not file_path:
            logger.warning("  - WARNING: Change plan is missing 'file_path'. Skipping.")
            continue

        original_content = ""
        target_path = os.path.join(codebase_dir, file_path)
        if os.path.exists(target_path):
            try:
                with open(target_path, encoding="utf-8") as f:
                    original_content = f.read()
            except Exception as e:
                logger.warning(
                    f"  - WARNING: Could not read original file, proceeding as if empty. Error: {e}"
                )
        new_content = None

        if change_type == "file":
            if _contains_truncation_marker(block["code"]):
                logger.warning(
                    "  - WARNING: Truncation markers detected. LLM-based merging is not part of this function. Treating as a full replacement."
                )
            new_content = block["code"]
        elif change_type == "diff":
            try:
                patch_set = patch_fromstring(block["code"].encode("utf-8"))
                applied_bytes = patch_set.apply(original_content.encode("utf-8"))
                if applied_bytes is False:
                    raise ValueError("Standard patch library returned False.")
                new_content = applied_bytes.decode("utf-8")
            except Exception:
                try:
                    new_content = patch_text(original_content, block["code"])
                except PatchFailedError as e:
                    logger.error(f"  - ERROR: Fuzzy patch failed for {file_path}: {e}")
                    new_content = None
        else:
            logger.info(f"  - Unknown change type '{change_type}' for {file_path}. Skipping.")
            continue
        if new_content is not None:
            is_new = not os.path.exists(target_path)
            final_changes.append(
                Change(
                    action="create" if is_new else "modify",
                    path=file_path,
                    new_content=new_content,
                    original_content=original_content,
                )
            )
    return final_changes
