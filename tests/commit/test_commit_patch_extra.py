
import re
import pytest
from contextforge.commit.patch import patch_text,  PatchFailedError

def test_missing_old_and_pattern_raises():
    with pytest.raises(PatchFailedError):
        patch_text("hello", [dict(new="x")])

def test_sentinel_head_tail_unique_replacement():
    A16 = "A"*16
    B16 = "B"*16
    original = f"{A16} MIDDLE TEXT {B16}"
    old = f"{A16} DIFFERENT {B16}"
    new = f"{A16} REPLACED {B16}"
    out = patch_text(original, [dict(old=old, new=new)])
    assert out == new
