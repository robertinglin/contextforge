# contextforge/extract/main.py
from typing import Any, Dict, List

from .diffs import extract_diffs_from_text
from .files import extract_file_blocks_from_text


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
    diffs = extract_diffs_from_text(
        markdown_content,
        allow_bare_fences_that_look_like_diff=False,
        split_per_file=True,
    )

    # Normalize diff blocks
    norm_diffs: List[Dict[str, Any]] = []
    for d in diffs:
        norm_diffs.append({
            "type": "diff",
            "language": "diff",
            "start": d.get("start", 0),
            "end": d.get("end", 0),
            "code": d.get("code", ""),
            "file_path": d.get("file_path") or None,
            "context": d.get("context"),
        })

    files = extract_file_blocks_from_text(markdown_content)

    # Normalize file blocks (reconstruct code if only offsets are provided)
    norm_files: List[Dict[str, Any]] = []
    for fb in files:
        body_start = fb.get("start") or fb.get("body_start") or 0
        body_end = fb.get("end") or fb.get("body_end") or body_start
        code = fb.get("code")
        if code is None:
            code = markdown_content[body_start:body_end].strip("\r\n")
        norm_files.append({
            "type": "file",
            "language": fb.get("language", "plain"),
            "start": body_start,
            "end": body_end,
            "code": code,
            "file_path": fb.get("file_path") or None,
        })

    # Stable, deterministic ordering
    return sorted(norm_diffs + norm_files, key=lambda b: b["start"])
