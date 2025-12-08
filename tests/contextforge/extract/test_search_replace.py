import textwrap
import pytest

from contextforge.extract import extract_blocks_from_text
from contextforge.plan import plan_changes
from contextforge.transform import apply_change_smartly


def test_extract_basic_search_replace_block():
    """Test extraction of a basic SEARCH/REPLACE block."""
    content = textwrap.dedent("""
        Here's the change for src/file.ts:

        ```typescript
        <<<<<<< SEARCH
        const old = 1;
        =======
        const old = 2;
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 1
    block = blocks[0]

    assert block["type"] == "file"
    assert block["language"] == "typescript"
    assert block["is_search_replace"] is True
    assert block["old_content"] == "const old = 1;"
    assert block["new_content"] == "const old = 2;"
    assert block["file_path"] == "src/file.ts"


def test_extract_search_replace_with_multiline_content():
    """Test SEARCH/REPLACE block with multiline content."""
    content = textwrap.dedent("""
        src/types/index.ts
        ```typescript
        <<<<<<< SEARCH
        export interface FileTreeNode {
          name: string;
          path: string;
        }
        =======
        export interface FileTreeNode {
          name: string;
          path: string;
          selection_state?: 'checked' | 'unchecked' | 'indeterminate';
        }
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 1
    block = blocks[0]

    assert block["type"] == "file"
    assert block["is_search_replace"] is True
    assert "export interface FileTreeNode" in block["old_content"]
    assert "selection_state?" in block["new_content"]
    assert "selection_state?" not in block["old_content"]
    assert block["file_path"] == "src/types/index.ts"


def test_extract_search_replace_complex_example():
    """Test the exact SEARCH/REPLACE example from the user instructions."""
    content = textwrap.dedent("""
        src_v2/types/index.ts
        ```typescript
        <<<<<<< SEARCH
        export interface FileTreeNode {
          name: string;
          path: string;
          type: 'file' | 'directory';
          children?: FileTreeNode[];
          is_expanded?: boolean;
          children_loaded?: boolean;
          token_count?: number;
          is_binary?: boolean;
          is_test?: boolean;
          extension?: string;
          file_count?: number;
          total_tokens?: number;
          token_count?: number;
        }

        export interface FileTreeData {
          tree: FileTreeNode[];
          state_version: number;
        }
        =======
        export interface FileTreeNode {
          name: string;
          path: string;
          type: 'file' | 'directory';
          children?: FileTreeNode[];
          is_expanded?: boolean;
          children_loaded?: boolean;
          token_count?: number;
          is_binary?: boolean;
          is_test?: boolean;
          extension?: string;
          file_count?: number;
          total_tokens?: number;
          selection_state?: 'checked' | 'unchecked' | 'indeterminate';
        }

        export interface FileTreeData {
          tree: FileTreeNode[];
          state_version: number;
          selected_token_count?: number;
        }
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 1
    block = blocks[0]

    assert block["type"] == "file"
    assert block["is_search_replace"] is True
    assert block["language"] == "typescript"
    assert block["file_path"] == "src_v2/types/index.ts"

    # Check old content
    assert "export interface FileTreeNode" in block["old_content"]
    assert "export interface FileTreeData" in block["old_content"]

    # The original file has duplicates but they are not adjacent.
    # Just verify they exist in the extracted block.
    assert block["old_content"].count("token_count?: number;") == 2

    assert "selection_state?" not in block["old_content"]
    assert "selected_token_count?" not in block["old_content"]

    # Check new content
    assert "export interface FileTreeNode" in block["new_content"]
    assert "export interface FileTreeData" in block["new_content"]
    assert "selection_state?: 'checked' | 'unchecked' | 'indeterminate';" in block["new_content"]
    assert "selected_token_count?: number;" in block["new_content"]


def test_plan_changes_recognizes_search_replace(tmp_path):
    """Test that plan_changes correctly handles SEARCH/REPLACE blocks."""
    content = textwrap.dedent("""
        src/app.ts
        ```typescript
        <<<<<<< SEARCH
        const x = 1;
        =======
        const x = 2;
        >>>>>>> REPLACE
        ```
    """)

    # Ensure the file exists so plan_changes doesn't force 'full_replacement'
    p = tmp_path / "src" / "app.ts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("const x = 1;")

    blocks = extract_blocks_from_text(content)
    plans = plan_changes(blocks, str(tmp_path))

    assert len(plans) == 1
    plan = plans[0]

    # Standardize path separators for assertion
    assert plan["metadata"]["file_path"].replace("\\", "/") == "src/app.ts"
    assert plan["metadata"]["change_type"] == "search_replace"
    assert plan["block"]["is_search_replace"] is True


def test_apply_change_smartly_with_search_replace(tmp_path):
    """Test that apply_change_smartly applies SEARCH/REPLACE correctly."""
    # Create a file with original content
    test_file = tmp_path / "app.ts"
    test_file.write_text("const x = 1;\nconst y = 2;\n")

    # Create a SEARCH/REPLACE plan
    plan = {
        "metadata": {
            "file_path": "app.ts",
            "change_type": "full_replacement",  # SEARCH/REPLACE is treated as full_replacement
        },
        "block": {
            "is_search_replace": True,
            "old_content": "const x = 1;",
            "new_content": "const x = 10;",
            "code": "",
            "block_id": 1,
        },
    }

    result, logs = apply_change_smartly(plan, str(tmp_path))

    assert result is not None
    assert "const x = 10;" in result["new_content"]
    assert "const y = 2;" in result["new_content"]  # Unchanged line preserved
    assert "const x = 1;" not in result["new_content"]  # Old line replaced


def test_multiple_search_replace_blocks():
    """Test extraction of multiple SEARCH/REPLACE blocks."""
    content = textwrap.dedent("""
        First change for src/a.ts:
        ```typescript
        <<<<<<< SEARCH
        const a = 1;
        =======
        const a = 2;
        >>>>>>> REPLACE
        ```

        Second change for src/b.ts:
        ```typescript
        <<<<<<< SEARCH
        const b = 1;
        =======
        const b = 2;
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 2
    assert all(b["type"] == "file" for b in blocks)
    assert all(b["is_search_replace"] for b in blocks)
    assert blocks[0]["file_path"] == "src/a.ts"
    assert blocks[1]["file_path"] == "src/b.ts"
    assert blocks[0]["old_content"] == "const a = 1;"
    assert blocks[0]["new_content"] == "const a = 2;"
    assert blocks[1]["old_content"] == "const b = 1;"
    assert blocks[1]["new_content"] == "const b = 2;"


def test_search_replace_without_file_path():
    """Test SEARCH/REPLACE block without explicit file path."""
    content = textwrap.dedent("""
        ```python
        <<<<<<< SEARCH
        x = 1
        =======
        x = 2
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 1
    block = blocks[0]

    assert block["type"] == "file"
    assert block["is_search_replace"] is True
    assert block["file_path"] is None  # No path hint found
    assert block["old_content"] == "x = 1"
    assert block["new_content"] == "x = 2"


def test_multiple_search_replace_blocks_in_same_fence():
    """Test extraction of multiple SEARCH/REPLACE blocks within the same fenced code block."""
    content = textwrap.dedent("""
        src_v2/components/workspace/ContextPanel.tsx
        ```tsx
        <<<<<<< SEARCH
        const x = 1;
        const y = 2;
        =======
        const x = 10;
        const y = 20;
        >>>>>>> REPLACE
        <<<<<<< SEARCH
        function doSomething() {
          console.log("old");
        }
        =======
        function doSomething() {
          console.log("new");
        }
        >>>>>>> REPLACE
        <<<<<<< SEARCH
        export default App;
        =======
        export default ContextPanel;
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    # Should extract 3 separate SEARCH/REPLACE blocks from the same fence
    assert len(blocks) == 3

    # All blocks should have the same file path
    for block in blocks:
        assert block["type"] == "file"
        assert block["is_search_replace"] is True
        assert block["language"] == "tsx"
        assert block["file_path"] == "src_v2/components/workspace/ContextPanel.tsx"

    # Check content of each block
    assert blocks[0]["old_content"] == "const x = 1;\nconst y = 2;"
    assert blocks[0]["new_content"] == "const x = 10;\nconst y = 20;"

    assert "function doSomething()" in blocks[1]["old_content"]
    assert '"old"' in blocks[1]["old_content"]
    assert '"new"' in blocks[1]["new_content"]

    assert blocks[2]["old_content"] == "export default App;"
    assert blocks[2]["new_content"] == "export default ContextPanel;"


def test_multiple_search_replace_blocks_realistic_example():
    """Test the exact pattern from user's example with multiple blocks in same fence."""
    content = textwrap.dedent("""
        src_v2/components/workspace/ContextPanel.tsx
        ```tsx
        <<<<<<< SEARCH
          const { 
            items: conversationItems, 
            clearItems: clearConversationItems,
            getTotalTokenCount: getContextTokenCount 
          } = useConversationContextStore();
        =======
          const { 
            items: conversationItems, 
            clearItems: clearConversationItems,
            getTotalTokenCount: getContextTokenCount 
          } = useConversationContextStore();

          // New wrapper function added
          const onGenerateProposal = useCallback(() => {
            onGenerate();
          }, [onGenerate]);
        >>>>>>> REPLACE
        <<<<<<< SEARCH
            // Ctrl/Cmd + Enter to generate
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault();
              if (!isGenerating && instructions.trim()) {
                onGenerate();
              }
            }
        =======
            // Ctrl/Cmd + Enter to generate
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              e.preventDefault();
              if (!isGenerating && instructions.trim()) {
                onGenerateProposal();
              }
            }
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 2

    for block in blocks:
        assert block["type"] == "file"
        assert block["is_search_replace"] is True
        assert block["file_path"] == "src_v2/components/workspace/ContextPanel.tsx"

    # First block adds the wrapper function
    assert "useConversationContextStore()" in blocks[0]["old_content"]
    assert "onGenerateProposal" in blocks[0]["new_content"]
    assert "onGenerateProposal" not in blocks[0]["old_content"]

    # Second block changes onGenerate to onGenerateProposal
    assert "onGenerate();" in blocks[1]["old_content"]
    assert "onGenerateProposal();" in blocks[1]["new_content"]


def test_multiple_files_with_multiple_search_replace_blocks():
    """Test extraction from multiple files, each with multiple SEARCH/REPLACE blocks."""
    content = textwrap.dedent("""
        src/file1.ts
        ```typescript
        <<<<<<< SEARCH
        const a = 1;
        =======
        const a = 10;
        >>>>>>> REPLACE
        <<<<<<< SEARCH
        const b = 2;
        =======
        const b = 20;
        >>>>>>> REPLACE
        ```

        src/file2.ts
        ```typescript
        <<<<<<< SEARCH
        const c = 3;
        =======
        const c = 30;
        >>>>>>> REPLACE
        ```
    """)

    blocks = extract_blocks_from_text(content)

    assert len(blocks) == 3

    # First two blocks from file1
    assert blocks[0]["file_path"] == "src/file1.ts"
    assert blocks[0]["old_content"] == "const a = 1;"
    assert blocks[1]["file_path"] == "src/file1.ts"
    assert blocks[1]["old_content"] == "const b = 2;"

    # Third block from file2
    assert blocks[2]["file_path"] == "src/file2.ts"
    assert blocks[2]["old_content"] == "const c = 3;"