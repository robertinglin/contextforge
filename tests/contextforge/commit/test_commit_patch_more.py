from contextforge.commit.patch import patch_text


def test_sentinel_branch_replacement_unique_edges():
    A16 = "A" * 16
    B16 = "B" * 16
    original = f"{A16} MIDDLE TEXT {B16}"
    old = f"{A16} DIFFERENT {B16}"
    new = f"{A16} REPLACED {B16}"
    out = patch_text(original, [dict(old=old, new=new)])
    assert out == new

