# conftest.py - pytest configuration
import sys
from unittest.mock import MagicMock

# Mock pathspec module if not available
try:
    import pathspec
except ImportError:
    pathspec_mock = MagicMock()
    pathspec_mock.PathSpec = MagicMock
    sys.modules['pathspec'] = pathspec_mock
