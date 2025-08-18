"""
Convenience system helpers for working with context strings.

Public API:
  - append_context(existing: str, more: str, *, header: str | None = None, sep: str = "\n\n") -> str
  - copy_to_clipboard(text: str) -> bool
  - write_tempfile(text: str, *, suffix: str = ".txt", prefix: str = "contextforge-", dir: str | None = None, encoding: str = "utf-8") -> str
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile

__all__ = ["append_context", "copy_to_clipboard", "write_tempfile"]


def append_context(existing: str, more: str, *, header: str | None = None, sep: str = "\n\n") -> str:
    """
    Append `more` to an existing context string with a clean separator.
    Optionally insert a Markdown header above the appended chunk.
    """
    out = existing.rstrip("\n") + "\n"
    if header:
        out += f"{sep}{header.strip()}\n{sep}"
    else:
        out += sep
    if not out.endswith("\n"):
        out += "\n"
    out += more.rstrip("\n") + "\n"
    return out


def copy_to_clipboard(text: str) -> bool:
    """
    Copy `text` to the system clipboard using best-effort, cross-platform fallbacks.
    Returns True on apparent success, False otherwise.
    """
    try:
        # macOS
        if _which("pbcopy"):
            proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            return proc.returncode == 0
        # Windows
        if _which("clip"):
            proc = subprocess.run(["clip"], input=text.encode("utf-8"), shell=True, check=False)
            return proc.returncode == 0
        # Linux/BSD: try Wayland then X11
        if _which("wl-copy"):
            proc = subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=False)
            return proc.returncode == 0
        if _which("xclip"):
            proc = subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=False)
            return proc.returncode == 0
        if _which("xsel"):
            proc = subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode("utf-8"), check=False)
            return proc.returncode == 0
    except Exception:
        pass
    return False


def write_tempfile(
    text: str,
    *,
    suffix: str = ".txt",
    prefix: str = "contextforge-",
    dir: str | None = None,
    encoding: str = "utf-8",
) -> str:
    """
    Write `text` to a new temporary file and return the absolute file path.
    The file is created with delete=False so it persists after the call.
    """
    # Ensure suffix begins with a dot for readability
    if suffix and not suffix.startswith("."):
        suffix = f".{suffix}"
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=dir)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
    except Exception:
        # If writing fails, clean up the on-disk handle.
        with contextlib.suppress(Exception):
            os.remove(path)
        raise
    return os.path.realpath(path)


def _which(cmd: str) -> bool:
    """Minimal shutil.which to avoid import overhead."""
    paths = os.environ.get("PATH", "").split(os.pathsep)
    exts = [""]
    if os.name == "nt":
        pathext = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";")
        exts = [e.lower() for e in pathext if e]
    for folder in paths:
        full = os.path.join(folder, cmd)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return True
        # Windows: try with PATHEXT
        for e in exts:
            full_ext = full + e
            if os.path.isfile(full_ext) and os.access(full_ext, os.X_OK):
                return True
    return False
