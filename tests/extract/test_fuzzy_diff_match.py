
# Import the contextforge version for testing
import textwrap
from contextforge import patch_text as cf_fuzzy_patch

# -----------------------
# Tests
# -----------------------
def _assert_eq(actual: str, expected: str, name: str):
    if actual != expected:
        raise AssertionError(f"{name} failed:\n--- actual ---\n{actual}\n--- expected ---\n{expected}")


def test_run_tests():
    tests_passed = 0

    # 1. duplicate at beginning
    initial = "line1\ntarget\ntarget\nafter\nend\n"
    patch = textwrap.dedent("""
    @@ -1,5 +1,4 @@
     line1
    -target
     target
     after
     end
    """)
    expected = "line1\ntarget\nafter\nend\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "dup_begin")
    tests_passed += 1

    # 2. duplicate near end
    initial = "begin\nalpha\nbeta\nbeta\nomega\nend\n"
    patch = textwrap.dedent("""
    @@ -1,6 +1,5 @@
     begin
     alpha
    -beta
     beta
     omega
     end
    """)
    expected = "begin\nalpha\nbeta\nomega\nend\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "dup_end")
    tests_passed += 1

    # 3. pure addition in the middle
    initial = "a\nb\nd\ne\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +c
    """)
    expected = "a\nb\nc\nd\ne\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "pure_add_mid")
    tests_passed += 1

    # 4. pure addition append
    initial = "x\ny\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +z
    """)
    expected = "x\ny\nz\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "pure_add_end")
    tests_passed += 1

    # 5. guarded delete non-existent
    initial = "p\nq\nr\n"
    patch = textwrap.dedent("""
    @@ -1,3 +1,4 @@
     p
     q
    -miss
     r
    +s
    """)
    expected = "p\nq\nr\ns\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "guarded_delete")
    tests_passed += 1

    # 6. popup.js-like QR removal
    before = (
        "const views = {\n"
        "  unpaired: U,\n"
        "  paired: P,\n"
        "  qrScanner: Q,\n"
        "  contextBuilder: C,\n"
        "};\n"
        "let qrVideoStream = null;\n"
        "let qrAnimationId = null;\n"
        "unpairButton.style.display = (viewName === 'paired') ? 'block' : 'none';\n"
        "mainFooter.style.display = (viewName === 'qrScanner') ? 'none' : 'flex';\n"
        "scanQrButton.addEventListener('click', startQrScanner);\n"
    )
    patch = textwrap.dedent("""
    @@ -1,11 +1,9 @@
     const views = {
       unpaired: U,
       paired: P,
    -  qrScanner: Q,
       contextBuilder: C,
     };
    -let qrVideoStream = null;
    -let qrAnimationId = null;
     unpairButton.style.display = (viewName === 'paired') ? 'block' : 'none';
    -mainFooter.style.display = (viewName === 'qrScanner') ? 'none' : 'flex';
    +mainFooter.style.display = 'flex'; // Always visible now
    -scanQrButton.addEventListener('click', startQrScanner);
    """)
    after = cf_fuzzy_patch(before, patch)
    assert "qrScanner:" not in after and "qrVideoStream" not in after and "qrAnimationId" not in after
    assert "viewName === 'qrScanner'" not in after and "scanQrButton.addEventListener" not in after
    tests_passed += 1

    # 7. block-first contiguous replacement
    initial = "one\nalpha\nbeta\ngamma\nend\n"
    patch = textwrap.dedent("""
    @@ -1,5 +1,5 @@
     one
     alpha
    -beta
    +BETA
     gamma
     end
    """)
    expected = "one\nalpha\nBETA\ngamma\nend\n"
    out = cf_fuzzy_patch(initial, patch)
    _assert_eq(out, expected, "block-first")
    tests_passed += 1

    print(f"All tests passed: {tests_passed} cases ✔️")