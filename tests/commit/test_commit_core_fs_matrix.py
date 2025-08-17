import os
import stat
import pytest
from unittest.mock import patch

from contextforge.commit.core import Change, commit_changes

def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

@pytest.mark.parametrize("atomic", [True, False])
@pytest.mark.parametrize("backup_ext", [None, ".bak", "bak"])
def test_backup_behavior_matrix(tmp_path, atomic, backup_ext):
    base = tmp_path
    # Existing file
    (base / "existing.txt").write_text("old content")
    # New file
    changes = [
        Change(path="existing.txt", new_content="new", original_content="old content", is_new=False),
        Change(path="new.txt", new_content="new", original_content="", is_new=True),
    ]

    summary = commit_changes(str(base), changes, atomic=atomic, backup_ext=backup_ext)

    assert "existing.txt" in summary.success
    assert "new.txt" in summary.success
    assert read(base / "existing.txt") == "new"
    assert read(base / "new.txt") == "new"

    backup_path = base / "existing.txt.bak"
    if backup_ext:
        assert backup_path.exists()
        assert read(backup_path) == "old content"
    else:
        assert not backup_path.exists()

    # No backup for new files
    assert not (base / "new.txt.bak").exists()

def test_fail_fast_non_atomic_partial_write(tmp_path):
    base = tmp_path
    changes = [
        Change("a.txt", "A", "", is_new=True),
        Change("../invalid.txt", "X", "", is_new=True), # This will fail
        Change("b.txt", "B", "", is_new=True), # This should not be written
    ]
    summary = commit_changes(str(base), changes, mode="fail_fast", atomic=False)

    assert "a.txt" in summary.success
    assert "../invalid.txt" in summary.failed
    assert "b.txt" not in summary.success
    assert (base / "a.txt").exists()
    assert not (base / "b.txt").exists()

def test_atomic_promotion_rollback(tmp_path):
    base = tmp_path
    (base / "a.txt").write_text("original A")
    (base / "b.txt").write_text("original B")
    (base / "c.txt").mkdir() # Make this a directory to cause os.replace to fail
    
    changes = [
        Change("a.txt", "new A", "original A", is_new=False),
        Change("b.txt", "new B", "original B", is_new=False),
        Change("c.txt", "new C", "", is_new=True), # This will fail on promotion
    ]
    
    summary = commit_changes(str(base), changes, mode="fail_fast", atomic=True)

    assert "c.txt" in summary.failed
    assert "a.txt" not in summary.success
    assert "b.txt" not in summary.success
    
    # Assert rollback
    assert read(base / "a.txt") == "original A"
    assert read(base / "b.txt") == "original B"
    assert (base / "c.txt").is_dir() # Should not have been replaced

@patch("tempfile.mkstemp")
def test_staging_failure_cleanup(mock_mkstemp, tmp_path):
    mock_mkstemp.side_effect = IOError("Disk full")
    base = tmp_path
    changes = [Change("a.txt", "A", "", is_new=True)]
    
    summary = commit_changes(str(base), changes, mode="fail_fast", atomic=True)
    
    assert "a.txt" in summary.failed
    assert "Disk full" in summary.errors["a.txt"]
    
    assert not (base / "a.txt").exists()
    temp_files = [f for f in os.listdir(base) if f.startswith(".cf-")]
    assert not temp_files

def test_dry_run_permissions_probe(tmp_path):
    base = tmp_path
    unwritable_dir = tmp_path / "unwritable"
    unwritable_dir.mkdir()
    # On Windows, removing write permission from a directory doesn't prevent file creation inside it.
    # This test is therefore more reliable on POSIX systems.
    try:
        os.chmod(unwritable_dir, stat.S_IREAD | stat.S_IEXEC)
    except PermissionError:
        pytest.skip("Could not set directory to read-only")

    changes = [Change("unwritable/a.txt", "A", "", is_new=True)]
    
    summary = commit_changes(str(base), changes, mode="fail_fast", dry_run=True)
    
    if os.name != 'nt':
        assert "unwritable/a.txt" in summary.failed
        assert "PermissionError" in summary.errors["unwritable/a.txt"]
    
    # cleanup
    os.chmod(unwritable_dir, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)

def test_directory_creation(tmp_path):
    base = tmp_path
    changes = [Change("new/deep/dir/file.txt", "content", "", is_new=True)]
    
    # Non-atomic
    summary = commit_changes(str(base), changes, atomic=False)
    assert "new/deep/dir/file.txt" in summary.success
    assert read(base / "new/deep/dir/file.txt") == "content"
    
    # Atomic
    (base / "new/deep/dir/file.txt").unlink()
    (base / "new/deep/dir").rmdir()
    (base / "new/deep").rmdir()
    (base / "new").rmdir()

    summary = commit_changes(str(base), changes, atomic=True)
    assert "new/deep/dir/file.txt" in summary.success
    assert read(base / "new/deep/dir/file.txt") == "content"