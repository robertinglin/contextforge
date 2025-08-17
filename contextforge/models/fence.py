from dataclasses import dataclass


@dataclass
class FenceToken:
    """A run of 3+ backticks or 3+ tildes anywhere in a line."""
    start: int            # absolute index of first fence char
    end: int              # absolute index AFTER last fence char
    char: str             # '`' or '~'
    length: int           # run length (>=3)
    before: str           # text on the same line BEFORE the fence
    after: str            # text on the same line AFTER the fence (until newline)
    info_first_token: str # first token right after the fence on that line, lowercased
    line_start: int       # abs index of start of the fence's line
    line_end: int         # abs index of '\n' ending the fence's line (or len(text))