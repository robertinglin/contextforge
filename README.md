
# ContextForge

ContextForge is a small toolkit for:
- extracting diffs and full-file blocks from Markdown,
- and applying robust, fuzzy-aware patches to text.

## Pillars

**Extraction** — Robustly parse fenced diffs and full-file blocks from Markdown.

**Patch** — Apply unified diffs using a block-first strategy with contextual anchors and fuzzy fallbacks that preserve EOL style.

**Commit** — Safely materialize planned edits with containment checks, optional atomic promotion, and optional backups.

**Context** — Build a reproducible context bundle that includes a file tree and selected file contents with guardrails against path traversal.

## Tooling & CI

This repo includes pre-commit hooks, Ruff for linting/formatting, MyPy for types, and Pytest + Coverage. GitHub Actions runs the checks across Python 3.8–3.12.

### Local dev
```bash
python -m pip install -U pip
python -m pip install -e ".[docs]"
pre-commit install
ruff check . && ruff format --check .
mypy contextforge
pytest


## Install

```bash
pip install .
````

## Quick start

```python
from contextforge import (
  parse_markdown_string,
  patch_text,
  extract_blocks_from_text,
  plan_and_generate_changes,
  build_context,
)
```

### Apply a unified diff (with fuzzy fallback)

```python
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
```

### Parse blocks from Markdown

````python
md = """
```diff
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
````

```python
for block in extract\_blocks\_from\_text(md):
print(block\["type"], block.get("code")\[:60])

```
