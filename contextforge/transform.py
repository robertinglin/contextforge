import os
import logging
from typing import List, Dict, Any, Callable, Optional, Tuple

try:
    import patch as patch_lib
except ImportError:
    patch_lib = None

from .commit.patch import patch_text, PatchFailedError
from .utils.parsing import _contains_truncation_marker
from .utils.text import cleanup_llm_output

log = logging.getLogger(__name__)

def apply_change_smartly(
    plan: Dict[str, Any],
    codebase_dir: str,
    merge_callback: Optional[Callable[[str, str], str]] = None,
    patch_callback: Optional[Callable[[str, str], str]] = None,
    log_callback: Optional[Callable[[str], None]] = None
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Phase 2: Generates the final file content for a single planned change.
    
    Implements the 'Tiered' strategy:
    - Full Replacement: Check for truncation -> Call merge_callback if needed.
    - Diff: Standard Patch -> Fuzzy Patch -> Call patch_callback if needed.
    
    Returns:
        Tuple(ResultDict, List[str]): The result dict (compatible with Change object) 
                                      and a list of log messages.
    """
    metadata = plan['metadata']
    block = plan['block']
    file_path = metadata.get('file_path')
    change_type = metadata.get('change_type')
    
    local_logs = []
    def _log(msg: str):
        local_logs.append(msg)
        if log_callback:
            log_callback(msg)

    if not file_path:
        _log("  ✘ Missing file_path in plan. Skipping.")
        return None, local_logs

    target_path = os.path.join(codebase_dir, file_path)
    original_content = ""
    
    # Read Original Content
    if os.path.exists(target_path):
        try:
            with open(target_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
        except Exception as e:
            _log(f"  - WARNING: Could not read original file: {e}")
    
    new_content = None
    
    # === Strategy: Full Replacement OR Search/Replace ===
    if change_type == 'full_replacement' or change_type == 'search_replace':
        # Special handling for SEARCH/REPLACE blocks
        if change_type == 'search_replace' or (block.get('is_search_replace')):
            _log("  - Applying SEARCH/REPLACE transformation.")
            try:
                # removed inner import that was causing UnboundLocalError
                old_sr = block.get('old_content', '')
                new_sr = block.get('new_content', '')
                new_content = patch_text(original_content, [{"old": old_sr, "new": new_sr}])
                _log("  ✔ SEARCH/REPLACE successful.")
            except Exception as e:
                _log(f"  ✘ SEARCH/REPLACE failed: {e}")
                new_content = None
        
        elif _contains_truncation_marker(block['code']):
            _log("  - Detected truncation markers.")
            if not original_content:
                _log("  - WARNING: No original file to merge with. Using replacement as-is.")
                new_content = block['code']
            elif merge_callback:
                _log("  - Tier M: invoking merge_callback (Smart Merge).")
                try:
                    raw_merged = merge_callback(original_content, block['code'])
                    new_content = cleanup_llm_output(raw_merged)
                except Exception as e:
                    _log(f"  - Tier M Failed: {e}")
            else:
                 _log("  - Truncation detected but no merge_callback provided. Using as-is.")
                 new_content = block['code']
        else:
            _log("  - Applying full file replacement.")
            new_content = block['code']

    # === Strategy: Diff ===
    elif change_type == 'diff':
        # Tier 1: Standard Patch
        patched = False
        if patch_lib:
            try:
                _log("  - Tier 1: Attempting standard patch...")
                patch_set = patch_lib.fromstring(block['code'].encode('utf-8'))
                if patch_set:
                    result_bytes = patch_set.apply(original_content.encode('utf-8'))
                    if result_bytes is not False:
                        new_content = result_bytes.decode('utf-8')
                        _log("  ✔ Tier 1: Success.")
                        patched = True
            except Exception:
                pass # Fall through to Tier 2
        
        # Tier 2: Fuzzy Patch
        if not patched:
            try:
                _log("  - Tier 2: Attempting fuzzy patch...")
                new_content = patch_text(original_content, block['code'])
                _log("  ✔ Tier 2: Success.")
                patched = True
            except PatchFailedError:
                _log("  - Tier 2 failed.")
        
        # Tier 3: LLM Patch
        if not patched:
            if patch_callback:
                _log("  - Tier 3: Invoking patch_callback (LLM Patch).")
                try:
                    raw_patched = patch_callback(original_content, block['code'])
                    new_content = cleanup_llm_output(raw_patched)
                    patched = True
                except Exception as e:
                    _log(f"  - Tier 3 Failed: {e}")
            else:
                _log("  - Tier 3 unavailable (no callback).")

    else:
        _log(f"  - Unknown change type '{change_type}'. Skipping.")
        return None, local_logs

    if new_content is not None:
        result = {
            "file_path": file_path,
            "new_content": new_content,
            "original_content": original_content,
            "is_new": not os.path.exists(target_path),
            "original_change_type": change_type,
            "block_id": block.get('block_id')
        }
        return result, local_logs

    return None, local_logs