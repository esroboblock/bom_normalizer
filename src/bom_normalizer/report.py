"""Serialize results for programs and format reviews for people."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, time, timedelta
from decimal import Decimal
import math

from .models import Result, Source


def _json_value(value):
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta) or (isinstance(value, float) and not math.isfinite(value)):
        return str(value)
    return value


def result_dict(result: Result) -> dict:
    """Return a versioned, JSON-ready representation with exact decimal strings."""
    return {"schema_version": 1, **_json_value(result)}


def _location(source: Source) -> str:
    sheet = f" [{source.sheet}]" if source.sheet is not None else ""
    return f"{source.file}{sheet} row {source.row}"


def format_report(result: Result) -> str:
    """Return Ref counts and one human-readable line per review."""
    counts = Counter(placement.status for placement in result.placements)
    statuses = ", ".join(f"{status} {counts[status]}" for status in ("matched", "ambiguous", "bom_only", "coordinate_only"))
    lines = [
        f"Refs {len(result.placements)} ({statuses})",
        f"Unresolved tokens {len(result.unresolved_bom)} BOM, {len(result.unresolved_coordinates)} coordinate",
        f"Reviews {len(result.issues)}",
    ]
    for issue in result.issues:
        ref = f" [{issue.ref}]" if issue.ref is not None else ""
        locations = ", ".join(_location(source) for source in issue.sources)
        lines.append(f"- {issue.code}{ref} — {issue.message} ({locations})")
    return "\n".join(lines)
