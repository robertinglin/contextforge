
from contextforge.commit.patch import patch_text


def test_sentinel_branch_replacement_unique_edges():
    A16 = "A"*16
    B16 = "B"*16
    original = f"{A16} MIDDLE TEXT {B16}"
    old = f"{A16} DIFFERENT {B16}"
    new = f"{A16} REPLACED {B16}"
    out = patch_text(original, [dict(old=old, new=new)])
    assert out == new

def test_debug_logger_path():
    logs = []
    class Logger:
        def debug(self, msg): logs.append(msg)
    original = "log me"
    patch = dict(old="log", new="LOG")
    out = patch_text(original, [patch], debug=True, logger=Logger())
    assert out == "LOG me" and logs
