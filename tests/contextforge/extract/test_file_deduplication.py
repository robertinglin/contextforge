# tests/contextforge/extract/test_file_deduplication.py
import textwrap

from contextforge.extract import extract_blocks_from_text


def test_uses_last_full_file_block_for_same_path():
    """
    When a model outputs the same full file twice, only the last
    version should be returned.
    """
    markdown_content = textwrap.dedent("""
        Here is the first version of the file.

        File: src/app.js
        ```javascript
        console.log("old version");
        ```

        Oh wait, I made a mistake. Here is the corrected version.

        File: src/app.js
        ```javascript
        console.log("new and improved version");
        ```
    """)

    blocks = extract_blocks_from_text(markdown_content)

    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "file"
    assert block["file_path"] == "src/app.js"
    assert "new and improved version" in block["code"]
    assert "old version" not in block["code"]


def test_also_replaces_diffs_for_same_file():
    """
    The deduplication logic should NOT apply to diffs. Multiple diffs
    for the same file are valid sequential changes and should be preserved.
    """
    markdown_content = textwrap.dedent("""
        Here are some changes.

        ```diff
        --- a/src/app.js
        +++ b/src/app.js
        @@ -1,1 +1,1 @@
        - console.log("one");
        + console.log("two");
        ```

        And another change for the same file.

        ```diff
        --- a/src/app.js
        +++ b/src/app.js
        @@ -5,1 +5,1 @@
        - const x = 1;
        + const x = 2;
        ```
    """)

    blocks = extract_blocks_from_text(markdown_content)

    assert len(blocks) == 1
    assert all(b["type"] == "diff" for b in blocks)
    assert all(b["file_path"] == "src/app.js" for b in blocks)
    assert "const x = 2" in blocks[0]["code"]
