from contextforge.extract import extract_diffs_from_text as cf_extract_diffs

# =============================
# Tests
# =============================

def _assert(cond: bool, msg: str = "assertion failed") -> None:
    if not cond:
        raise AssertionError(msg)


def test_run_base_tests() -> None:
    # 1) Basic explicit ```diff ... ``` with stray backticks inside body
    s1 = (
        "Intro\n"
        "```diff\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-print(\"hi\")\n"
        "+print(\"hello\")\n"
        "code with stray backticks inside: ``` and even ````\n"
        "```\n"
        "Outro\n"
    )
    r1 = cf_extract_diffs(s1)
    _assert(len(r1) == 1, "basic explicit diff not extracted")
    _assert("--- a/x.py" in r1[0]["code"], "diff header missing")

    # 2) Closing fence attached to last line of code: }```
    s2 = (
        "Start\n"
        "```diff\n"
        "--- a/y.js\n"
        "+++ b/y.js\n"
        "@@ -2,3 +2,4 @@\n"
        "-}\n"
        "+}\n"
        "+console.log(1)\n"
        "}```\n"  # fence stuck to end of line
        "after\n"
    )
    r2 = cf_extract_diffs(s2)
    _assert(len(r2) == 1, "failed to handle closing fence at end of line")
    _assert("console.log(1)" in r2[0]["code"], "missing body content before close")

    # 3) Longer fence runs (``````diff ... ``````) enclosing inner triple backticks
    s3 = (
        "prelude\n"
        "``````diff\n"
        "--- a/hello.txt\n"
        "+++ b/hello.txt\n"
        "@@ -1 +1 @@\n"
        "-foo\n"
        "+bar\n"
        "Here is a nested code block marker: ``` not a close.\n"
        "``````\n"
        "tail\n"
    )
    r3 = cf_extract_diffs(s3)
    _assert(len(r3) == 1, "failed to handle long fences")
    _assert("+bar" in r3[0]["code"], "long-fence body incorrect")

    # 4) Opening with content on the same line (inline after fence)
    s4 = (
        "text\n"
        "```diff --- a/a\n"
        "+++ b/a\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "```\n"
    )
    r4 = cf_extract_diffs(s4)
    _assert(len(r4) == 1, "failed opening with content after the fence")
    _assert("--- a/a" in r4[0]["code"], "missing header when opener has same-line content")

    # 5) Tilde fences with closing fence attached at end of code
    s5 = (
        "~~~diff\n"
        "--- a/z\n"
        "+++ b/z\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y ~~~\n"  # this ~~~ is not a fence (has trailing text)
        "+z\n"
        "}~~~\n"
    )
    r5 = cf_extract_diffs(s5)
    _assert(len(r5) == 1, "failed to handle tilde fences or end-attached tilde")
    _assert("+z" in r5[0]["code"], "tilde body incorrect")

    # 6) Bare fences that look like diffs (heuristics)
    s6 = (
        "```\n"
        "--- a/q\n"
        "+++ b/q\n"
        "@@ -1 +1 @@\n"
        "-q\n"
        "+w\n"
        "```\n"
    )
    r6 = cf_extract_diffs(s6, allow_bare_fences_that_look_like_diff=True)
    _assert(len(r6) == 1, "bare diff detection failed")

    # 7) Non-diff code block should be ignored
    s7 = (
        "```python\n"
        "print('hello')\n"
        "```\n"
    )
    r7 = cf_extract_diffs(s7)
    _assert(len(r7) == 0, "non-diff code block should not be extracted")

    # 8) Multiple diffs in one document
    s8 = (
        "Part A\n"
        "```diff\n"
        "--- a/a\n"
        "+++ b/a\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "```\n"
        "Middle\n"
        "````diff\n"
        "--- a/c\n"
        "+++ b/c\n"
        "@@ -1 +1 @@\n"
        "-c\n"
        "+d\n"
        "````\n"
    )
    r8 = cf_extract_diffs(s8)
    _assert(len(r8) == 2, "should have extracted two diffs")

    # 9) Fallback: whole document is a raw diff, no fences
    s9 = (
        "diff --git a/file b/file\n"
        "index 111..222 100644\n"
        "--- a/file\n"
        "+++ b/file\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    r9 = cf_extract_diffs(s9)
    _assert(len(r9) == 1 and r9[0]["open_fence"] is None, "raw diff fallback failed")

    # 10) Same physical line open & close (inline, long fence)
    s10 = "before ``````diff --- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n `````` after"
    r10 = cf_extract_diffs(s10)
    _assert(len(r10) == 1, "failed when open/close share the same line")
    _assert("+b" in r10[0]["code"], "inline same-line block body wrong")

    # 11) Closing Diff fence on opening fence line
    s11 = (
        "Part A\n"
        "```diff\n"
        "--- a/a\n"
        "+++ b/a\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "``````diff\n"
        "--- a/c\n"
        "+++ b/c\n"
        "@@ -1 +1 @@\n"
        "-c\n"
        "+d\n"
        "```\n"
    )
    r11 = cf_extract_diffs(s11)
    _assert(len(r11) == 2, "should have extracted two diffs")
    _assert(r11[0]["code"].strip() == "--- a/a\n+++ b/a\n@@ -1 +1 @@\n-a\n+b", "first diff body incorrect")
    _assert(r11[1]["code"].strip() == "--- a/c\n+++ b/c\n@@ -1 +1 @@\n-c\n+d", "second diff body incorrect")

    print("All tests passed ✅")