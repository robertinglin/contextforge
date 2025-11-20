import os
from unittest.mock import MagicMock, patch
from contextforge.transform import apply_change_smartly
from contextforge.commit.patch import PatchFailedError


def test_apply_change_smartly_full_replacement_simple(tmp_path):
    plan = {
        "metadata": {"file_path": "a.txt", "change_type": "full_replacement"},
        "block": {"code": "new content", "block_id": 1},
    }
    result, logs = apply_change_smartly(plan, str(tmp_path))
    assert result["new_content"] == "new content"
    assert result["original_change_type"] == "full_replacement"


def test_apply_change_smartly_smart_merge_truncation(tmp_path):
    """If truncation markers detected, invoke merge_callback."""
    (tmp_path / "a.py").write_text("original code")

    # Code has truncation marker '# ...'
    plan = {
        "metadata": {"file_path": "a.py", "change_type": "full_replacement"},
        "block": {"code": "start\n# ...\nend", "block_id": 2},
    }

    mock_merge = MagicMock(return_value="merged content")

    result, logs = apply_change_smartly(plan, str(tmp_path), merge_callback=mock_merge)

    assert result["new_content"] == "merged content"
    mock_merge.assert_called_once_with("original code", "start\n# ...\nend")
    assert any("Tier M" in l for l in logs)


def test_apply_change_smartly_diff_tier2_fuzzy(tmp_path):
    """Tier 2 (Fuzzy Patch) should be used if standard patch isn't available/fails."""
    (tmp_path / "a.py").write_text("line1\nline2")

    # Simple fuzzy patch
    patch_code = "@@\n-line1\n+LINE1"
    plan = {
        "metadata": {"file_path": "a.py", "change_type": "diff"},
        "block": {"code": patch_code, "block_id": 3},
    }

    # contextforge uses patch_text for Tier 2
    result, logs = apply_change_smartly(plan, str(tmp_path))

    assert result["new_content"] == "LINE1\nline2"
    assert any("Tier 2" in l for l in logs)


def test_apply_change_smartly_diff_tier3_fallback(tmp_path):
    """Tier 3 (LLM Patch) should be used if fuzzy patch fails."""
    (tmp_path / "a.py").write_text("complex content")

    patch_code = "@@\n-nonexistent\n+stuff"
    plan = {
        "metadata": {"file_path": "a.py", "change_type": "diff"},
        "block": {"code": patch_code, "block_id": 4},
    }

    mock_patch_cb = MagicMock(return_value="llm patched result")

    # 1. Force patch_lib to None to skip Tier 1
    # 2. Force patch_text to raise PatchFailedError to fail Tier 2
    with patch("contextforge.transform.patch_lib", None), patch(
        "contextforge.transform.patch_text", side_effect=PatchFailedError("Mock failure")
    ):
        result, logs = apply_change_smartly(plan, str(tmp_path), patch_callback=mock_patch_cb)

    assert result["new_content"] == "llm patched result"
    assert any("Tier 2 failed" in l for l in logs)
    assert any("Tier 3" in l for l in logs)


def test_apply_change_smartly_unknown_change_type(tmp_path):
    plan = {
        "metadata": {"file_path": "a.txt", "change_type": "teleport"},
        "block": {"code": "", "block_id": 5},
    }
    result, logs = apply_change_smartly(plan, str(tmp_path))
    assert result is None
    assert any("Unknown change type" in l for l in logs)
