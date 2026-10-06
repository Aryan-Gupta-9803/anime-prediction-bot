import re
import unicodedata

_PUNCT = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)
_INVISIBLE = re.compile("[​‌‍⁠﻿]")
_NUMBERING = re.compile(r"^\s*\d{1,2}\s*[.):\-]\s*")


def normalize(text) -> str:
    """Case/spacing/unicode-insensitive form used for every title and answer comparison.
    Handles curly quotes and full-width characters that phone keyboards produce."""
    t = unicodedata.normalize("NFKC", str(text))
    t = _INVISIBLE.sub("", t).translate(_PUNCT)
    return " ".join(t.casefold().split())


def strip_numbering(line: str) -> str:
    """'3. Frieren', '3) Frieren', '3 - Frieren' -> 'Frieren'."""
    return _NUMBERING.sub("", line, count=1).strip()


def chunk_lines(lines: list, limit: int = 3800) -> list:
    """Joins lines into pages of at most `limit` characters (embed descriptions cap at 4096)."""
    pages, current, size = [], [], 0
    for line in lines:
        line = line if len(line) <= limit else line[: limit - 3] + "..."
        if current and size + len(line) + 1 > limit:
            pages.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pages.append("\n".join(current))
    return pages


def num(value):
    """A points value as a whole number when it is one, otherwise a float (a score can be 7.5 under a
    multiplier rule). Keeps '5' from showing up as '5.0'."""
    value = float(value)
    return int(value) if value == int(value) else value


def clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."
