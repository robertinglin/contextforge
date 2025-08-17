import os
import sys
import pytest
from contextforge.commit.core import _normalized_path
from contextforge.errors.path import PathViolation

def test_normalized_path_enforces_containment(tmp_path):
    base = str(tmp_path.resolve())
    with pytest.raises(PathViolation, match="Path traversal attempt detected"):
        _normalized_path(base, "../evil.txt")
    with pytest.raises(PathViolation, match="Path traversal attempt detected"):
        _normalized_path(base, "a/../../evil.txt")
    with pytest.raises(PathViolation, match="Path traversal attempt detected"):
        # Test with Windows-style separators
        _normalized_path(base, "a\\..\\..\\evil.txt")

def test_normalized_path_allows_valid_paths(tmp_path):
    base = str(tmp_path.resolve())
    assert _normalized_path(base, "a/b/c.txt").startswith(base)
    assert _normalized_path(base, "a/b/../b/c.txt").startswith(base)

@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require special permissions on Windows")
def test_normalized_path_blocks_symlink_escapes(tmp_path):
    base = tmp_path.resolve()
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("OUTSIDE")
    
    (base / "a").mkdir()
    os.symlink(outside, base / "a" / "link_to_outside")
    
    with pytest.raises(PathViolation):
        _normalized_path(str(base), "a/link_to_outside")