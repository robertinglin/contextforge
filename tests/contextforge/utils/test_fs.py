import os
from contextforge.utils.fs import resolve_filename

def test_resolve_filename_root_match(tmp_path):
    """Should return the file immediately if it exists at the root."""
    (tmp_path / "root.txt").touch()
    path, logs = resolve_filename("root.txt", str(tmp_path))
    assert path == "root.txt"
    assert not logs

def test_resolve_filename_unique_match_in_subdir(tmp_path):
    """Should find a unique file in a subdirectory and return relative path."""
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "deep.txt").touch()
    
    path, logs = resolve_filename("deep.txt", str(tmp_path))
    
    # Expect forward slashes for consistency
    assert path == "subdir/deep.txt"
    assert any("Found unique match" in l for l in logs)

def test_resolve_filename_ambiguous_match(tmp_path):
    """Should return the original input if multiple matches are found."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "dup.txt").touch()
    (tmp_path / "b" / "dup.txt").touch()
    
    path, logs = resolve_filename("dup.txt", str(tmp_path))
    
    assert path == "dup.txt" # Unchanged due to ambiguity
    assert any("Found multiple files" in l for l in logs)

def test_resolve_filename_ignore_git_directory(tmp_path):
    """Should not search inside .git directories."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignore.txt").touch()
    
    path, logs = resolve_filename("ignore.txt", str(tmp_path))
    
    assert path == "ignore.txt" # Not found
    assert not any("Found unique match" in l for l in logs)

def test_resolve_filename_absolute_or_relative_path_passed(tmp_path):
    """Should return as-is if the input looks like a path (has separators)."""
    input_path = "some/dir/file.txt"
    path, logs = resolve_filename(input_path, str(tmp_path))
    assert path == input_path
    assert not logs