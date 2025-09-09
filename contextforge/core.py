# contextforge/core.py
import logging
import os
from typing import Dict, Generator, List, Any

from patch import fromstring as patch_fromstring

from .commit import Change, patch_text
from .errors import PatchFailedError
from .extract import extract_blocks_from_text
from .extract.metadata import extract_file_info_from_context_and_code
from .utils.parsing import _contains_truncation_marker

logger = logging.getLogger(__name__)


def parse_markdown_string(markdown_content: str) -> Generator[Dict[str, Any], None, None]:
    """
    Parses markdown by delegating to the robust block extractor and enriches each
    block with convenient metadata used by downstream planning code.

    Behavior expected by tests:
      - Only yield blocks of type "diff" or "file". Ignore others.
      - Preserve the original positional index as `block_id`.
      - Normalize language as `lang` (diff => "diff"; otherwise use provided language).
      - Mark synthetic diffs when metadata extraction suggests a 'diff' change.
      - Pre-classify file blocks either from an explicit `file_path` hint or from
        context-derived extraction.
    """
    blocks = extract_blocks_from_text(markdown_content)
    for i, raw in enumerate(blocks):
        b = dict(raw)  # shallow copy so we don't mutate upstream data
        b["block_id"] = i

        btype = b.get("type")
        # Only process file/diff blocks; ignore others entirely.
        if btype not in ("diff", "file"):
            continue

        # Normalize language for convenience in tests/downstream consumers.
        if btype == "diff":
            b["lang"] = "diff"
        else:
            if b.get("language"):
                b["lang"] = b["language"]

        # Enrichment / classification
        context_str = b.get("context", "")
        code_str = b.get("code", "")

        if btype == "diff":
            # Attempt to extract file info; if it classifies as a diff, treat as synthetic.
            try:
                info = extract_file_info_from_context_and_code(context_str, code_str)
            except Exception:
                info = None
            if info and info.get("change_type") == "diff":
                b["is_synthetic"] = True
                b["synthetic_info"] = info

        elif btype == "file":
            # If there's a direct hint, classify as a full replacement without calling the extractor.
            if b.get("file_path"):
                b["is_pre_classified"] = True
                b["pre_classification"] = {
                    "file_path": b["file_path"],
                    "change_type": "full_replacement",
                }
            else:
                # Fall back to extracting from context/code.
                try:
                    info = extract_file_info_from_context_and_code(context_str, code_str)
                except Exception:
                    info = None
                if info:
                    b["is_pre_classified"] = True
                    b["pre_classification"] = info

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
        # Tests pass change_type under 'change_type' (not 'type')
        change_type = metadata.get("change_type")

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

        # Treat 'full_replacement' as an alias for a plain file replacement.
        if change_type in ("file", "full_replacement"):
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
