# contextforge/commit/core.py
import os
from dataclasses import dataclass, field
from typing import List

@dataclass
class Change:
    path: str
    new_content: str
    original_content: str
    is_new: bool

@dataclass
class CommitSummary:
    success: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    dry_run: bool = False

def commit_changes(base_path: str, changes: List[Change], *, dry_run: bool = False) -> CommitSummary:
    """
    Safely writes a list of changes to the filesystem.

    Args:
        base_path: The root directory for all file operations.
        changes: A list of Change objects to apply.
        dry_run: If True, simulates the changes without writing to disk.

    Returns:
        A CommitSummary detailing the outcome of the operation.
    """
    summary = CommitSummary(dry_run=dry_run)
    base_real_path = os.path.realpath(base_path)

    for change in changes:
        try:
            # --- Security Check: Prevent path traversal ---
            target_path = os.path.join(base_real_path, *change.path.split('/'))
            resolved_path = os.path.realpath(target_path)

            # Ensure the final path is within the base directory
            if not resolved_path.startswith(base_real_path):
                raise PermissionError(f"Path traversal attempt detected for '{change.path}'")

            if dry_run:
                action = "create" if change.is_new else "write"
                summary.success.append(f"DRY RUN: Would {action} {len(change.new_content)} bytes to {change.path}")
                continue

            # --- Actual File Write ---
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(change.new_content)
            summary.success.append(f"Successfully wrote changes to {change.path}")

        except Exception as e:
            summary.failed.append(f"Failed to write to {change.path}: {e}")

    return summary