from .core import commit_changes, Change
from .patch import patch_text, fuzzy_patch_partial
    
__all__ = ["patch_text", "commit_changes", "Change"]
