from contextforge.commit.core import Change, commit_changes
import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from contextforge.commit.core import Change, commit_changes
from contextforge.errors.path import PathViolation

def test_commit_changes_invalid_mode_raises_value_error(tmp_path):
    """Ensures an invalid 'mode' argument raises a ValueError (unguarded ❌ line)."""
    with pytest.raises(ValueError):
        commit_changes(str(tmp_path), [], mode="not-a-mode")


def test_dry_run_modify_permission_denied_logs_and_records(tmp_path, monkeypatch):
    """
    Dry-run path: simulate a non-writable directory to trigger the
    PermissionError branch in the (create|modify) dry-run logic (❌ line 120).
    """
    # Arrange: an existing file (modify requires existence due to normalization)
    rel = "sub/exists.txt"
    abs_file = tmp_path / "sub" / "exists.txt"
    abs_file.parent.mkdir(parents=True)
    abs_file.write_text("old")

    # Only deny write access to the containing dir during the dry-run permission probe.
    real_exists = os.path.exists
    real_access = os.access

    def fake_exists(path):
        # Everything exists as normal
        return real_exists(path)

    def fake_access(path, mode):
        # Deny write to the specific directory
        if os.path.samefile(path, abs_file.parent):
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(os, "access", fake_access)

    summary = commit_changes(
        str(tmp_path),
        [Change(action="modify", path=rel, new_content="new")],
        dry_run=True,
    )
    assert rel in summary.failed
    assert "No write permission for directory" in summary.errors[rel]


def test_dry_run_delete_success_and_failure_with_mocked_normalization(tmp_path, monkeypatch):
    """
    Exercise dry-run delete paths:
    - Success message when the target "exists"
    - FileNotFoundError branch when it "does not exist" (❌ lines 124–127, 132–136)
    We mock _normalized_path to decouple from existence checks at normalization time.
    """
    from contextforge.commit import core as ccore

    base_real = os.path.realpath(str(tmp_path))
    resolved_target = os.path.join(base_real, "will_be_deleted.txt")

    # 1) Make normalization always "succeed" and return our resolved path.
    monkeypatch.setattr(ccore, "_normalized_path", lambda _base, rel, check_exists=False: resolved_target)

    # 2a) Pretend the file exists -> success message path
    monkeypatch.setattr(os.path, "exists", lambda p: p == resolved_target)
    summary_ok = commit_changes(
        str(tmp_path),
        [Change(action="delete", path="will_be_deleted.txt")],
        dry_run=True,
    )
    assert "will_be_deleted.txt" in summary_ok.success[0]
    assert not summary_ok.failed

    # 2b) Pretend the file does NOT exist -> FileNotFoundError path
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    summary_missing = commit_changes(
        str(tmp_path),
        [Change(action="delete", path="will_be_deleted.txt")],
        dry_run=True,
    )
    assert "will_be_deleted.txt" in summary_missing.failed
    assert "File to delete not found" in summary_missing.errors["will_be_deleted.txt"]


def test_dry_run_rename_success_and_failure_with_mocked_normalization(tmp_path, monkeypatch):
    """
    Exercise dry-run rename paths:
    - Success message when "from" exists
    - FileNotFoundError when "from" is missing (❌ lines 129–131, 132–136)
    """
    from contextforge.commit import core as ccore

    base_real = os.path.realpath(str(tmp_path))
    from_resolved = os.path.join(base_real, "from.txt")
    to_resolved = os.path.join(base_real, "to.txt")

    # Return from_resolved for the first call (from_path), and to_resolved thereafter.
    def fake_norm(_base, rel, check_exists=False):
        return from_resolved if rel == "from.txt" else to_resolved

    monkeypatch.setattr(ccore, "_normalized_path", fake_norm)

    # a) "from" exists -> success message
    monkeypatch.setattr(os.path, "exists", lambda p: p == from_resolved)
    summary_ok = commit_changes(
        str(tmp_path),
        [Change(action="rename", path="to.txt", from_path="from.txt")],
        dry_run=True,
    )
    assert "from.txt to to.txt" in summary_ok.success[0]
    assert not summary_ok.failed

    # b) "from" missing -> error branch
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    summary_missing = commit_changes(
        str(tmp_path),
        [Change(action="rename", path="to.txt", from_path="from.txt")],
        dry_run=True,
    )
    assert "to.txt" in summary_missing.failed
    assert "File to rename not found" in summary_missing.errors["to.txt"]


def test_atomic_staging_cleanup_on_fail_fast(tmp_path, monkeypatch):
    """
    Force a staging error after one file has been staged to exercise the
    cleanup path that removes temporary staged files (❌ lines 166–168).
    """
    # Existing file for "modify" (normalization needs existence)
    ok_rel = "ok.txt"
    fail_rel = "boom.txt"
    (tmp_path / ok_rel).write_text("old")
    (tmp_path / fail_rel).write_text("old")

    staged_tmp_holder = {"path": None}
    real_mkstemp = tempfile.mkstemp

    def mkstemp_side_effect(*args, **kwargs):
        # First call: create a real temp file and remember its path.
        if staged_tmp_holder["path"] is None:
            fd, p = real_mkstemp(*args, **kwargs)
            staged_tmp_holder["path"] = p
            return fd, p
        # Second call: simulate failure
        raise OSError("simulated staging failure")

    with patch("contextforge.commit.core.tempfile.mkstemp", side_effect=mkstemp_side_effect):
        summary = commit_changes(
            str(tmp_path),
            [
                Change(action="modify", path=ok_rel, new_content="new1"),
                Change(action="modify", path=fail_rel, new_content="new2"),
            ],
            atomic=True,
            mode="fail_fast",
        )

    # The staged tmp for the first file should have been cleaned up.
    # (If cleanup didn't happen, this file would still exist.)
    assert staged_tmp_holder["path"] is not None
    assert not os.path.exists(staged_tmp_holder["path"])
    assert fail_rel in summary.failed
    # In fail_fast staging failure, nothing is promoted, so no successes.
    assert summary.success == []


def test_atomic_delete_and_rename_success(tmp_path, monkeypatch):
    """
    Exercise atomic promotion branches for delete and rename (❌ lines 184–195).
    Also ensure staging loop skips delete/rename (❌ line 148) without error.
    """
    # Prepare files
    del_rel = "deleteme.txt"
    ren_from = "oldname.txt"
    ren_to = "newname.txt"
    (tmp_path / del_rel).write_text("content")
    (tmp_path / ren_from).write_text("data")

    # Patch os.rename to be a no-op when src == dst to avoid platform-specific errors
    real_rename = os.rename

    def safe_rename(src, dst):
        if os.path.samefile(src, dst):
            return
        return real_rename(src, dst)

    with patch("contextforge.commit.core.os.rename", side_effect=safe_rename):
        summary = commit_changes(
            str(tmp_path),
            [
                Change(action="delete", path=del_rel),
                Change(action="rename", path=ren_to, from_path=ren_from),
            ],
            atomic=True,
        )

    # Delete applied
    assert del_rel in summary.success
    assert not (tmp_path / del_rel).exists()
    # Rename applied (string contains 'from -> to')
    assert any(f"{ren_from} -> {ren_to}" in s for s in summary.success)
    assert not (tmp_path / ren_from).exists()
    assert (tmp_path / ren_to).exists()


def test_atomic_rollback_all_actions_on_promotion_failure(tmp_path, monkeypatch):
    """
    Create a sequence that promotes create, modify, delete, and rename,
    then fails on a final modify to trigger rollback (❌ lines 211–224).
    Verify that rollback restores prior state.
    """
    # Initial state
    (tmp_path / "b.txt").write_text("B-old")
    (tmp_path / "c.txt").write_text("C-old")
    (tmp_path / "d_old.txt").write_text("D-old")
    (tmp_path / "fail.txt").write_text("F-old")

    changes = [
        Change(action="create", path="a.txt", new_content="A-new"),
        Change(action="modify", path="b.txt", new_content="B-new", original_content="B-old"),
        Change(action="delete", path="c.txt", original_content="C-old"),
        Change(action="rename", path="d_new.txt", from_path="d_old.txt"),
        Change(action="modify", path="fail.txt", new_content="F-new", original_content="F-old"),
    ]

    # Fail only when promoting 'fail.txt'
    real_replace = os.replace

    def replace_side_effect(src, dst):
        if dst.endswith(os.path.join("", "fail.txt")):  # robust-ish check
            raise OSError("boom at promotion")
        return real_replace(src, dst)

    # Avoid issues with double-processing rename when src == dst (see normalization quirk)
    real_rename = os.rename

    def safe_rename(src, dst):
        if os.path.abspath(src) == os.path.abspath(dst):
            return
        return real_rename(src, dst)

    with patch("contextforge.commit.core.os.replace", side_effect=replace_side_effect), \
         patch("contextforge.commit.core.os.rename", side_effect=safe_rename):
        summary = commit_changes(str(tmp_path), changes, atomic=True, mode="fail_fast")

    # Should have failed on 'fail.txt' and cleared successes due to rollback.
    assert "fail.txt" in summary.failed
    assert summary.success == []

    # Filesystem rolled back:
    # - 'a.txt' (create) removed
    assert not (tmp_path / "a.txt").exists()
    # - 'b.txt' content restored
    assert (tmp_path / "b.txt").read_text() == "B-old"
    # - 'c.txt' restored (delete rollback writes original_content)
    assert (tmp_path / "c.txt").exists()
    assert (tmp_path / "c.txt").read_text() == "C-old"
    # - rename rolled back: 'd_old.txt' present, 'd_new.txt' absent
    assert (tmp_path / "d_old.txt").exists()
    assert not (tmp_path / "d_new.txt").exists()
    # - 'fail.txt' unchanged (since promotion failed before replacing)
    assert (tmp_path / "fail.txt").read_text() == "F-old"


def test_non_atomic_delete_and_rename_success(tmp_path):
    """
    Non-atomic path: exercise delete and rename branches (❌ lines 244–255).
    """
    (tmp_path / "x.txt").write_text("X")
    (tmp_path / "y_old.txt").write_text("Y")

    summary = commit_changes(
        str(tmp_path),
        [
            Change(action="delete", path="x.txt"),
            Change(action="rename", path="y_new.txt", from_path="y_old.txt"),
        ],
    )

    assert "x.txt" in summary.success
    assert not (tmp_path / "x.txt").exists()
    assert any("y_old.txt -> y_new.txt" in s for s in summary.success)
    assert not (tmp_path / "y_old.txt").exists()
    assert (tmp_path / "y_new.txt").exists()


def test_non_atomic_fail_fast_error_handling_returns_early(tmp_path, monkeypatch):
    """
    Trigger the non-atomic exception handler (❌ lines 256–261) by raising during write.
    """
    (tmp_path / "ok.txt").write_text("OK")

    # Patch open to fail only for the problematic path
    real_open = open

    def open_side_effect(path, mode="r", *args, **kwargs):
        if os.path.basename(path) == "broken.txt" and "w" in mode:
            raise OSError("write failed")
        return real_open(path, mode, *args, **kwargs)

    with patch("contextforge.commit.core.open", side_effect=open_side_effect):
        summary = commit_changes(
            str(tmp_path),
            [
                Change(action="modify", path="ok.txt", new_content="OK2"),
                Change(action="modify", path="broken.txt", new_content="NOPE"),
            ],
            mode="fail_fast",
        )

    # Fails on broken.txt and stops early. ok.txt might have been written already (no rollback here).
    assert "broken.txt" in summary.failed
    assert "write failed" in summary.errors["broken.txt"]

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_best_effort_writes_and_reports_failures(tmp_path):
    base = tmp_path
    good = Change(action="create", path="a.txt", new_content="A", original_content="")
    # Path traversal should be blocked and reported.
    bad = Change(action="create", path="../evil.txt", new_content="X", original_content="")

    summary = commit_changes(
        str(base), [good, bad], mode="best_effort", atomic=False, dry_run=False
    )
    assert "a.txt" in summary.success
    assert "../evil.txt" in summary.failed
    assert (base / "a.txt").exists()
    assert read(base / "a.txt") == "A"


def test_fail_fast_atomic_keeps_fs_unchanged_on_error(tmp_path):
    base = tmp_path
    ok = Change(action="create", path="ok.txt", new_content="OK", original_content="")
    bad = Change(action="create", path="../oops.txt", new_content="X", original_content="")

    # With atomic+fail_fast, the bad change causes an abort and no files should appear.
    summary = commit_changes(str(base), [ok, bad], mode="fail_fast", atomic=True, dry_run=False)
    assert "ok.txt" not in summary.success
    assert "../oops.txt" in summary.failed
    assert not (base / "ok.txt").exists()


def test_dry_run_reports_plan_without_writing(tmp_path):
    base = tmp_path
    ch = Change(action="create", path="plan.txt", new_content="P", original_content="")
    summary = commit_changes(str(base), [ch], dry_run=True)
    assert any("DRY RUN" in s and "plan.txt" in s for s in summary.success)
    assert not (base / "plan.txt").exists()


def test_backup_ext_writes_backup_for_existing_file(tmp_path):
    base = tmp_path
    target = base / "c.txt"
    target.write_text("OLD", encoding="utf-8")
    ch = Change(action="modify", path="c.txt", new_content="NEW", original_content="OLD")

    summary = commit_changes(str(base), [ch], atomic=False, backup_ext=".bak")
    assert "c.txt" in summary.success
    assert read(target) == "NEW"
    assert (base / "c.txt.bak").exists()
    assert read(base / "c.txt.bak") == "OLD"


def test_atomic_best_effort_promotes_what_succeeds(tmp_path, monkeypatch):
    base = tmp_path
    ok = Change(action="create", path="x.txt", new_content="X", original_content="")
    nope = Change(action="create", path="../no.txt", new_content="N", original_content="")

    summary = commit_changes(str(base), [ok, nope], mode="best_effort", atomic=True, dry_run=False)
    assert "x.txt" in summary.success
    assert "../no.txt" in summary.failed
    assert (base / "x.txt").exists()
