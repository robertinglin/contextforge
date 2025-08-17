
# ContextForge

ContextForge is a small toolkit for:
- extracting diffs and full-file blocks from Markdown,
- and applying robust, fuzzy-aware patches to text.

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
