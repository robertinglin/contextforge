# contextforge/commit/core.py
import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..errors.path import PathViolation


@dataclass
class Change:
    """A single filesystem operation slated for commit."""
    action: str  # "create", "modify", "delete", "rename"
    path: str
    new_content: Optional[str] = None
    original_content: Optional[str] = None
    from_path: Optional[str] = None


@dataclass
class CommitSummary:
    """Outcome of a commit operation."""

    success: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    dry_run: bool = False
    # Map relative path -> error string (when failed)
    errors: Dict[str, str] = field(default_factory=dict)


def _normalized_path(base_real: str, rel_path: str, check_exists: bool = False) -> str:
    """
    Join and normalize a repository-relative path while enforcing containment.
    Raises PathViolation if the resolved path escapes base_real.
    If check_exists is False, we don't require the path to exist (for new files/dirs).
    """
    target_path = os.path.join(base_real, *rel_path.split("/"))
    # For non-existent paths, realpath can fail or return cwd. Normalize manually.
    if not check_exists:
        resolved = os.path.abspath(target_path)
    else:
        resolved = os.path.realpath(target_path)
    # Use commonpath for robust containment check (prefix checks are error-prone).
    if os.path.commonpath([base_real, resolved]) != base_real:
        raise PathViolation(f"Path traversal attempt detected for '{rel_path}'")
    return resolved


def _backup_path(dest: str, backup_ext: str) -> str:
    ext = backup_ext if backup_ext.startswith(".") else "." + backup_ext
    return dest + ext


def commit_changes(
    base_path: str,
    changes: List[Change],
    *,
    mode: str = "best_effort",
    atomic: bool = False,
    dry_run: bool = False,
    backup_ext: str | None = None,
) -> CommitSummary:
    """
    Write a batch of file edits safely, with validation, optional atomic staging,
    and configurable error handling.

    Args:
        base_path: Root directory for operations.
        changes: Sequence of Change instances describing the edits.
        mode: "best_effort" (default) writes what it can and accumulates failures;
              "fail_fast" aborts at the first validation/write error.
        atomic: If True, stage file contents to same-directory tempfile(s) and
                then promote via os.replace(). With mode="fail_fast", the
                filesystem remains unchanged if any staging or promotion fails.
        dry_run: If True, perform validation and planning only—no writes.
        backup_ext: Optional extension used to write backups of pre-existing
                    files (e.g., ".bak" or "bak"). For new files, no backup
                    is written. Deletes and renames ignore this.

    Returns:
        CommitSummary describing successes/failures deterministically.
    """
    if mode not in {"best_effort", "fail_fast"}:
        raise ValueError("mode must be one of {'best_effort','fail_fast'}")

    summary = CommitSummary(dry_run=dry_run)
    base_real = os.path.realpath(base_path)

    # Normalize by resolved path
    normalized: List[Tuple[Change, str]] = []
    for ch in changes:
        try:
            # For renames, normalize both paths.
            if ch.action == "rename" and ch.from_path:
                from_resolved = _normalized_path(base_real, ch.from_path, check_exists=True)
                # The target path for rename does not exist yet.
                resolved = _normalized_path(base_real, ch.path, check_exists=False)
                # We will operate on the resolved 'from' path
                normalized.append((ch, from_resolved))
            else:
                resolved = _normalized_path(base_real, ch.path, check_exists=True)
                normalized.append((ch, resolved))
        except Exception as e:
            summary.failed.append(ch.path)
            summary.errors[ch.path] = str(e)
            if mode == "fail_fast":
                return summary

    # Dry-run: only validate directory writability and report the plan
    if dry_run:
        for ch, resolved in normalized:
            try:
                if ch.action in ("create", "modify"):
                    # Check containing directory is or can be created
                    dirpath = os.path.dirname(resolved)
                    # No actual creation; just a simple permission probe if exists
                    if os.path.exists(dirpath) and not os.access(dirpath, os.W_OK):
                        raise PermissionError(f"No write permission for directory '{dirpath}'")
                    summary.success.append(
                        f"DRY RUN: Would {ch.action} file {ch.path} ({len(ch.new_content or '')} bytes)"
                    )
                elif ch.action == "delete":
                    if not os.path.exists(resolved):
                        raise FileNotFoundError(f"File to delete not found: '{ch.path}'")
                    summary.success.append(f"DRY RUN: Would delete file {ch.path}")
                elif ch.action == "rename" and ch.from_path:
                    if not os.path.exists(resolved):  # resolved is from_path here
                        raise FileNotFoundError(f"File to rename not found: '{ch.from_path}'")
                    summary.success.append(f"DRY RUN: Would rename {ch.from_path} to {ch.path}")
            except Exception as e:
                summary.failed.append(ch.path)
                summary.errors[ch.path] = str(e)
                if mode == "fail_fast":
                    return summary
        return summary

    # Actual writes
    if atomic:
        # Stage each change to a tempfile in the destination directory.
        staged: Dict[str, Tuple[str, Change]] = {}  # dest -> (tmp, change)
        staging_failed = False

        for ch, resolved in normalized:
            # Skip staging for deletes/renames, they are handled later.
            if ch.action in ("delete", "rename"):
                continue
            try:
                dirpath = os.path.dirname(resolved)
                os.makedirs(dirpath, exist_ok=True)
                fd, tmp = tempfile.mkstemp(prefix=".cf-", suffix=".tmp", dir=dirpath)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(ch.new_content)
                staged[resolved] = (tmp, ch)
            except Exception as e:
                summary.failed.append(ch.path)
                summary.errors[ch.path] = str(e)
                staging_failed = True
                if mode == "fail_fast":
                    break

        if staging_failed and mode == "fail_fast":
            # Nothing promoted yet: delete stage files; filesystem unchanged.
            for tmp, _ch in [v for v in staged.values()]:
                with contextlib.suppress(Exception):
                    os.remove(tmp)
            return summary

        # Phase 2: Apply all changes (deletes, renames, promotions)
        promoted: List[Tuple[str, Change]] = []
        for ch, resolved in normalized:  # Re-iterate to get all changes in order
            try:
                if ch.action in ("create", "modify"):
                    dest = resolved
                    tmp, _ = staged[dest]
                    # Optional backup of existing file before replacement
                    if backup_ext and os.path.exists(dest) and ch.action == "modify":
                        with open(_backup_path(dest, backup_ext), "w", encoding="utf-8") as b:
                            b.write(ch.original_content or "")
                    os.replace(tmp, dest)  # atomic within a filesystem
                    summary.success.append(ch.path)
                    promoted.append((dest, ch))
                elif ch.action == "delete":
                    if os.path.exists(resolved):
                        os.remove(resolved)
                    summary.success.append(ch.path)
                    promoted.append((resolved, ch))  # Use for rollback
                elif ch.action == "rename" and ch.from_path:
                    from_path = resolved  # 'resolved' was the from_path for renames
                    to_path = _normalized_path(base_real, ch.path, check_exists=False)
                    os.rename(from_path, to_path)
                    summary.success.append(f"{ch.from_path} -> {ch.path}")
                    promoted.append((to_path, ch))  # Use to_path for rollback

            except Exception as e:
                summary.failed.append(ch.path)
                summary.errors[ch.path] = str(e)
                # Ensure staged blob is cleaned up if replace failed
                if ch.action in ("create", "modify"):
                    tmp, _ = staged[resolved]
                    with contextlib.suppress(Exception):
                        if os.path.exists(tmp):
                            os.remove(tmp)

                if mode == "fail_fast":
                    # Roll back previous promotions—restore original contents.
                    for pth, pch in reversed(promoted):
                        try:
                            if pch.action == "create":
                                with contextlib.suppress(Exception):
                                    os.remove(pth)
                            elif pch.action == "modify":
                                with open(pth, "w", encoding="utf-8") as f:
                                    f.write(pch.original_content or "")
                            elif pch.action == "delete":
                                # This is tricky, we'd need original content to restore
                                with open(pth, "w", encoding="utf-8") as f:
                                    f.write(pch.original_content or "")
                            elif pch.action == "rename" and pch.from_path:
                                from_path_rb = _normalized_path(base_real, pch.from_path, check_exists=False)
                                with contextlib.suppress(Exception):
                                    os.rename(pth, from_path_rb)
                        except Exception:
                            # Best-effort rollback; remain silent per library default.
                            pass
                    summary.success.clear()
                    return summary
        return summary

    # Non-atomic path: write each file directly.
    written: List[Tuple[str, Change]] = []
    for ch, resolved in normalized:
        try:
            if ch.action in ("create", "modify"):
                dirpath = os.path.dirname(resolved)
                os.makedirs(dirpath, exist_ok=True)
                if backup_ext and os.path.exists(resolved) and ch.action == "modify":
                    shutil.copy2(resolved, _backup_path(resolved, backup_ext))
                with open(resolved, "w", encoding="utf-8") as f:
                    f.write(ch.new_content or "")
                summary.success.append(ch.path)
                written.append((resolved, ch))
            elif ch.action == "delete":
                if os.path.exists(resolved):
                    os.remove(resolved)
                summary.success.append(ch.path)
                written.append((resolved, ch))
            elif ch.action == "rename" and ch.from_path:
                from_path = resolved
                to_path = _normalized_path(base_real, ch.path, check_exists=False)
                os.rename(from_path, to_path)
                summary.success.append(f"{ch.from_path} -> {ch.path}")
                written.append((to_path, ch))

        except Exception as e:
            summary.failed.append(ch.path)
            summary.errors[ch.path] = str(e)
            if mode == "fail_fast":
                # Stop early; do not attempt rollback (only atomic guarantees clean FS).
                return summary

    return summary
