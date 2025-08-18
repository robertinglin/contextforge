from dataclasses import dataclass
from typing import Optional


@dataclass
class Block:
    """Base block spans a region of the source text."""

    type: str
    content: str
    start: int
    end: int


@dataclass
class FileBlock(Block):
    """Represents a full file/code block."""

    language: str
    file_path: Optional[str] = None


@dataclass
class DiffBlock(Block):
    """Represents a diff/patch block, optionally scoped to a file path."""

    file_path: Optional[str] = None
