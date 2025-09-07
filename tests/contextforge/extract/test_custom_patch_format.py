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