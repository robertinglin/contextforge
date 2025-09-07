from types import SimpleNamespace

from contextforge.context.builder import _build_context_string
from contextforge.utils.gitignore import get_gitignore
from contextforge.utils.tree import _generate_tree_string


def test_security_violation_formatting(tmp_path):
    (tmp_path / "safe.txt").write_text("SAFE")

    req = SimpleNamespace(
        base_path=str(tmp_path),
        files=["safe.txt", "../unsafe.txt"],
        instructions="test",
        include_file_tree=False,
    )

    context = _build_context_string(req)

    assert "File: safe.txt" in context
    assert "SAFE" in context
    assert "File: ../unsafe.txt" in context
    assert "Error: Security violation - file path is outside the project directory." in context


def test_gitignore_tree_rendering(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\nnode_modules/\n/config.py")
    (tmp_path / "main.py").touch()
    (tmp_path / "run.log").touch()
    (tmp_path / "config.py").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "component.js").touch()
    (tmp_path / "src" / "config.py").touch()  # Should not be ignored
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "some-lib").touch()

    spec = get_gitignore(str(tmp_path))
    tree = _generate_tree_string(str(tmp_path), spec)

    assert "main.py" in tree
    assert "src" in tree
    assert "component.js" in tree
    assert "config.py" in tree
    assert "run.log" not in tree
    assert "node_modules" not in tree

    # Check that root config.py is ignored but nested one is not
    tree_lines = tree.splitlines()
    assert not any(
        line.endswith("├── config.py") or line.endswith("└── config.py") for line in tree_lines
    )
    assert any(
        line.endswith("├── config.py") or line.endswith("└── config.py")
        for line in tree_lines
        if "src" in line
    )
