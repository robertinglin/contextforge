# ContextForge

Reliable, context-aware helpers for:
- **Extraction** — Parse fenced diffs & full-file blocks from Markdown.
- **Patch** — Apply unified diffs with fuzzy/context fallbacks (EOL-preserving).
- **Commit** — Safely write planned edits with path-containment checks.
- **Context** — Build a reproducible “bundle” (file tree + files) with guardrails.

> This README shows the recommended, **root-level imports** that are now exported by `contextforge`. See the API section for details.

---

## Install

```bash
pip install .
````

For development:

```bash
python -m pip install -U pip
python -m pip install -e ".[docs]"
pre-commit install
ruff check . && ruff format --check .
mypy contextforge
pytest
```

---

## Quick start

```python
from contextforge import (
    # Parsing & planning
    parse_markdown_string,  # -> generator of normalized “blocks”
    plan_and_generate_changes,  # -> list of planned file edits (dicts)

    # Patching
    patch_text,

    # Extraction utilities
    extract_blocks_from_text,

    # Context bundle
    build_context,
)
```

### 1) Apply a unified diff (with fuzzy fallback)

```python
from contextforge import patch_text

original = "one\nalpha\nbeta\ngamma\nend\n"
patch = """@@ -1,5 +1,5 @@
 one
 alpha
-beta
+BETA
 gamma
 end
"""

print(patch_text(original, patch))
# -> "one\nalpha\nBETA\ngamma\nend\n"
```

### 2) Parse blocks from Markdown

````python
from contextforge import extract_blocks_from_text

md = """
```diff
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
````

"""

for block in extract\_blocks\_from\_text(md):
print(block\["type"], block.get("file\_path"), block.get("language"))

# e.g.: ("diff", "file.txt", "diff")

````

### 3) End-to-end: Parse → Plan → Commit

```python
import os
from types import SimpleNamespace
from contextforge import (
    parse_markdown_string, plan_and_generate_changes,
    commit_changes, build_context
)
from contextforge.commit.core import Change  # dataclass for committing

# (A) Markdown input produced by a model or a user:
md = r"""
Here are the edits:

```diff
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 print("hello")
+print("world")
````

```python
# File: utils/math.py
def add(a, b):
    return a + b
```

"""

# (B) Parse into normalized blocks (diffs or whole files), with best-effort file path detection:

blocks = list(parse\_markdown\_string(md))

# (C) Build a simple plan by using any pre-classification (file path + change type) the parser found:

planned = \[]
for b in blocks:
meta = b.get("pre\_classification") or b.get("synthetic\_info")
if not meta:
continue
planned.append({"metadata": meta, "block": b})

# (D) Materialize edits offline (no writes yet): apply diffs / prepare final file contents

repo\_dir = os.getcwd()
file\_edits = plan\_and\_generate\_changes(planned, repo\_dir)

# file\_edits -> list of dicts with keys: file\_path, new\_content, original\_content, is\_new, block\_id, ...

# (E) Commit to disk safely

changes = \[
Change(
path=fe\["file\_path"],
new\_content=fe\["new\_content"],
original\_content=fe\["original\_content"],
is\_new=fe\["is\_new"],
)
for fe in file\_edits
]

summary = commit\_changes(
repo\_dir,
changes,
mode="best\_effort",  # or "fail\_fast"
atomic=True,         # stage + atomic promote via os.replace
backup\_ext=".bak",   # write backups for pre-existing files
)
print(summary.success, summary.failed)

````

### 4) Build a “context bundle” (tree + files + instructions)

`build_context` expects a small request object with:

- `include_file_tree: bool`
- `base_path: str` (project root)
- `files: list[str]` (project-relative paths to include)
- `instructions: str` (footer text)

You can pass a light object like a `SimpleNamespace`:

```python
from types import SimpleNamespace
from contextforge import build_context

req = SimpleNamespace(
    include_file_tree=True,
    base_path=".",
    files=["README.md", "pyproject.toml", "contextforge/__init__.py"],
    instructions="Do X, then Y.",
)
bundle = build_context(req)
print(bundle[:400])  # contains <file_tree>, <file_contents>, and <user_instructions> sections
````

---

## API reference (top-level)

```python
from contextforge import (
  # Context
  build_context,

  # Markdown → blocks → edits
  parse_markdown_string,
  plan_and_generate_changes,

  # Extraction helpers
  extract_blocks_from_text,
  extract_diffs_from_text,
  extract_file_blocks_from_text,
  extract_file_info_from_context_and_code,
  detect_new_files,

  # Patch / Commit
  patch_text,
  commit_changes,

  # System helpers
  append_context, copy_to_clipboard, write_tempfile,

  # Errors
  PatchFailedError, ExtractError, ContextError, CommitError, PathViolation,
)
```

**Notes & gotchas**

* `parse_markdown_string` normalizes fenced blocks and tries to infer `file_path` + `change_type` for each block (diff vs full replacement). Use its `pre_classification` (or `synthetic_info` for diffs) to seed your planning.&#x20;
* `plan_and_generate_changes` uses the standard `patch` library first, then falls back to `patch_text` for robust fuzzy application. It returns a list of ready-to-commit dicts; convert them to `contextforge.commit.core.Change` for `commit_changes`.&#x20;
* `commit_changes` enforces path containment (prevents `../` escapes), supports atomic promotion, and optional backups. &#x20;
* `build_context` renders the file tree honoring `.gitignore`, includes requested files, and appends your instructions section; paths outside `base_path` are replaced with a clear security message.&#x20;

---

## Logging

Library code is silent by default. Opt into debug logs (e.g., in `patch_text`) by passing `log=True` **or** providing your own logger via `contextforge._logging.resolve_logger`. See `README-LOGGING.md` for details.&#x20;

---

## Testing

The repo ships with a comprehensive test suite (patching heuristics, context containment, extractors, and system helpers). Run `pytest`.&#x20;

