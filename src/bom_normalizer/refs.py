"""Conservative parsing of lists of reference designators."""

import re

_DESIGNATOR = re.compile(r"([A-Z]+)([0-9]+)", re.ASCII)
_RANGE = re.compile(r"([A-Z]+)([0-9]+)-([A-Z]*)([0-9]+)", re.ASCII)
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})
_MAX_RANGE_SIZE = 10_000


def _tokens(value: str) -> list[str]:
    # Preserve the source token, including original case and dash character.
    chunks = re.split(r"[,;\r\n]+", value)
    return [
        token
        for chunk in chunks
        for token in re.split(r"(?<![-–—−\s])\s+(?![-–—−\s])", chunk.strip())
        if token
    ]


def _expand_token(token: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s*([-–—−])\s*", r"\1", token).translate(_DASHES)
    if not normalized.isascii():
        raise ValueError(f"invalid reference designator {token!r}")
    normalized = normalized.upper()
    if _DESIGNATOR.fullmatch(normalized):
        return (normalized,)
    match = _RANGE.fullmatch(normalized)
    if match is None:
        raise ValueError(f"invalid reference designator {token!r}")

    prefix, start, end_prefix, end = match.groups()
    padded = (len(start) > 1 and start[0] == "0") or (len(end) > 1 and end[0] == "0")
    if (end_prefix and end_prefix != prefix) or (padded and len(start) != len(end)):
        raise ValueError(f"ambiguous reference range {token!r}")
    first, last = int(start), int(end)
    if not 0 <= last - first < _MAX_RANGE_SIZE:
        raise ValueError(f"reversed or oversized reference range {token!r}")
    width = len(start) if padded else 1
    return tuple(f"{prefix}{number:0{width}d}" for number in range(first, last + 1))


def expand_refs(value: str) -> tuple[str, ...]:
    """Expand a list of designators, raising ValueError on missing or invalid tokens."""
    tokens = _tokens(value)
    if not tokens:
        raise ValueError("reference designator is missing")
    return tuple(ref for token in tokens for ref in _expand_token(token))
