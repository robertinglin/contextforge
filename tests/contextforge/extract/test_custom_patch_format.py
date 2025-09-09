# tests/extract/test_custom_patch_format.py
import textwrap

from contextforge.extract.diffs import extract_diffs_from_text


def test_extract_basic_custom_patch_format():
    content = textwrap.dedent("""
        Some introductory text.

        *** Begin Patch
        *** Update File: backend/app/core/db.py
        @@
        - from .session import SessionLocal
        + from app.core.session import SessionLocal
        *** End Patch

        Some trailing text.
    """)
    blocks = extract_diffs_from_text(content)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["lang"] == "diff"
    assert block["file_path"] == "backend/app/core/db.py"
    assert "from .session import SessionLocal" in block["code"]
    assert "from app.core.session import SessionLocal" in block["code"]
    assert block["open_fence"] is None
    assert block["close_fence"] is None


def test_extract_mixed_custom_and_fenced_patches():
    content = textwrap.dedent("""
        Here is a custom patch first.
        *** Begin Patch
        *** File: path/to/first.py
        @@ -1 +1 @@
        -a
        +b
        *** End Patch

        Now for a standard fenced diff.
        ```diff
        --- a/path/to/second.py
        +++ b/path/to/second.py
        @@ -1 +1 @@
        -x
        +y
        ```
    """)
    blocks = extract_diffs_from_text(content)
    assert len(blocks) == 2

    # Check the custom block
    custom_block = blocks[0]
    assert custom_block["file_path"] == "path/to/first.py"
    assert custom_block["code"].strip() == "@@ -1 +1 @@\n-a\n+b"
    assert custom_block["open_fence"] is None

    # Check the fenced block
    fenced_block = blocks[1]
    assert fenced_block["file_path"] == "path/to/second.py"
    assert "-x" in fenced_block["code"]
    assert fenced_block["open_fence"] is not None


def test_custom_patch_flexible_prefix_and_no_path():
    content = textwrap.dedent("""
        *** Begin Patch
        *** Patched File Is: a/flexible/path.txt
        @@
        -1
        +2
        *** End Patch

        *** Begin Patch
        @@
        - no path here
        *** End Patch
    """)
    blocks = extract_diffs_from_text(content)
    assert len(blocks) == 2

    block_with_path = blocks[0]
    assert block_with_path["file_path"] == "a/flexible/path.txt"
    assert block_with_path["code"].strip() == "-1\n+2"

    block_no_path = blocks[1]
    assert block_no_path["file_path"] == ""
    assert block_no_path["code"].strip() == "- no path here"


def test_extract_unclosed_and_mixed_custom_patches():
    # Test case with one unclosed patch followed by a regular one.
    content1 = textwrap.dedent("""
        Some introductory text.

        *** Begin Patch
        *** Update File: path/to/unclosed.py
        @@
        - unclosed patch content

        *** Begin Patch
        *** File: path/to/closed.py
        - closed patch content
        *** End Patch

        Trailing text.
    """)
    blocks1 = extract_diffs_from_text(content1)
    assert len(blocks1) == 2

    unclosed_block = blocks1[0]
    assert unclosed_block["file_path"] == "path/to/unclosed.py"
    assert unclosed_block["code"].strip() == "- unclosed patch content"
    assert "*** Begin Patch" not in unclosed_block["code"]

    closed_block = blocks1[1]
    assert closed_block["file_path"] == "path/to/closed.py"
    assert closed_block["code"].strip() == "- closed patch content"

    # Test case with an unclosed patch at the end of the file.
    content2 = textwrap.dedent("""
        Another file.

        *** Begin Patch
        *** Update File: contextforge/commit/patch.py
        @@
        -def _find_block_matches(target: list[str], block: list[str], loose: bool = False) -> list[int]:
    """)
    blocks2 = extract_diffs_from_text(content2)
    assert len(blocks2) == 1
    block_user = blocks2[0]
    assert block_user["file_path"] == "contextforge/commit/patch.py"
    assert block_user["code"].strip() == "-def _find_block_matches(target: list[str], block: list[str], loose: bool = False) -> list[int]:"