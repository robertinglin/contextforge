import os
from unittest.mock import MagicMock
from contextforge.plan import plan_changes

def test_plan_changes_passes_synthetic_blocks(tmp_path):
    """Synthetic blocks (from multi-file diffs) already have metadata."""
    # Ensure file exists so logic doesn't force 'full_replacement'
    (tmp_path / "a.py").touch()

    blocks = [{
        "is_synthetic": True,
        "synthetic_info": {"file_path": "a.py", "change_type": "diff"},
        "code": "diff code",
        "block_id": 1
    }]
    
    plans = plan_changes(blocks, str(tmp_path))
    
    assert len(plans) == 1
    assert plans[0]["metadata"]["file_path"] == "a.py"
    assert plans[0]["metadata"]["change_type"] == "diff"

def test_plan_changes_uses_pre_classification(tmp_path):
    """Blocks with explicit file paths (e.g. from headers) are pre-classified."""
    blocks = [{
        "is_pre_classified": True,
        "pre_classification": {"file_path": "b.py", "change_type": "full_replacement"},
        "code": "content",
        "block_id": 2
    }]
    
    plans = plan_changes(blocks, str(tmp_path))
    
    assert len(plans) == 1
    assert plans[0]["metadata"]["file_path"] == "b.py"
    assert plans[0]["metadata"]["change_type"] == "full_replacement"

def test_plan_changes_invokes_llm_callback(tmp_path):
    """Unclassified blocks should trigger the classifier callback."""
    blocks = [{"code": "def foo(): pass", "context": "some context", "block_id": 3}]
    
    # Mock the LLM classifier
    mock_cb = MagicMock(return_value={"file_path": "c.py", "change_type": "full_replacement"})
    
    plans = plan_changes(blocks, str(tmp_path), classifier_callback=mock_cb)
    
    assert len(plans) == 1
    assert plans[0]["metadata"]["file_path"] == "c.py"
    mock_cb.assert_called_once_with("some context", "def foo(): pass")

def test_plan_changes_resolves_bare_filenames(tmp_path):
    """The planner should resolve 'deep.py' to 'src/deep.py' if it exists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep.py").touch()
    
    blocks = [{
        "is_pre_classified": True,
        "pre_classification": {"file_path": "deep.py", "change_type": "full_replacement"},
        "code": "content"
    }]
    
    plans = plan_changes(blocks, str(tmp_path))
    
    assert plans[0]["metadata"]["file_path"] == "src/deep.py"

def test_plan_changes_forces_replacement_for_new_files(tmp_path):
    """If a file does not exist, change_type should be forced to 'full_replacement'."""
    blocks = [{"code": "...", "context": "...", "block_id": 4}]
    
    # LLM says 'diff', but file is missing
    mock_cb = MagicMock(return_value={"file_path": "missing.py", "change_type": "diff"})
    
    plans = plan_changes(blocks, str(tmp_path), classifier_callback=mock_cb)
    
    assert plans[0]["metadata"]["change_type"] == "full_replacement"
    assert plans[0]["metadata"]["file_path"] == "missing.py"