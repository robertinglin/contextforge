import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from contextforge.core import parse_markdown_string, plan_and_generate_changes
from contextforge.errors import PatchFailedError


@patch("contextforge.core.extract_blocks_from_text")
def test_parse_markdown_string(mock_extract_blocks):
    """
    Tests the parse_markdown_string function with various block types and conditions.
    """
    mock_extract_blocks.return_value = [
        # 1. Diff block that gets classified as synthetic
        {"type": "diff", "context": "--- a/file1.py\n+++ b/file1.py", "code": "diff_code"},
        # 2. File block with a direct file_path hint
        {"type": "file", "code": "file_code_1", "language": "python", "file_path": "path/to/file2.py"},
        # 3. File block that gets pre-classified from context
        {"type": "file", "context": "File: path/to/file3.py", "code": "file_code_2", "language": "python"},
        # 4. Diff block that does not get classified as synthetic
        {"type": "diff", "context": "some context", "code": "diff_code_2"},
        # 5. File block with no classification info
        {"type": "file", "context": "no file info", "code": "file_code_3", "language": "python"},
        # 6. Block with a different type (should be ignored by the main logic but check loop)
        {"type": "other"},
    ]

    with patch("contextforge.core.extract_file_info_from_context_and_code") as mock_extract_info:
        # Configure mock for different calls
        mock_extract_info.side_effect = [
            # Call for item 1
            {"file_path": "file1.py", "change_type": "diff"},
            # Call for item 3
            {"file_path": "path/to/file3.py", "change_type": "full_replacement"},
            # Call for item 4
            {"file_path": "file4.py", "change_type": "full_replacement"}, # Not a 'diff' change_type
            # Call for item 5
            None,
        ]

        result = list(parse_markdown_string("some markdown"))

        assert len(result) == 5

        # 1. Test synthetic diff
        assert result[0]["block_id"] == 0
        assert result[0]["lang"] == "diff"
        assert result[0]["is_synthetic"] is True
        assert result[0]["synthetic_info"] == {"file_path": "file1.py", "change_type": "diff"}

        # 2. Test pre-classified file from hint
        assert result[1]["block_id"] == 1
        assert result[1]["lang"] == "python"
        assert result[1]["is_pre_classified"] is True
        assert result[1]["pre_classification"] == {
            "file_path": "path/to/file2.py",
            "change_type": "full_replacement",
        }

        # 3. Test pre-classified file from context
        assert result[2]["block_id"] == 2
        assert result[2]["is_pre_classified"] is True
        assert result[2]["pre_classification"] == {
            "file_path": "path/to/file3.py",
            "change_type": "full_replacement",
        }
        
        # 4. Test non-synthetic diff
        assert result[3]["block_id"] == 3
        assert "is_synthetic" not in result[3]

        # 5. Test file with no classification
        assert result[4]["block_id"] == 4
        assert "is_pre_classified" not in result[4]


def test_plan_and_generate_changes_full_replacement_with_truncation(tmp_path, caplog):
    """
    Tests that a warning is logged when a full replacement contains a truncation marker.
    """
    planned_changes = [
        {
            "metadata": {"file_path": "test.txt", "change_type": "full_replacement"},
            "block": {"code": "Hello\n//...\nWorld", "block_id": 0},
        }
    ]
    with caplog.at_level(logging.WARNING):
        result = plan_and_generate_changes(planned_changes, str(tmp_path))
        assert "LLM-based merging is not part of this function" in caplog.text
        assert len(result) == 1  # sanity


def test_plan_and_generate_changes_read_error(tmp_path, caplog):
    """
    Tests that a warning is logged if reading an existing file fails.
    """
    file_path = tmp_path / "test.txt"
    # Create the file but then cause a read error
    file_path.write_text("initial content")

    planned_changes = [
        {
            "metadata": {"file_path": "test.txt", "change_type": "full_replacement"},
            "block": {"code": "new content", "block_id": 0},
        }
    ]

    with patch("builtins.open", side_effect=IOError("Read failed")):
        with caplog.at_level(logging.WARNING):
            result = plan_and_generate_changes(planned_changes, str(tmp_path))
            assert "WARNING: Could not read original file" in caplog.text
            # It should proceed as if the file was empty
            assert result[0].original_content == ""
            assert result[0].new_content == "new content"

@patch("contextforge.core.patch_text")
@patch("contextforge.core.patch_fromstring")
def test_diff_patch_standard_success(mock_fromstring, mock_patch_text, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("original content")
    diff_block = {
        "code": "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-original content\n+new content",
        "block_id": 0
    }
    plan = [{"metadata": {"file_path": "test.txt", "change_type": "diff"}, "block": diff_block}]
    mock_patch_set_success = MagicMock()
    mock_patch_set_success.apply.return_value = b"successfully patched content"
    mock_fromstring.return_value = mock_patch_set_success

    result = plan_and_generate_changes(plan, str(tmp_path))
    assert len(result) == 1
    assert result[0].new_content == "successfully patched content"
    assert not mock_patch_text.called

@patch("contextforge.core.patch_text")
@patch("contextforge.core.patch_fromstring")
def test_diff_patch_standard_fails_fuzzy_succeeds(mock_fromstring, mock_patch_text, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("original content")
    diff_block = {
        "code": "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-original content\n+new content",
        "block_id": 0
    }
    plan = [{"metadata": {"file_path": "test.txt", "change_type": "diff"}, "block": diff_block}]
    mock_fromstring.side_effect = ValueError("standard patch failed")
    mock_patch_text.return_value = "fuzzy patched content"

    result = plan_and_generate_changes(plan, str(tmp_path))
    assert len(result) == 1
    assert result[0].new_content == "fuzzy patched content"
    mock_patch_text.assert_called_once_with("original content", diff_block["code"])

@patch("contextforge.core.patch_text")
@patch("contextforge.core.patch_fromstring")
def test_diff_patch_standard_returns_false_fuzzy_succeeds(mock_fromstring, mock_patch_text, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("original content")
    diff_block = {
        "code": "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-original content\n+new content",
        "block_id": 0
    }
    plan = [{"metadata": {"file_path": "test.txt", "change_type": "diff"}, "block": diff_block}]
    mock_patch_set = MagicMock()
    mock_patch_set.apply.return_value = False
    mock_fromstring.return_value = mock_patch_set
    mock_patch_text.return_value = "fuzzy patched content"

    result = plan_and_generate_changes(plan, str(tmp_path))
    assert len(result) == 1
    assert result[0].new_content == "fuzzy patched content"
    mock_patch_text.assert_called_once_with("original content", diff_block["code"])

@patch("contextforge.core.patch_text")
@patch("contextforge.core.patch_fromstring")
def test_diff_patch_both_fail(mock_fromstring, mock_patch_text, tmp_path, caplog):
    file_path = tmp_path / "test.txt"
    file_path.write_text("original content")
    diff_block = {
        "code": "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-original content\n+new content",
        "block_id": 0
    }
    plan = [{"metadata": {"file_path": "test.txt", "change_type": "diff"}, "block": diff_block}]
    mock_fromstring.side_effect = ValueError("standard patch failed")
    mock_patch_text.side_effect = PatchFailedError("fuzzy patch failed")

    with caplog.at_level(logging.ERROR):
        result = plan_and_generate_changes(plan, str(tmp_path))
        assert len(result) == 0
        assert "ERROR: Fuzzy patch failed" in caplog.text

@patch("contextforge.core.patch_text")
@patch("contextforge.core.patch_fromstring")
def test_diff_patch_bytes_decode_fails_fuzzy_succeeds(mock_fromstring, mock_patch_text, tmp_path, caplog):
    file_path = tmp_path / "test.txt"
    file_path.write_text("original content")
    diff_block = {
        "code": "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-original content\n+new content",
        "block_id": 0
    }
    plan = [{"metadata": {"file_path": "test.txt", "change_type": "diff"}, "block": diff_block}]
    mock_patch_set_bad_bytes = MagicMock()
    mock_patch_set_bad_bytes.apply.return_value = b"\xff\xfe"
    mock_fromstring.return_value = mock_patch_set_bad_bytes
    mock_patch_text.return_value = "fuzzy patched content"

    with caplog.at_level(logging.DEBUG):
        result = plan_and_generate_changes(plan, str(tmp_path))
        assert len(result) == 1
        assert result[0].new_content == "fuzzy patched content"
        mock_patch_text.assert_called_once_with("original content", diff_block["code"])


def test_plan_and_generate_changes_unknown_change_type(tmp_path, caplog):
    """
    Tests that an unknown change type is skipped and logged.
    """
    planned_changes = [
        {"metadata": {"file_path": "test.txt", "change_type": "unknown_type"}, "block": {"code": "content", "block_id": 0}}
    ]
    with caplog.at_level(logging.INFO):
        result = plan_and_generate_changes(planned_changes, str(tmp_path))
        assert len(result) == 0
        assert "Unknown change type 'unknown_type' for test.txt. Skipping." in caplog.text

def test_plan_and_generate_changes_handles_indented_diff(tmp_path):
        """
        Tests that an indented diff block is correctly dedented and applied.
        This is a key test for handling copy-paste artifacts from LLMs.
        """
        file_path = tmp_path / "test.py"
        file_path.write_text("print('old')\n")

        # This diff is indented by four spaces.
        indented_diff = """\
   ```diff
   --- a/src/components/AppLayout.tsx
   +++ b/src/components/AppLayout.tsx
   @@ -176,7 +176,7 @@ export function AppLayout() {
              <SidebarHeader className="p-2">
                  <Link to="/" className="flex items-center gap-2 p-2 font-semibold">
                      <FolderGit2 className="h-7 w-7 text-primary" />
   -                  <span className="group-data-[state=expanded]:inline
   group-data-[state=collapsed]:hidden font-bold">merged</span>
   +                  <span className="group-data-[state=expanded]:inline
   group-data-[state=collapsed]:hidden font-bold">Merged</span>
                  </Link>
              </SidebarHeader>
              <SidebarContent>
   @@ -205,7 +205,7 @@ export function AppLayout() {
                        <Badge variant="outline">{repoState.branch}</Badge>
                      </>
                    ) : (
   -                  <h1 className="text-lg font-semibold">{headerTitle || 'merged'}</h1>
   +                  <h1 className="text-lg font-semibold">{headerTitle || 'Merged'}</h1>
                    )}
                  </div>
              </header>}
   ```
   """

        result = list(parse_markdown_string(indented_diff))
        print(result)
        assert len(result) == 1
        assert result[0].new_content == "print('new')\n"

def test_plan_and_generate_changes_empty_input():
    """
    Tests that an empty list of planned changes returns an empty list.
    """
    assert plan_and_generate_changes([], "/fake/dir") == []