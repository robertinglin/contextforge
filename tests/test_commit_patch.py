import textwrap
from contextforge.commit.patch import patch_text


def _assert_eq(actual: str, expected: str, name: str):
    if actual != expected:
        raise AssertionError(f"{name} failed:\n--- actual ---\n{actual}\n--- expected ---\n{expected}")


def test_duplicate_at_beginning():
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
    out = patch_text(initial, patch)
    assert out == expected


def test_duplicate_near_end():
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
    out = patch_text(initial, patch)
    assert out == expected


def test_pure_addition_in_middle():
    initial = "a\nb\nd\ne\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +c
    """)
    expected = "a\nb\nc\nd\ne\n"
    out = patch_text(initial, patch)
    _assert_eq(out, expected, "pure_add_mid")


def test_pure_addition_append():
    initial = "x\ny\n"
    patch = textwrap.dedent("""
    @@ -0,0 +3,1 @@
    +z
    """)
    expected = "x\ny\nz\n"
    out = patch_text(initial, patch)
    _assert_eq(out, expected, "pure_add_end")


def test_guarded_delete_non_existent():
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
    out = patch_text(initial, patch)
    _assert_eq(out, expected, "guarded_delete")


def test_popup_js_like_qr_removal():
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
    after = patch_text(before, patch)
    assert "qrScanner:" not in after
    assert "qrVideoStream" not in after
    assert "qrAnimationId" not in after
    assert "viewName === 'qrScanner'" not in after
    assert "scanQrButton.addEventListener" not in after


def test_block_first_contiguous_replacement():
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
    out = patch_text(initial, patch)
    _assert_eq(out, expected, "block-first")