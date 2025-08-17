import os
import tempfile
from types import SimpleNamespace

from contextforge.context import build_context
from contextforge.system import append_context


def _make_tmp_tree():
    base = tempfile.mkdtemp(prefix="cf-test-")
    # .gitignore that hides one file
    with open(os.path.join(base, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("ignoreme.txt\n")
    with open(os.path.join(base, "keep.txt"), "w", encoding="utf-8") as f:
        f.write("KEEP\n")
    with open(os.path.join(base, "ignoreme.txt"), "w", encoding="utf-8") as f:
        f.write("IGNORE\n")
    return base


def test_build_context_includes_tree_and_enforces_containment():
    base = _make_tmp_tree()
    req = SimpleNamespace(
        include_file_tree=True,
        base_path=base,
        files=["keep.txt", "../outside.txt"],
        instructions="TEST-INSTR",
    )
    out = build_context(req)

    # Contains file contents for keep.txt
    assert "File: keep.txt" in out
    assert "KEEP" in out

    # Tree should not list .gitignored file
    assert "ignoreme.txt" not in out

    # Security message for path escape
    assert "Security violation - file path is outside the project directory" in out

    # Stable sections exist
    assert "<file_contents>" in out
    assert "</file_contents>" in out
    assert "<user_instructions>" in out
    assert "</user_instructions>" in out


def test_append_context_appends_with_header():
    s1 = "alpha"
    s2 = "beta"
    out = append_context(s1, s2, header="## Added")
    assert "alpha" in out and "beta" in out and "## Added" in out
