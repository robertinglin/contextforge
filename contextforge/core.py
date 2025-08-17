# contextforge/core.py
import os
from typing import Dict, Generator, List, Optional

from .commit import patch_text
from patch import fromstring as patch_fromstring
from .errors import PatchFailedError
from .extract import extract_blocks_from_text
from .extract.metadata import extract_file_info_from_context_and_code
from .utils.parsing import _contains_truncation_marker
import logging

logger = logging.getLogger(__name__)


def parse_markdown_string(markdown_content: str) -> Generator[Dict[str, str], None, None]:
    """
    Parses markdown by delegating to the robust block_extractor.
    """
    blocks = extract_blocks_from_text(markdown_content)
    block_id_counter = 0
    for b in blocks:
        btype = b.get("type")
        context = b.get("context", "")
        if btype == "diff":
            code = b.get("code", "")
            info = extract_file_info_from_context_and_code(context, code, "diff")
            out = {"context": context, "code": code, "lang": "diff", "block_id": block_id_counter}
            if info and info.get("change_type") == "diff":
                out["is_synthetic"] = True
                out["synthetic_info"] = {"file_path": info.get("file_path", "") or "", "change_type": "diff"}
            yield out
            block_id_counter += 1
        elif btype == "file":
            code = b.get("code", "")
            language = b.get("language") or "plain"
            file_path_hint = (b.get("file_path") or "").strip()
            out = {"context": context, "code": code, "lang": language, "block_id": block_id_counter}
            if file_path_hint:
                out["is_pre_classified"] = True
                out["pre_classification"] = {"file_path": file_path_hint, "change_type": "full_replacement"}
            else:
                info = extract_file_info_from_context_and_code(context, code, language)
                if info:
                    out["is_pre_classified"] = True
                    out["pre_classification"] = info
            yield out
            block_id_counter += 1


def plan_and_generate_changes(planned_changes: List[Dict], codebase_dir: str) -> List[Dict]:
    """
    Orchestrates the generation of file content from a list of planned changes.
    This is a non-LLM, non-streaming function that applies patches and prepares content.
    """
    final_changes = []
    for i, plan in enumerate(planned_changes):
        metadata, block = plan['metadata'], plan['block']
        file_path, change_type = metadata['file_path'], metadata['change_type']
        original_content = ""
        target_path = os.path.join(codebase_dir, file_path)
        if os.path.exists(target_path):
            try:
                with open(target_path, 'r', encoding='utf-8') as f:
                    original_content = f.read()
            except Exception as e:
                logger.warning(f"  - WARNING: Could not read original file, proceeding as if empty. Error: {e}")
        new_content = None
        if change_type == 'full_replacement':
            if _contains_truncation_marker(block['code']):
                logger.warning("  - WARNING: Truncation markers detected. LLM-based merging is not part of this function. Treating as a full replacement.")
            new_content = block['code']
        elif change_type == 'diff':
            try:
                patch_set = patch_fromstring(block['code'].encode('utf-8'))
                applied_bytes = patch_set.apply(original_content.encode('utf-8'))
                if applied_bytes is False:
                    raise ValueError("Standard patch library returned False.")
                new_content = applied_bytes.decode('utf-8')
            except Exception:
                try:
                    new_content = patch_text(original_content, block['code'])
                except PatchFailedError as e:
                    logger.error(f"  - ERROR: Fuzzy patch failed for {file_path}: {e}")
                    new_content = None
        else:
            logger.info(f"  - Unknown change type '{change_type}' for {file_path}. Skipping.")
            continue
        if new_content is not None:
            final_changes.append({
                "file_path": file_path, "new_content": new_content,
                "original_content": original_content,
                "is_new": not os.path.exists(target_path),
                "original_change_type": change_type,
                "block_id": block['block_id']
            })
    return final_changes