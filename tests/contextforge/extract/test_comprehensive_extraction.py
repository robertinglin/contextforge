# tests/contextforge/extract/test_comprehensive_extraction.py
import textwrap
from contextforge.extract.extract import extract_all_blocks_from_text

# ==============================================================================
# Comprehensive Test Suite for Block Extraction
#
# This suite covers the edge cases discovered during debugging, including:
# 1. False positives: Fences inside string literals (the "yolo" case).
# 2. Nesting: Multi-level nested blocks.
# 3. Same-line closers: Fences attached to the end of a code line.
# 4. Mixed fences: Nesting tildes inside backticks and vice-versa.
# 5. Unclosed blocks: Ensuring the parser doesn't crash and returns valid blocks.
# 6. Adjacent blocks: Simple sanity check for multiple top-level blocks.
# 7. Empty blocks: Correctly parsing blocks with no content.
# ==============================================================================
import logging
import sys

# This will print all DEBUG (and higher) level messages to your console's
# standard error when you run the tests.
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)


def test_yolo_service_false_positive_fence_in_string():
    """
    Ensures that a fence-like sequence inside a string literal does not
    prematurely close the top-level block. This was the original bug.
    """
    markdown_content = textwrap.dedent("""
        Here are the changes for `backend/app/services/yolo_service.py`:

        ```python
        # some python code...
        def construct_prompt():
            evaluation_prompt = "..."
            # This line should be treated as simple text content
            evaluation_prompt += "```diff\\n" 
            evaluation_prompt += "--- a/file.txt\\n"
            return evaluation_prompt
        
        # more python code...
        ```
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    # The extractor should only find ONE top-level block.
    assert len(blocks) == 1, "Should only extract one top-level block"
    
    block = blocks[0]
    assert block["language"] == "python"
    
    # The "false positive" fence must be present as content inside the block.
    assert 'evaluation_prompt += "```diff\\n"' in block["code"]


def test_deeply_nested_blocks_are_handled_correctly():
    """
    The parser should only return the outermost block, treating all
    inner fences as part of the content.
    """
    markdown_content = textwrap.dedent("""
        File: `README.md`
        ```md
        # Main Document
        
        Here is an example of a shell command:
        
        ```sh
        echo "Hello World"
        ```
        
        And that's how you do it.
        ```
    """)

    blocks = extract_all_blocks_from_text(markdown_content)
    
    # Only the outer 'md' block should be extracted.
    assert len(blocks) == 1, "Failed to ignore nested block"
    
    block = blocks[0]
    assert block["language"] == "md"
    
    # The content should contain the inner block's fences and code.
    assert '```sh' in block["code"]
    assert 'echo "Hello World"' in block["code"]


def test_same_line_closer_with_content():
    """
    Tests that a block is correctly terminated when the closing fence is
    on the same line as the last line of code.
    """
    markdown_content = 'File: main.go\n````go\npackage main\n\nfunc main() { println("ok") }\n````'
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    assert len(blocks) == 1, "Failed to parse block with same-line closer"
    
    block = blocks[0]
    assert block["language"] == "go"
    assert 'println("ok")' in block["code"]


def test_unclosed_block_at_end_of_file_is_ignored():
    """
    If a file ends with an unclosed block, the parser should not crash
    and should return any valid blocks that were completed before it.
    """
    markdown_content = textwrap.dedent("""
        ```json
        { "key": "value" }
        ```
        
        Now for a block that never closes...
        
        ```python
        def incomplete_function():
            pass
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    # It should only return the valid JSON block.
    assert len(blocks) == 1, "Parser should ignore unclosed blocks"
    assert blocks[0]["language"] == "json"


def test_mixed_fence_characters_nesting():
    """
    Tests that nesting different fence types (e.g., tildes inside backticks)
    is handled correctly.
    """
    markdown_content = textwrap.dedent("""
        ```javascript
        function demo() {
            const message = `
        ~~~text
        This is a tilde-fenced block inside a backtick one.
        ~~~
            `;
            return message;
        }
        ```
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)

    assert len(blocks) == 1, "Should handle mixed nested fences"
    assert blocks[0]["language"] == "javascript"
    assert "~~~text" in blocks[0]["code"]


def test_adjacent_blocks_are_handled():
    """
    A simple sanity check to ensure two top-level blocks right after
    one another are both extracted correctly.
    """
    markdown_content = textwrap.dedent("""
        ```python
        print("Block 1")
        ```
        ```typescript
        console.log("Block 2");
        ```
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    assert len(blocks) == 2, "Failed to extract adjacent blocks"
    assert blocks[0]["language"] == "python"
    assert blocks[1]["language"] == "typescript"
    assert "Block 1" in blocks[0]["code"]
    assert "Block 2" in blocks[1]["code"]


def test_empty_code_block_is_parsed():
    """
    An empty code block should be parsed correctly as a block with
    an empty string for its content.
    """
    markdown_content = textwrap.dedent("""
        Here is an empty block:
        ```python
        ```
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    assert len(blocks) == 1, "Failed to parse an empty block"
    assert blocks[0]["language"] == "python"
    assert blocks[0]["code"] == ""


def test_longer_closing_fence_is_valid():
    """
    A block opened with N fences can be closed by N or more.
    e.g., ``` opened can be closed by `````.
    """
    markdown_content = textwrap.dedent("""
        ```python
        x = 1
        `````
    """)
    
    blocks = extract_all_blocks_from_text(markdown_content)
    
    assert len(blocks) == 1, "Failed to close with a longer fence"
    assert "x = 1" in blocks[0]["code"]