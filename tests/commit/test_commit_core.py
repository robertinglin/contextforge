import os
from contextforge.commit.core import Change, commit_changes


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_best_effort_writes_and_reports_failures(tmp_path):
    base = tmp_path
    good = Change(path="a.txt", new_content="A", original_content="", is_new=True)
    # Path traversal should be blocked and reported.
    bad = Change(path="../evil.txt", new_content="X", original_content="", is_new=True)

    summary = commit_changes(str(base), [good, bad], mode="best_effort", atomic=False, dry_run=False)
    assert "a.txt" in summary.success
    assert "../evil.txt" in summary.failed
    assert (base / "a.txt").exists()
    assert read(base / "a.txt") == "A"


def test_fail_fast_atomic_keeps_fs_unchanged_on_error(tmp_path):
    base = tmp_path
    ok = Change(path="ok.txt", new_content="OK", original_content="", is_new=True)
    bad = Change(path="../oops.txt", new_content="X", original_content="", is_new=True)

    # With atomic+fail_fast, the bad change causes an abort and no files should appear.
    summary = commit_changes(str(base), [ok, bad], mode="fail_fast", atomic=True, dry_run=False)
    assert "ok.txt" not in summary.success
    assert "../oops.txt" in summary.failed
    assert not (base / "ok.txt").exists()


def test_dry_run_reports_plan_without_writing(tmp_path):
    base = tmp_path
    ch = Change(path="plan.txt", new_content="P", original_content="", is_new=True)
    summary = commit_changes(str(base), [ch], dry_run=True)
    assert any("DRY RUN" in s and "plan.txt" in s for s in summary.success)
    assert not (base / "plan.txt").exists()


def test_backup_ext_writes_backup_for_existing_file(tmp_path):
    base = tmp_path
    target = base / "c.txt"
    target.write_text("OLD", encoding="utf-8")
    ch = Change(path="c.txt", new_content="NEW", original_content="OLD", is_new=False)

    summary = commit_changes(str(base), [ch], atomic=False, backup_ext=".bak")
    assert "c.txt" in summary.success
    assert read(target) == "NEW"
    assert (base / "c.txt.bak").exists()
    assert read(base / "c.txt.bak") == "OLD"


def test_atomic_best_effort_promotes_what_succeeds(tmp_path, monkeypatch):
    base = tmp_path
    ok = Change(path="x.txt", new_content="X", original_content="", is_new=True)
    nope = Change(path="../no.txt", new_content="N", original_content="", is_new=True)

    summary = commit_changes(str(base), [ok, nope], mode="best_effort", atomic=True, dry_run=False)
    assert "x.txt" in summary.success
    assert "../no.txt" in summary.failed
    assert (base / "x.txt").exists()
