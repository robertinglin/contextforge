# contextforge/extract/main.py
from typing import List

from .diffs import extract_diffs_from_text
from .files import extract_file_blocks_from_text
from ..models import FileBlock, DiffBlock, Block


def extract_blocks_from_text(markdown_content: str) -> List[Block]:
    """
    Unified extractor: diffs + file blocks, returned as dataclass instances.
    - Diff blocks come ONLY from explicit ```diff/
    """
    diffs = extract_diffs_from_text(
        markdown_content,
        allow_bare_fences_that_look_like_diff=False,  # IMPORTANT for stable ordering
        split_per_file=True,
    )
    for d in diffs:
        d["type"] = "diff"

    files = extract_file_blocks_from_text(markdown_content)

    # Merge by textual order
    all_blocks = sorted(diffs + files, key=lambda b: b.get("start", 0))
    return all_blocks