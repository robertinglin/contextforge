import pprint
import re
from typing import Any, Dict, List, Optional

from .diffs import _looks_like_diff, _extract_custom_patch_blocks, _split_multi_file_diff
from .extract import extract_all_blocks_from_text
from .metadata import (
    detect_deletion_from_diff,
    detect_rename_from_diff,
    extract_file_info_from_context_and_code,
)
from ..utils.parsing import _try_parse_comment_header


def _extract_search_replace_blocks(text: str) -> List[Dict[str, Any]]:
    """
    Extract SEARCH/REPLACE blocks from markdown-style fenced code.

    Supports two formats:

    1. Single block per fence:
        ```language
        <<<<<<< SEARCH
        old content
        =======
        new content
        >>>>>>> REPLACE
        ```

    2. Multiple blocks per fence (same file):
        path/to/file.ext
        ```language
        <<<<<<< SEARCH
        old content 1
        =======
        new content 1
        >>>>>>> REPLACE
        <<<<<<< SEARCH
        old content 2
        =======
        new content 2
        >>>>>>> REPLACE
        ```

    Returns list of dicts with: file_path, old, new, language, start, end
    """
    results = []

    # Pattern to find fenced code blocks (standard markdown fence)
    fence_pattern = re.compile(
        r"```(\w*)\s*\n"  # Opening fence with optional language (group 1)
        r"(.*?)"  # Content inside fence (group 2)
        r"\n```(?:\s*$|\n|$)",  # Closing fence
        re.DOTALL,
    )

    # Pattern to extract individual SEARCH/REPLACE pairs from fence content
    # Note: new_content can be empty (for deletions), so we make the content optional
    sr_pattern = re.compile(
        r"<<<<<<< SEARCH\s*\n"  # SEARCH marker
        r"(.*?)"  # Old content - non-greedy (group 1)
        r"\n=======[ \t]*\n"  # Separator with required newline after
        r"(.*?)"  # New content - can be empty (group 2)
        r"(?:\n)?>>>>>>> REPLACE",  # REPLACE marker (newline optional before if new_content is empty)
        re.DOTALL,
    )

    for fence_match in fence_pattern.finditer(text):
        fence_content = fence_match.group(2)

        # Check if this fence contains any SEARCH/REPLACE blocks
        sr_matches = list(sr_pattern.finditer(fence_content))
        if not sr_matches:
            continue

        fence_start, fence_end = fence_match.span()
        language = fence_match.group(1) or "plain"

        # Extract file path from context before the fence
        file_path = None
        context_before = text[:fence_start]
        lines_before = context_before.split("\n")

        # Check last few lines for a file path hint
        for line in reversed(lines_before[-5:]):
            line = line.strip()
            if line and not line.startswith("```"):
                # First try to match a standalone file path (whole line is a path)
                path_match = re.match(r"^([\w\-./\\]+\.\w+)$", line)
                if path_match:
                    file_path = path_match.group(1).replace("\\", "/")
                    break
                # Then try to extract a file path from the line
                path_match = re.search(r"([\w\-./\\]+\.\w+)", line)
                if path_match:
                    file_path = path_match.group(1).replace("\\", "/")
                    break

        # Extract each SEARCH/REPLACE pair from this fence
        for sr_match in sr_matches:
            old_content = sr_match.group(1)
            new_content = sr_match.group(2) or ""  # May be None for deletions

            results.append(
                {
                    "file_path": file_path,
                    "old": old_content,
                    "new": new_content,
                    "language": language,
                    "start": fence_start,
                    "end": fence_end,
                }
            )

    return results


def _extract_chevron_blocks(text: str) -> List[Dict[str, Any]]:
    """
    Extract SEARCH/REPLACE blocks using chevron syntax (<<<<, ====, >>>>).

    Supports format:
        ```language
        // path/to/file.ext

        // optional comment
        <<<<
        old content
        ====
        new content
        >>>>
        ```

    Returns list of dicts with: file_path, old, new, language, start, end
    """
    results = []

    # Pattern to find fenced code blocks (standard markdown fence)
    fence_pattern = re.compile(
        r"```(\w*)\s*\n"  # Opening fence with optional language (group 1)
        r"(.*?)"  # Content inside fence (group 2)
        r"\n```(?:\s*$|\n|$)",  # Closing fence
        re.DOTALL,
    )

    # Pattern to extract individual chevron pairs from fence content
    # Note: new_content can be empty (for deletions), so we make the content optional
    chevron_pattern = re.compile(
        r"<<<<\s*\n"  # Opening marker
        r"(.*?)"  # Old content - non-greedy (group 1)
        r"\n====[ \t]*\n"  # Separator with required newline after
        r"(.*?)"  # New content - can be empty (group 2)
        r"(?:\n)?>>>>",  # Closing marker (newline optional before if new_content is empty)
        re.DOTALL,
    )

    for fence_match in fence_pattern.finditer(text):
        fence_content = fence_match.group(2)

        # Check if this fence contains any chevron blocks
        chevron_matches = list(chevron_pattern.finditer(fence_content))
        if not chevron_matches:
            continue

        fence_start, fence_end = fence_match.span()
        language = fence_match.group(1) or "plain"

        # Extract file path from comment at the top of the fence content
        # Look for patterns like "// path/to/file.ext" or "# path/to/file.ext"
        file_path = None
        path_match = re.match(r"^\s*(?://|#)\s*(\S+\.\w+)", fence_content)
        if path_match:
            file_path = path_match.group(1).replace("\\", "/")

        # Extract each chevron pair from this fence
        for chevron_match in chevron_matches:
            old_content = chevron_match.group(1)
            new_content = chevron_match.group(2) or ""  # May be None for deletions

            results.append(
                {
                    "file_path": file_path,
                    "old": old_content,
                    "new": new_content,
                    "language": language,
                    "start": fence_start,
                    "end": fence_end,
                }
            )

    return results


def extract_blocks_from_text(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Unified extractor returning a list of dictionaries ordered by their
    occurrence in the input (v0.1.0 schema).

    Common keys (both types):
      - type: "diff" | "file"
      - start: int  (start offset in source text)
      - end:   int  (end offset in source text)
      - code:  str  (the extracted body without the fences)

    Diff blocks (type == "diff") add:
      - language: "diff"
      - file_path: Optional[str]  (best-effort from headers)
      - context:   Optional[str]  (lines before opener, if available)

    File blocks (type == "file") add:
      - language: str  (e.g., "python", "md", "plain")
      - file_path: Optional[str]  (best-effort from nearby text)

    Notes:
      - Only explicit ```diff fences are treated as diffs (for stable ordering).
      - Adjacent and nested fences are handled by the underlying extractors.
    """
    # Step 0: Extract SEARCH/REPLACE blocks (they take priority)
    search_replace_blocks = _extract_search_replace_blocks(markdown_content)

    # Step 0b: Extract chevron-style blocks (<<<<, ====, >>>>)
    chevron_blocks = _extract_chevron_blocks(markdown_content)

    # Step 1: Extract all fenced code blocks (generic)
    all_blocks = extract_all_blocks_from_text(markdown_content)

    # Step 2: Extract custom patch blocks (*** Begin Patch / *** End Patch)
    custom_patch_blocks = _extract_custom_patch_blocks(markdown_content)

    # Filter generic blocks to exclude those that overlap with priority blocks
    priority_blocks = search_replace_blocks + chevron_blocks + custom_patch_blocks
    consumed_ranges = [(b["start"], b["end"]) for b in priority_blocks]

    filtered_all_blocks = []
    for blk in all_blocks:
        b_start, b_end = blk["start"], blk["end"]
        is_consumed = False
        for r_start, r_end in consumed_ranges:
            # Check for significant overlap/containment
            if max(b_start, r_start) < min(b_end, r_end):
                is_consumed = True
                break
        if not is_consumed:
            filtered_all_blocks.append(blk)

    # Step 3: Process and classify each block
    results: List[Dict[str, Any]] = []

    # Process SEARCH/REPLACE blocks first (convert to file type with special marker)
    for sr_block in search_replace_blocks:
        results.append(
            {
                "type": "file",  # Treat as file replacement
                "language": sr_block["language"],
                "start": sr_block["start"],
                "end": sr_block["end"],
                "code": "",  # Code will be generated via patch
                "file_path": sr_block.get("file_path"),
                "is_search_replace": True,  # Special marker
                "old_content": sr_block["old"],
                "new_content": sr_block["new"],
            }
        )

    # Process chevron blocks (convert to file type with special marker)
    for chevron_block in chevron_blocks:
        results.append(
            {
                "type": "file",  # Treat as file replacement
                "language": chevron_block["language"],
                "start": chevron_block["start"],
                "end": chevron_block["end"],
                "code": "",  # Code will be generated via patch
                "file_path": chevron_block.get("file_path"),
                "is_search_replace": True,  # Special marker (same as SEARCH/REPLACE)
                "old_content": chevron_block["old"],
                "new_content": chevron_block["new"],
            }
        )

    # Process custom patch blocks first
    for blk in custom_patch_blocks:
        results.append(
            {
                "type": "diff",
                "language": "diff",
                "start": blk.get("start", 0),
                "end": blk.get("end", 0),
                "code": blk.get("code", ""),
                "file_path": blk.get("file_path") or None,
                "context": blk.get("context"),
            }
        )

    # Process regular fenced blocks (filtered)
    for blk in filtered_all_blocks:
        language = blk.get("language", "plain")
        code = blk.get("code", "")
        context = blk.get("context", "")
        file_path = blk.get("file_path")

        # High-priority checks based on diff content for rename/delete
        rename_info = detect_rename_from_diff(code)
        if rename_info:
            results.append(
                {
                    "type": "rename",
                    "from_path": rename_info["from_path"],
                    "to_path": rename_info["to_path"],
                    "start": blk.get("start", 0),
                    "end": blk.get("end", 0),
                    "code": code,  # Keep the diff for logging/inspection
                }
            )
            continue

        deleted_path = detect_deletion_from_diff(code)
        if deleted_path:
            results.append(
                {
                    "type": "delete",
                    "file_path": deleted_path,
                    "start": blk.get("start", 0),
                    "end": blk.get("end", 0),
                }
            )
            continue

        # Generic classification: is it a diff or a file?
        is_diff = False
        diff_file_path = None

        # Explicit diff/patch language tag
        if language in ("diff", "patch"):
            is_diff = True
            # Try to extract file path from diff headers if not already hinted
            if not file_path:
                path_match = re.search(r"^\+\+\+ b/(\S+)", code, re.MULTILINE)
                if path_match:
                    diff_file_path = path_match.group(1).strip().split("\t")[0].replace("\\", "/")
        # Check if content looks like a diff even without explicit tag
        elif _looks_like_diff(code):
            # Use extract_file_info_from_context_and_code to determine if it's a diff
            info = extract_file_info_from_context_and_code(context, code, language)
            if info and info.get("change_type") == "diff":
                is_diff = True
                diff_file_path = info.get("file_path")

        if is_diff:
            # Split multi-file diffs if needed
            file_chunks = _split_multi_file_diff(code)
            if not file_chunks:
                file_chunks = [(diff_file_path or file_path or "", code)]

            for chunk_path, chunk_text in file_chunks:
                if not chunk_text.strip():
                    continue
                results.append(
                    {
                        "type": "diff",
                        "language": "diff",
                        "start": blk.get("start", 0),
                        "end": blk.get("end", 0),
                        "code": chunk_text.strip("\n"),
                        "file_path": chunk_path or diff_file_path or file_path or None,
                        "context": context,
                    }
                )
        else:
            # It's a regular file block. Check for commented path.
            final_code = code
            final_file_path = file_path

            comment_header = _try_parse_comment_header(code)
            if comment_header:
                final_file_path = comment_header["file_path"]
                final_code = comment_header["code"]

            results.append(
                {
                    "type": "file",
                    "language": language,
                    "is_pre_classified": False,
                    "pre_classification": None,
                    "start": blk.get("start", 0),
                    "end": blk.get("end", 0),
                    "code": final_code,
                    "file_path": final_file_path or None,
                }
            )

    # If we didn't find any fenced blocks but the whole text looks like a diff,
    # emit a single diff block (raw diff fallback).
    if not results and _looks_like_diff(markdown_content):
        results.append(
            {
                "type": "diff",
                "language": "diff",
                "start": 0,
                "end": len(markdown_content),
                "code": markdown_content,
                "file_path": None,
                "context": None,
            }
        )

    # If the same file path is provided multiple times, use the last one based on start position.
    # This handles cases where a model refines its answer in a single response.
    # We deduplicate blocks by (file_path, type) tuple. This ensures that:
    # 1. Two file blocks for 'a.py' -> last one wins (correction).
    # 2. Two diff blocks for 'a.py' -> last one wins (correction).
    # 3. One file block 'a.py' and one diff block 'a.py' -> both kept (mixed operations).
    # NOTE: We do NOT deduplicate is_search_replace blocks - they represent intentional
    # multiple atomic changes to the same file.

    latest_blocks_by_key = {}
    other_blocks = []
    for block in results:
        fp = block.get("file_path")
        b_type = block["type"]

        # Skip deduplication for search/replace blocks - they are intentional multiple changes
        if block.get("is_search_replace"):
            other_blocks.append(block)
        # Only deduplicate if we have a file path and it is a file or diff block
        elif fp and b_type in ("file", "diff"):
            key = (fp, b_type)
            if (
                key not in latest_blocks_by_key
                or block["start"] > latest_blocks_by_key[key]["start"]
            ):
                latest_blocks_by_key[key] = block
        else:
            other_blocks.append(block)

    # Combine the deduplicated file blocks with others
    deduped = other_blocks + list(latest_blocks_by_key.values())

    # Sort by start position for stable ordering
    return sorted(deduped, key=lambda b: b["start"])