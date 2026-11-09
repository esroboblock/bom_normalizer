"""Serialize results for programs and format reviews for people."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import math

from .models import CellError, Formula, Result, Review, Source, _XlsxStandardFormula


def _json_value(value):
    if isinstance(value, _XlsxStandardFormula):
        return value.text
    if isinstance(value, Review):
        return {
            "code": value.code, "message": value.message,
            "sources": _json_value(value.sources), "ref": value.ref,
        }
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


def _raw_cell(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"type": "float", "value": str(value)}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, (datetime, date, time)):
        return {"type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, timedelta):
        microseconds = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
        return {"type": "timedelta", "value": str(microseconds)}
    if isinstance(value, Formula):
        return {"type": "formula", "text": value.text, "attributes": dict(value.attributes)}
    if isinstance(value, CellError):
        return {"type": "cell_error", "error": value.error}
    raise ValueError(f"unsupported raw cell type: {type(value).__name__}")


def result_dict(
    result: Result, *, schema_version: int = 2, include_paths: bool | None = None, include_raw: bool = True,
) -> dict:
    """Encode a result using the documented v2 contract or the legacy v1 layout."""
    if schema_version == 1:
        if not result.coordinates_provided or include_paths is False or not include_raw:
            raise ValueError("schema version 1 requires coordinates and full source evidence")
        return {
            "schema_version": 1, "placements": _json_value(result.placements),
            "unresolved_bom": _json_value(result.unresolved_bom),
            "unresolved_coordinates": _json_value(result.unresolved_coordinates),
            "issues": _json_value(result.issues),
        }
    if schema_version != 2:
        raise ValueError("unsupported result schema version")

    aliases = {}
    sheets = {}

    def source_dict(source):
        file = source.file
        if not include_paths:
            file = aliases.setdefault(file, f"source-{len(aliases) + 1}")
        sheet = source.sheet
        if not include_raw and sheet is not None:
            sheet = sheets.setdefault((source.file, sheet), f"sheet-{len(sheets) + 1}")
        return {"file": file, "row": source.row, "sheet": sheet}

    def input_dict(provided, evidence):
        return {
            "status": "provided" if provided else "not_provided",
            "source": source_dict(evidence.source) if evidence and evidence.source else None,
            "mapping": _json_value(evidence.mapping) if evidence and include_raw else None,
        }

    def entry_dict(entry):
        data = {
            field.name: _json_value(getattr(entry, field.name))
            for field in fields(entry) if field.name not in {"source", "raw", "token"}
        }
        data["source"] = source_dict(entry.source)
        data["raw"] = {key: _raw_cell(value) for key, value in sorted(entry.raw.items())} if include_raw else None
        data["token"] = entry.token if include_raw else None
        return data

    inputs = {
        "bom": input_dict(True, result.bom_evidence),
        "coordinates": input_dict(result.coordinates_provided, result.coordinate_evidence),
    }
    return {
        "schema_version": 2,
        "evidence_mode": "full" if include_raw else "omit_raw",
        "inputs": inputs,
        "placements": [
            {
                "ref": placement.ref, "status": placement.status, "population": placement.population,
                "mount": placement.mount, "material_group": placement.material_group,
                "bom": [entry_dict(entry) for entry in placement.bom],
                "coordinates": [entry_dict(entry) for entry in placement.coordinates],
            }
            for placement in result.placements
        ],
        "unresolved_bom": [entry_dict(entry) for entry in result.unresolved_bom],
        "unresolved_coordinates": [entry_dict(entry) for entry in result.unresolved_coordinates],
        "issues": [
            {
                "code": issue.code, "message": issue.message if include_raw else None,
                "sources": [source_dict(source) for source in issue.sources],
                "ref": issue.ref, "severity": issue.severity, "field": issue.field, "input_role": issue.input_role,
            }
            for issue in result.issues
        ],
    }


def _location(source: Source) -> str:
    sheet = f" [{source.sheet}]" if source.sheet is not None else ""
    return f"{source.file}{sheet} row {source.row}"


def format_report(result: Result, *, include_paths: bool = True, include_raw: bool = True) -> str:
    """Return Ref counts and one human-readable line per review."""
    refs = f"Refs {len(result.placements)}"
    unresolved = f"Unresolved tokens {len(result.unresolved_bom)} BOM"
    if result.coordinates_provided:
        counts = Counter(placement.status for placement in result.placements)
        statuses = ", ".join(
            f"{status} {counts[status]}" for status in ("matched", "ambiguous", "bom_only", "coordinate_only")
        )
        lines = [f"{refs} ({statuses})"]
        unresolved += f", {len(result.unresolved_coordinates)} coordinate"
    else:
        lines = [refs, "Coordinates not provided (comparison skipped)"]
    lines.extend([unresolved, f"Reviews {len(result.issues)}"])
    for issue in result.issues:
        ref = f" [{issue.ref}]" if issue.ref is not None else ""
        line = f"- {issue.code}{ref}"
        if include_raw:
            line += f" — {issue.message}"
        if include_paths:
            locations = ", ".join(
                _location(source) if include_raw else f"{source.file} row {source.row}" for source in issue.sources
            )
            line += f" ({locations})"
        lines.append(line)
    return "\n".join(lines)
