import os
import logging
from typing import List, Dict, Any, Callable, Optional

from .extract.metadata import extract_file_info_from_context_and_code
from .utils.fs import resolve_filename

log = logging.getLogger(__name__)

def plan_changes(
    change_blocks: List[Dict[str, Any]],
    codebase_dir: str,
    classifier_callback: Optional[Callable[[str, str], Dict[str, str]]] = None,
    log_callback: Optional[Callable[[str], None]] = None
) -> List[Dict[str, Any]]:
    """
    Phase 1: Analyzes extracted blocks to determine file paths and change types.
    
    Args:
        change_blocks: List of blocks from parse_markdown_string/extract_blocks.
        codebase_dir: Root directory of the project.
        classifier_callback: Function(context, code) -> {'file_path': str, 'change_type': str}.
                             Used when deterministic classification fails.
        log_callback: Optional function to receive user-facing log strings.
    
    Returns:
        List of dicts containing {'metadata': ..., 'block': ...}.
    """
    planned_changes = []
    
    def _log(msg: str):
        if log_callback:
            log_callback(msg)
        log.debug(msg)

    for i, block in enumerate(change_blocks):
        metadata = None
        # 0. Check for SEARCH/REPLACE blocks (highest priority)
        # Note: extract_blocks_from_text sets type='file' but adds is_search_replace=True
        if block.get("is_search_replace"):
            metadata = {
                "file_path": block.get("file_path"),
                "change_type": "search_replace"
            }
            _log(f"  Recognized SEARCH/REPLACE block for '{metadata.get('file_path', 'N/A')}'")
        
        # 1. Check for Synthetic Blocks (from multi-file diffs)
        elif block.get("is_synthetic"):
            metadata = block["synthetic_info"]
            _log(f"  Recognized pre-classified block for '{metadata.get('file_path', 'N/A')}'")

        # 2. Check for Deterministic Pre-classification
        elif block.get("is_pre_classified"):
            metadata = block["pre_classification"]
            _log(f"  Using deterministic classification for '{metadata.get('file_path', 'N/A')}'")

        # 3. Fallback: LLM Classification
        else:
            if classifier_callback:
                _log(f"  Using classifier callback for block #{block.get('block_id', i)}...")
                try:
                    metadata = classifier_callback(block.get("context", ""), block.get("code", ""))
                except Exception as e:
                    _log(f"  - Classifier callback failed: {e}")
            
            if not metadata:
                _log("  - Could not extract metadata. Skipping block.")
                continue

        # 4. Resolve Filenames and Refine Change Type
        original_path = metadata.get('file_path')
        if original_path:
            resolved_path, path_logs = resolve_filename(original_path, codebase_dir)
            for msg in path_logs:
                _log(msg)
            metadata['file_path'] = resolved_path
            
            # If file doesn't exist, force 'full_replacement' (creation)
            target_path = os.path.join(codebase_dir, resolved_path)
            if not os.path.exists(target_path):
                if metadata.get('change_type') != 'full_replacement':
                    _log(f"  - File does not exist. Forcing 'full_replacement' for new file: {resolved_path}")
                    metadata['change_type'] = 'full_replacement'
            else:
                _log(f"  - File exists. Type: '{metadata.get('change_type')}'")

        planned_changes.append({"metadata": metadata, "block": block})
        _log(f"  ✔ Classified as '{metadata.get('change_type')}' for '{metadata.get('file_path')}'")

    return planned_changes