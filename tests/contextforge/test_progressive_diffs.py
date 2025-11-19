

"""
Progressive, end-to-end tests for ContextForge's diff extraction, block extraction,
fuzzy patching, planning, and commit preparation.

This suite is intentionally comprehensive and incremental. It validates:

- Diff extraction:
  - Fenced ```diff blocks
  - Multi-file diffs
  - Raw unfenced diffs
  - Custom "*** Begin Patch" blocks
  - Ambiguous/nested fences
  - Mixed markdown with prose + code + diffs

- Block extraction and classification:
  - File vs diff vs rename vs delete blocks
  - File path inference from headers, context, and comment hints
  - Multi-file diff splitting
  - Deduplication semantics (latest block per path wins)

- Patching (patch_text, fuzzy_patch_partial):
  - Standard unified diffs
  - Simplified hunks using "@@" only
  - Multiple hunks per file
  - Pure additions / deletions
  - Non-contiguous additions
  - Duplicate anchors and fuzzy context matching
  - Fuzzy matching across indentation and minor drift
  - EOL (LF/CRLF) preservation
  - Structured patch mode (list-of-dicts with old/new and regex)
  - Malformed diff failure behavior
  - Guard rails for partial deletes
  - Merge conflict generation when a hunk is bounded by perfect hunks
  - Stability under many small hunks

- High-level orchestration:
  - parse_markdown_string producing enriched blocks
  - plan_and_generate_changes building Change objects with correct metadata
"""

from __future__ import annotations

import os
import textwrap
import tempfile
from typing import List, Dict, Any

import pytest

from contextforge import (
    patch_text,
    fuzzy_patch_partial,  
    extract_blocks_from_text,
    extract_diffs_from_text,
    extract_file_info_from_context_and_code,
    detect_new_files,
    parse_markdown_string,
    plan_and_generate_changes,
)
from contextforge.commit import Change, commit_changes
from contextforge.errors import PatchFailedError
from contextforge.extract import metadata as meta_mod


# ---------------------------------------------------------------------------
# Level 1: Basic single-hunk unified diff (sanity check)
# ---------------------------------------------------------------------------


def test_level_1_basic_single_hunk_diff():
    original = "line1\nline2\nline3\n"
    diff = """\
```diff
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 line1
-line2
+LINE2
 line3
```
"""
    blocks = extract_diffs_from_text(diff)
    assert len(blocks) == 1
    assert blocks[0]["file_path"] == "file.txt" or blocks[0]["file_path"].endswith("file.txt")
    code = blocks[0]["code"]

    result = patch_text(original, code)
    assert result == "line1\nLINE2\nline3\n"


# ---------------------------------------------------------------------------
# Level 2: Multiple hunks in a single file, fenced diff
# ---------------------------------------------------------------------------


def test_level_2_multi_hunk_single_file():
    original = textwrap.dedent(
        """
        alpha
        beta
        gamma
        delta
        epsilon
        """
    ).lstrip()
    diff = """\
```diff
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
-alpha
+ALPHA
 beta
@@ -3,5 +3,5 @@
 gamma
-delta
+DELTA
 epsilon
```
"""
    blocks = extract_diffs_from_text(diff)
    assert len(blocks) == 1
    code = blocks[0]["code"]

    patched = patch_text(original, code)
    expected = textwrap.dedent(
        """
        ALPHA
        beta
        gamma
        DELTA
        epsilon
        """
    ).lstrip()
    assert patched == expected


# ---------------------------------------------------------------------------
# Level 3: Multi-file diff with per-file splitting
# ---------------------------------------------------------------------------


def test_level_3_multi_file_diff_split_and_patch():
    original_a = "one\ntwo\n"
    original_b = "cat\ndog\n"
    multi = """\
```diff
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
-one
+ONE
 two
diff --git a/b.txt b/b.txt
--- a/b.txt
+++ b/b.txt
@@ -1,2 +1,2 @@
-cat
+DOG
 dog
```
"""
    blocks = extract_diffs_from_text(multi)
    assert len(blocks) == 2

    a_block = next(b for b in blocks if b["file_path"].endswith("a.txt"))
    b_block = next(b for b in blocks if b["file_path"].endswith("b.txt"))

    patched_a = patch_text(original_a, a_block["code"])
    patched_b = patch_text(original_b, b_block["code"])

    assert patched_a == "ONE\ntwo\n"
    assert patched_b == "DOG\ndog\n"


# ---------------------------------------------------------------------------
# Level 4: Simplified "@@" hunks without headers and indented content
# ---------------------------------------------------------------------------


def test_level_4_simplified_indented_hunks():
    original = "hello\nworld\n"
    patch = textwrap.dedent(
        """
        Some commentary above

        @@
        - hello
        +HELLO

        End.
        """
    )
    blocks = extract_blocks_from_text(patch)
    assert any(b["type"] == "diff" for b in blocks)

    diff_block = next(b for b in blocks if b["type"] == "diff")
    code = diff_block["code"]

    result = patch_text(original, code)
    assert result == "HELLO\nworld\n"


# ---------------------------------------------------------------------------
# Level 5: Duplicate anchors and context scoring
# ---------------------------------------------------------------------------


def test_level_5_duplicate_anchors_context_sensitive():
    original = "start\ntarget\ntarget\nafter\nend\n"
    patch_body = textwrap.dedent(
        """
        @@ -1,5 +1,4 @@
         start
        -target
         target
         after
         end
        """
    ).strip()

    out = patch_text(original, patch_body)
    assert out == "start\ntarget\nafter\nend\n"


# ---------------------------------------------------------------------------
# Level 6: Pure additions with context before and after
# ---------------------------------------------------------------------------


def test_level_6_pure_addition_with_context():
    original = "a\nb\nd\ne\n"
    patch_body = textwrap.dedent(
        """
        @@ -0,0 +3,1 @@
        +c
        """
    ).strip()

    result = patch_text(original, patch_body)
    assert result == "a\nb\nc\nd\ne\n"


# ---------------------------------------------------------------------------
# Level 7: Custom "*** Begin Patch" blocks with file hints
# ---------------------------------------------------------------------------


def test_level_7_custom_begin_patch_format():
    original = "old = 1\n"
    content = textwrap.dedent(
        """
        Intro text

        *** Begin Patch
        *** Update File: src/example.py
        @@
        -old = 1
        +old = 2
        *** End Patch

        Outro text
        """
    )
    blocks = extract_diffs_from_text(content)
    assert len(blocks) == 1
    blk = blocks[0]
    assert blk["file_path"] == "src/example.py"
    code = blk["code"]
    patched = patch_text(original, code)
    assert patched == "old = 2\n"


# ---------------------------------------------------------------------------
# Level 8: Raw unfenced diff as entire document
# ---------------------------------------------------------------------------


def test_level_8_raw_unfenced_diff_detection_and_patch():
    original = "x\ny\n"
    raw = textwrap.dedent(
        """
        --- a/test.txt
        +++ b/test.txt
        @@ -1,2 +1,2 @@
        -x
        +X
         y
        """
    ).strip()
    blocks = extract_diffs_from_text(raw)
    assert len(blocks) == 1
    code = blocks[0]["code"]

    patched = patch_text(original, code)
    assert patched == "X\ny\n"


# ---------------------------------------------------------------------------
# Level 9: Non-contiguous additions and multi-hunk assignment
# ---------------------------------------------------------------------------


def test_level_9_non_contiguous_additions_and_assignment():
    original = textwrap.dedent(
        """
        header
        keep
        keep
        footer
        """
    ).lstrip()

    patch_body = textwrap.dedent(
        """
        @@
         header
        +added-one
         keep
         keep
        +added-two
         footer
        """
    ).strip()

    out = patch_text(original, patch_body)
    assert "header" in out
    assert "added-one" in out
    assert "added-two" in out
    assert "footer" in out


# ---------------------------------------------------------------------------
# Level 10: Fuzzy patch with slightly drifted context
# ---------------------------------------------------------------------------


def test_level_10_fuzzy_with_drifted_context():
    original = textwrap.dedent(
        """
        function greet() {
            console.log("hello");
        }

        function bye() {
            console.log("bye");
        }
        """
    ).lstrip()

    patch_body = textwrap.dedent(
        """
        @@ -1,4 +1,4 @@
-       function greet() {
-           console.log("hello");
-       }
+       function greet() {
+           console.log("HELLO");
+       }
        """
    ).strip()

    out = patch_text(original, patch_body)
    assert 'console.log("HELLO");' in out
    assert "function bye()" in out


# ---------------------------------------------------------------------------
# Level 11: Merge-conflict generation when bounded by perfect hunks
# ---------------------------------------------------------------------------


def test_level_11_merge_conflict_generation():
    content = textwrap.dedent(
        """
        start
        alpha
        beta
        gamma
        end
        """
    ).strip()

    patch = textwrap.dedent(
        """
        @@ -1,1 +1,1 @@
        -start
        +START
        @@ -2,1 +2,1 @@
        -nonexistent
        +REPLACED
        @@ -5,1 +5,1 @@
        -end
        +END
        """
    ).strip()

    out = patch_text(content, patch)
    assert "START" in out
    assert "END" in out
    assert "<<<<<<< CURRENT" in out
    assert "=======" in out
    assert ">>>>>>> PATCH" in out


# ---------------------------------------------------------------------------
# Level 12: EOL preservation (LF vs CRLF, trailing newline)
# ---------------------------------------------------------------------------


def test_level_12_eol_preservation_crlf_and_lf():
    crlf_original = "a\r\nb\r\n"
    crlf_patch = "@@\r\n-b\r\n+B\r\n"
    out = patch_text(crlf_original, crlf_patch)
    assert out == "a\r\nB\r\n"

    lf_original = "a\nb"
    lf_patch = "@@\n-b\n+B\n"
    out2 = patch_text(lf_original, lf_patch)
    assert out2 == "a\nB"


# ---------------------------------------------------------------------------
# Level 13: Structured patch (list of dict) sentinel behavior
# ---------------------------------------------------------------------------


def test_level_13_structured_patch_sentinel_and_regex():
    original = "prefix ABC MIDDLE XYZ suffix"
    old = "prefix ABC WRONG XYZ suffix"
    new = "prefix ABC RIGHT XYZ suffix"

    out = patch_text(original, [dict(old=old, new=new)])
    assert out == "prefix ABC RIGHT XYZ suffix"

    original2 = "value=foo other"
    out2 = patch_text(original2, [dict(pattern=r"value=foo", new="value=bar")])
    assert out2 == "value=bar other"

    with pytest.raises(PatchFailedError):
        patch_text("x", [dict(new="y")])


# ---------------------------------------------------------------------------
# Level 14: Structured patch regex and error behavior
# ---------------------------------------------------------------------------


def test_level_14_structured_patch_regex_ambiguity_and_missing():
    text = "match\nmatch\n"
    with pytest.raises(PatchFailedError):
        patch_text(text, [dict(pattern=r"doesnotexist", new="x")])

    # Successful regex with multiple potential matches: we only replace first by design
    text2 = "foo=1\nfoo=1\n"
    out = patch_text(text2, [dict(pattern=r"foo=1", new="foo=2")])
    assert out == "foo=2\nfoo=1\n"


# ---------------------------------------------------------------------------
# Level 15: Complex markdown with nested fences and diff + file blocks
# ---------------------------------------------------------------------------


def test_level_15_complex_markdown_nested_and_mixed_blocks():
    markdown = textwrap.dedent(
        """
        Intro prose.

        ```python
        # app.py
        print("hello")
        ```

        Some explanation.

        ```diff
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,1 @@
        -print("hello")
        +print("hello world")
        ```

        More prose.

        ```md
        # README.md
        Some docs
        ```

        ```diff
        --- a/README.md
        +++ b/README.md
        @@ -1,2 +1,3 @@
         # README.md
         Some docs
        +More docs
        ```
        """
    )

    blocks = extract_blocks_from_text(markdown)
    types = [b["type"] for b in blocks]
    assert "file" in types
    assert "diff" in types

    app_file = next(b for b in blocks if b["type"] == "file" and b.get("file_path", "").endswith("app.py"))
    app_diff = next(b for b in blocks if b["type"] == "diff" and b.get("file_path", "").endswith("app.py"))

    readme_file = next(
        b for b in blocks if b["type"] == "file" and b.get("file_path", "").lower().endswith("readme.md")
    )
    readme_diff = next(
        b for b in blocks if b["type"] == "diff" and b.get("file_path", "").lower().endswith("readme.md")
    )

    app_patched = patch_text(app_file["code"], app_diff["code"])
    readme_patched = patch_text(readme_file["code"], readme_diff["code"])

    assert 'print("hello world")' in app_patched
    assert "More docs" in readme_patched


# ---------------------------------------------------------------------------
# Level 16: Guard rails - malformed diff should raise PatchFailedError
# ---------------------------------------------------------------------------


def test_level_16_malformed_diff_raises():
    original = "x\n"
    bad = "--- a/file\n+++ b/file\n"
    with pytest.raises(PatchFailedError):
        patch_text(original, bad)


# ---------------------------------------------------------------------------
# Level 17: Fuzzy patch with partial deletion / guarded delete lines
# ---------------------------------------------------------------------------


def test_level_17_guarded_delete_partial_match():
    original = "p\nq\nr\n"
    patch_body = textwrap.dedent(
        """
        @@ -1,3 +1,4 @@
         p
         q
        -miss
         r
        +s
        """
    ).strip()

    out = patch_text(original, patch_body)
    # We keep original lines (no 'miss') and still add 's'
    assert out == "p\nq\nr\ns\n"


# ---------------------------------------------------------------------------
# Level 18: Stress test with many small hunks and mixed contexts
# ---------------------------------------------------------------------------


def test_level_18_many_small_hunks_stress():
    original_lines = [f"line {i}" for i in range(1, 51)]
    original = "\n".join(original_lines) + "\n"

    hunks = []
    hunks.append("@@ -5,1 +5,1 @@\n-line 5\n+LINE 5\n")
    hunks.append("@@ -10,1 +10,0 @@\n-line 10\n")
    hunks.append("@@ -20,1 +20,1 @@\n-line 20\n+LINE 20\n")
    hunks.append("@@ -0,0 +55,2 @@\n+extra A\n+extra B\n")
    diff = "\n".join(hunks)

    out = patch_text(original, diff)
    assert "LINE 5" in out
    assert "LINE 20" in out
    assert "line 10" not in out
    assert out.endswith("extra A\nextra B\n")


# ---------------------------------------------------------------------------
# Level 19: Interplay of extract_diffs_from_text with ambiguous fences
# ---------------------------------------------------------------------------


def test_level_19_ambiguous_fences_and_diff_extraction():
    text = textwrap.dedent(
        """
        ```python
        code = "````"
        ```
        ```diff
        --- a/x
        +++ b/x
        @@ -1,1 +1,1 @@
        -a
        +b
        ```
        """
    )

    blocks = extract_diffs_from_text(text)
    assert len(blocks) == 1
    assert "--- a/x" in blocks[0]["code"]
    assert "+b" in blocks[0]["code"]


# ---------------------------------------------------------------------------
# Level 20: End-to-end "LLM-style" mixed answer with explanations + patches
# ---------------------------------------------------------------------------


def test_level_20_llm_style_mixed_answer_end_to_end():
    original = "console.log('old');\n"
    llm_answer = textwrap.dedent(
        """
        Here's what I'll change:

        1. Update logging line.

        ```diff
        --- a/src/main.js
        +++ b/src/main.js
        @@ -1,1 +1,1 @@
        -console.log('old');
        +console.log('new');
        ```
        """
    )

    blocks = extract_blocks_from_text(llm_answer)
    diff_blocks = [b for b in blocks if b["type"] == "diff"]
    assert len(diff_blocks) == 1
    diff_block = diff_blocks[0]
    assert diff_block["file_path"].endswith("src/main.js")

    patched = patch_text(original, diff_block["code"])
    assert "console.log('new');" in patched


# ---------------------------------------------------------------------------
# Level 21: extract_file_info_from_context_and_code heuristics
# ---------------------------------------------------------------------------


def test_level_21_extract_file_info_heuristics_diff_and_full_file():
    ctx = "We will modify `src/app.py` as follows:"
    diff = textwrap.dedent(
        """
        --- a/src/app.py
        +++ b/src/app.py
        @@ -1,1 +1,1 @@
        -print("hi")
        +print("hello")
        """
    ).strip()
    info = extract_file_info_from_context_and_code(ctx, diff, "diff")
    assert info is not None
    assert info["change_type"] == "diff"
    assert "src/app.py" in info["file_path"]

    ctx2 = "Here is the full content of `foo/bar.txt`:"
    code2 = "line1\nline2\n"
    info2 = extract_file_info_from_context_and_code(ctx2, code2, "text")
    assert info2 is not None
    assert info2["change_type"] == "full_replacement"
    assert info2["file_path"].endswith("bar.txt")


# ---------------------------------------------------------------------------
# Level 22: detect_new_files from various diff styles
# ---------------------------------------------------------------------------


def test_level_22_detect_new_files_modes():
    content = textwrap.dedent(
        """
        ```diff
        diff --git a/none b/new1.txt
        new file mode 100644
        index 0000000..e69de29
        --- /dev/null
        +++ b/new1.txt
        ```
        ```diff
        --- /dev/null
        +++ b/new2.txt
        @@ -0,0 +1,2 @@
        +hello
        +world
        ```
        """
    )
    new_files = detect_new_files(content)
    assert "new1.txt" in new_files or "b/new1.txt" in new_files
    assert "new2.txt" in new_files or "b/new2.txt" in new_files


# ---------------------------------------------------------------------------
# Level 23: parse_markdown_string enrichment and filtering semantics
# ---------------------------------------------------------------------------


def test_level_23_parse_markdown_string_enrichment():
    markdown = textwrap.dedent(
        """
        Text

        ```python
        # file_a.py
        x = 1
        ```

        ```diff
        --- a/file_a.py
        +++ b/file_a.py
        @@ -1,1 +1,1 @@
        -x = 1
        +x = 2
        ```
        """
    )
    blocks = list(parse_markdown_string(markdown))
    assert len(blocks) == 2
    types = {b["type"] for b in blocks}
    assert types == {"file", "diff"}

    file_block = next(b for b in blocks if b["type"] == "file")
    assert file_block["is_pre_classified"]
    assert file_block["pre_classification"]["file_path"].endswith("file_a.py")
    assert file_block["pre_classification"]["change_type"] == "full_replacement"

    diff_block = next(b for b in blocks if b["type"] == "diff")
    assert diff_block["lang"] == "diff"


# ---------------------------------------------------------------------------
# Level 24: plan_and_generate_changes with file, diff, rename, delete
# ---------------------------------------------------------------------------


def test_level_24_plan_and_generate_changes_integration(tmp_path: Any = None):
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
    else:
        tmp_path = str(tmp_path)

    app_path = os.path.join(tmp_path, "app.py")
    readme_path = os.path.join(tmp_path, "README.md")
    os.makedirs(os.path.dirname(app_path), exist_ok=True)

    with open(app_path, "w", encoding="utf-8") as f:
        f.write("print('old')\n")

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("# Title\n")

    markdown = textwrap.dedent(
        """
        ```diff
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,1 @@
        -print('old')
        +print('new')
        ```

        ```diff
        rename from README.md
        rename to docs/README.md
        ```

        ```diff
        --- a/unused.txt
        +++ /dev/null
        @@ -1,1 +0,0 @@
        -gone
        ```
        """
    )

    blocks = extract_blocks_from_text(markdown)

    planned: List[Dict[str, Any]] = []
    for b in blocks:
        if b["type"] == "diff" and "rename from" in b["code"]:
            info = meta_mod.detect_rename_from_diff(b["code"])
            assert info
            planned.append({"metadata": {"change_type": "rename", **info}, "block": b})
        elif b["type"] == "diff" and "unused.txt" in b["code"]:
            deleted = meta_mod.detect_deletion_from_diff(b["code"])
            assert deleted == "unused.txt"
            planned.append({"metadata": {"change_type": "delete", "file_path": deleted}, "block": b})
        elif b["type"] == "diff":
            planned.append(
                {
                    "metadata": {
                        "change_type": "diff",
                        "file_path": "app.py",
                    },
                    "block": b,
                }
            )

    changes = plan_and_generate_changes(planned, tmp_path)

    # Expected: modify app.py, rename README.md -> docs/README.md, delete unused.txt
    actions = [c.action for c in changes]
    paths = [c.path for c in changes]

    assert "modify" in actions or "create" in actions
    assert "rename" in actions
    assert "delete" in actions
    assert any(p.endswith("app.py") for p in paths)
    assert any(p.endswith("docs/README.md") for p in paths)
    assert any(p.endswith("unused.txt") for p in paths)


# ---------------------------------------------------------------------------
# Level 25: commit_changes basic integration (non-atomic)
# ---------------------------------------------------------------------------


def test_level_25_commit_changes_basic(tmp_path: Any = None):
    if tmp_path is None:
        tmp = tempfile.TemporaryDirectory()
        base = tmp.name
    else:
        base = str(tmp_path)

    file1 = "a.txt"
    file2 = "b.txt"
    os.makedirs(base, exist_ok=True)

    changes: List[Change] = [
        Change(action="create", path=file1, new_content="hello\n"),
        Change(action="create", path=file2, new_content="world\n"),
    ]

    summary = commit_changes(base, changes, mode="best_effort", atomic=False)
    assert not summary.failed
    assert set(summary.success) == {file1, file2}

    # Modify and delete
    changes2: List[Change] = [
        Change(action="modify", path=file1, new_content="HELLO\n"),
        Change(action="delete", path=file2),
    ]
    summary2 = commit_changes(base, changes2, mode="best_effort", atomic=False)
    assert file1 in summary2.success
    assert file2 in summary2.success

    with open(os.path.join(base, file1), encoding="utf-8") as f:
        assert f.read() == "HELLO\n"
    assert not os.path.exists(os.path.join(base, file2))


# ---------------------------------------------------------------------------
# Level 26: commit_changes atomic + rename and backup behavior (smoke)
# ---------------------------------------------------------------------------


def test_level_26_commit_changes_atomic_and_rename(tmp_path: Any = None):
    if tmp_path is None:
        tmp = tempfile.TemporaryDirectory()
        base = tmp.name
    else:
        base = str(tmp_path)

    src = os.path.join(base, "src.txt")
    os.makedirs(base, exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("data\n")

    changes: List[Change] = [
        Change(action="modify", path="src.txt", new_content="DATA\n", original_content="data\n"),
        Change(action="rename", path="dest.txt", from_path="src.txt"),
    ]

    summary = commit_changes(base, changes, mode="best_effort", atomic=True, backup_ext=".bak")
    assert not summary.failed
    assert any("src.txt" in s for s in summary.success)
    assert any("src.txt -> dest.txt" in s for s in summary.success)

    dest = os.path.join(base, "dest.txt")
    assert os.path.exists(dest)
    with open(dest, "r", encoding="utf-8") as f:
        assert f.read() == "DATA\n"


# ---------------------------------------------------------------------------
# Level 27: fuzzy_patch_partial best-effort behavior
# ---------------------------------------------------------------------------


def test_level_27_fuzzy_patch_partial_best_effort():
    original = "a\nb\nc\n"
    patch = textwrap.dedent(
        """
        @@ -1,3 +1,3 @@
         a
        -b
        +B
        -missing
         c
        """
    ).strip()

    new_text, applied, failed = fuzzy_patch_partial(original, patch)
    assert "B" in new_text
    # The hunk referencing "missing" should fail or be partially applied; ensure file not corrupted
    assert "missing" not in new_text
    assert new_text.endswith("c\n") or new_text.endswith("c")


# ---------------------------------------------------------------------------
# Level 28: extract_blocks_from_text deduplication semantics
# ---------------------------------------------------------------------------


def test_level_28_extract_blocks_dedup_last_wins():
    markdown = textwrap.dedent(
        """
        ```python
        # app.py
        print("v1")
        ```

        ```python
        # app.py
        print("v2")
        ```
        """
    )
    blocks = extract_blocks_from_text(markdown)
    # Only last version for the same path should remain
    app_blocks = [b for b in blocks if b.get("file_path", "").endswith("app.py")]
    assert len(app_blocks) == 1
    assert "print(\"v2\")" in app_blocks[0]["code"] or "print('v2')" in app_blocks[0]["code"]


# ---------------------------------------------------------------------------
# Level 29: Mixed rename, delete, and diff extraction in one markdown
# ---------------------------------------------------------------------------


def test_level_29_mixed_rename_delete_diff_blocks():
    markdown = textwrap.dedent(
        """
        ```diff
        rename from src/old.py
        rename to src/new.py
        ```

        ```diff
        --- a/remove.me
        +++ /dev/null
        @@ -1,1 +0,0 @@
        -bye
        ```

        ```diff
        --- a/src/new.py
        +++ b/src/new.py
        @@ -1,1 +1,1 @@
        -print("x")
        +print("y")
        ```
        """
    )

    blocks = extract_blocks_from_text(markdown)
    kinds = [b["type"] for b in blocks]
    assert "rename" in kinds or any("rename from" in b.get("code", "") for b in blocks)
    assert any(b["type"] == "diff" for b in blocks)


# ---------------------------------------------------------------------------
# Level 30: End-to-end: parse + plan + commit on a mini workspace
# ---------------------------------------------------------------------------


def test_level_30_end_to_end_parse_plan_commit(tmp_path: Any = None):
    if tmp_path is None:
        tmp = tempfile.TemporaryDirectory()
        base = tmp.name
    else:
        base = str(tmp_path)

    os.makedirs(base, exist_ok=True)

    app = os.path.join(base, "app.py")
    with open(app, "w", encoding="utf-8") as f:
        f.write("print('old')\n")

    markdown = textwrap.dedent(
        """
        I'll update app.py and add a new config file.

        ```diff
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,1 @@
        -print('old')
        +print('new')
        ```

        ```python
        # config.yml
        value: 1
        ```
        """
    )

    parsed = list(parse_markdown_string(markdown))
    planned: List[Dict[str, Any]] = []

    for b in parsed:
        if b["type"] == "diff":
            planned.append(
                {
                    "metadata": {"change_type": "diff", "file_path": "app.py"},
                    "block": b,
                }
            )
        elif b["type"] == "file":
            planned.append(
                {
                    "metadata": {
                        "change_type": "full_replacement",
                        "file_path": b["pre_classification"]["file_path"],
                    },
                    "block": b,
                }
            )

    changes = plan_and_generate_changes(planned, base)
    assert any(isinstance(c, Change) for c in changes)

    summary = commit_changes(base, changes, mode="best_effort", atomic=True)
    assert not summary.failed

    with open(app, "r", encoding="utf-8") as f:
        assert "print('new')" in f.read()

    cfg = os.path.join(base, "config.yml")
    assert os.path.exists(cfg)