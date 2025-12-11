import textwrap
from contextforge.commit.patch import patch_text


def test_sentinel_branch_replacement_unique_edges():
    A16 = "A" * 16
    B16 = "B" * 16
    original = f"{A16} MIDDLE TEXT {B16}"
    old = f"{A16} DIFFERENT {B16}"
    new = f"{A16} REPLACED {B16}"
    out = patch_text(original, [dict(old=old, new=new)])
    assert out == new


def test_multi_hunk_pure_addition_at_end_of_file():
    """
    Test that when a multi-hunk diff has a pure addition at the end of the file,
    the addition is correctly applied.

    This is a regression test for a bug where additions at EOF in multi-diff sets
    were being dropped due to an off-by-one boundary check.
    """
    # Original content with multiple functions
    content = textwrap.dedent("""
        import { Button } from './button';
        import { Input } from './input';

        function ComponentA() {
          return <div>A</div>;
        }

        function ComponentB() {
          return <div>B</div>;
        }
    """).strip()

    # Patch that:
    # 1. Adds a new import at the top
    # 2. Adds a new function at the end of the file
    patch = textwrap.dedent("""
        @@ -1,2 +1,3 @@
         import { Button } from './button';
         import { Input } from './input';
        +import { Label } from './label';
        @@ -9,3 +10,9 @@
         function ComponentB() {
           return <div>B</div>;
         }
        +
        +function ComponentC() {
        +  return <div>C</div>;
        +}
    """).strip()

    result = patch_text(content, patch)

    # The new import should be present
    assert "import { Label } from './label';" in result

    # The new function at the end should be present
    assert "function ComponentC()" in result
    assert "return <div>C</div>;" in result


def test_pure_addition_at_exact_end_of_file():
    """
    Test adding content at the exact end of file where the insertion point
    equals len(file_lines).
    """
    content = textwrap.dedent("""
        line1
        line2
        line3
    """).strip()

    # Patch adds content after line3 (at EOF)
    patch = textwrap.dedent("""
        @@ -1,3 +1,5 @@
         line1
         line2
         line3
        +line4
        +line5
    """).strip()

    result = patch_text(content, patch)

    assert "line4" in result
    assert "line5" in result
    # Verify order
    lines = result.strip().split("\n")
    assert lines[-1] == "line5"
    assert lines[-2] == "line4"


def test_multi_hunk_with_trailing_pure_addition_using_lead_context_only():
    """
    Test a multi-hunk diff where the last hunk is a pure addition
    that only has leading context (no trailing context possible at EOF).
    """
    content = textwrap.dedent("""
        // File start
        function foo() {
          console.log('foo');
        }

        function bar() {
          console.log('bar');
        }
    """).strip()

    # First hunk modifies foo(), second hunk adds a new function at EOF
    patch = textwrap.dedent("""
        @@ -2,3 +2,3 @@
         function foo() {
        -  console.log('foo');
        +  console.log('FOO');
         }
        @@ -6,3 +6,7 @@
         function bar() {
           console.log('bar');
         }
        +
        +function baz() {
        +  console.log('baz');
        +}
    """).strip()

    result = patch_text(content, patch)

    # First hunk change should be applied
    assert "console.log('FOO');" in result
    assert "console.log('foo');" not in result

    # Second hunk (pure addition at EOF) should be applied
    assert "function baz()" in result
    assert "console.log('baz');" in result
