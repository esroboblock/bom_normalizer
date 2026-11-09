"""Pure normalization of mapped table rows into independent input models."""

import re
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path

from .files import read_table
from .models import (
    BomEntry, BomInput, CellError, CoordinateEntry, CoordinateInput, FileMapping, Formula, RawRow, Review,
    Table, Unresolved,
)
from .refs import _expand_token, _tokens

_BOM_FIELDS = {
    "ref", "quantity", "population", "mount", "sourcing", "manufacturer", "mpn",
    "specification", "value", "package",
}
_COORDINATE_FIELDS = {"ref", "x", "y", "rotation", "side"}
_MM_PER_UNIT = {
    "mm": Decimal("1"), "cm": Decimal("10"), "um": Decimal("0.001"),
    "mil": Decimal("0.0254"), "in": Decimal("25.4"),
}
_POPULATIONS = {
    "populated": "populated", "yes": "populated", "true": "populated", "1": "populated",
    "dnp": "dnp", "dnf": "dnp", "no": "dnp", "false": "dnp", "0": "dnp",
    "unknown": "unknown",
}
_MOUNTS = {"smd": "smd", "smt": "smd", "tht": "tht", "through-hole": "tht", "unknown": "unknown"}
_SOURCING = {"fixed": "fixed", "spec_based": "spec_based", "unknown": "unknown"}
_SIDES = {"top": "top", "bottom": "bottom", "unknown": "unknown"}
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _validate_mapping(table: Table, mapping: FileMapping, allowed: set[str], required: set[str]) -> None:
    unknown = mapping.columns.keys() - allowed
    missing = required - mapping.columns.keys()
    missing_headers = set(mapping.columns.values()) - set(table.headers)
    if unknown:
        raise ValueError(f"unknown mapping fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing mapping fields: {', '.join(sorted(missing))}")
    if missing_headers:
        raise ValueError(f"mapped columns are absent: {', '.join(sorted(missing_headers))}")


def _text(value: object) -> str | None:
    return (str(value).strip() or None) if value is not None else None


def _mapped_values(
    row: RawRow, mapping: FileMapping, issues: list[Review],
) -> dict[str, str | None]:
    values = {}
    for field, column in mapping.columns.items():
        raw = row.raw.get(column)
        if isinstance(raw, CellError):
            issues.append(Review("source_cell_error", f"Cell error {raw.error!r} in mapped field {field}.", (row.source,)))
            values[field] = None
            continue
        if isinstance(raw, Formula):
            value = raw.text or f"={raw.attributes['t']}({raw.attributes.get('ref', '')})"
        else:
            value = _text(raw)
        if isinstance(raw, Formula) or (value is not None and value.startswith("=")):
            issues.append(Review("unsupported_formula", f"Formula in mapped field {field}.", (row.source,)))
            if field != "ref":
                value = None
            elif not value.startswith("="):
                value = "=" + value
        values[field] = value
    return values


def _row_refs(
    row: RawRow, value: str | None, issues: list[Review], unresolved: list[Unresolved],
    *, single: bool = False,
) -> list[tuple[str, str]]:
    if value is not None and value.startswith("="):
        unresolved.append(Unresolved(row.source, dict(row.raw), value))
        return []

    tokens = _tokens(value) if value is not None else []
    if not tokens:
        issues.append(Review("missing_ref", "Reference designator is missing.", (row.source,)))
        unresolved.append(Unresolved(row.source, dict(row.raw), None))
        return []

    invalid_tokens = []
    resolved = []
    for token in tokens:
        try:
            refs = _expand_token(token)
        except ValueError as error:
            issues.append(Review("invalid_ref", str(error), (row.source,)))
            invalid_tokens.append(token)
        else:
            resolved.extend((ref, token) for ref in refs)

    if single and (len(tokens) > 1 or len(resolved) > 1):
        issues.append(Review(
            "multiple_coordinate_refs", "One coordinate record lists multiple references.", (row.source,),
        ))
        invalid_tokens = tokens
        resolved = []
    unresolved.extend(Unresolved(row.source, dict(row.raw), token) for token in invalid_tokens)
    return resolved


def _category(
    values: dict[str, str | None], field: str, choices: dict[str, str],
    source_row: RawRow, issues: list[Review],
) -> str:
    value = values.get(field)
    if value is None:
        return "unknown"
    result = choices.get(value.casefold())
    if result is None:
        issues.append(Review(f"unknown_{field}", f"Unrecognized {field}: {value!r}.", (source_row.source,)))
        return "unknown"
    return result


def _decimal(value: str | None) -> Decimal | None:
    if value is None or len(value) > 1000 or _NUMBER.fullmatch(value) is None:
        return None
    try:
        number = Decimal(value, context=Context(traps=[InvalidOperation]))
    except InvalidOperation:
        return None
    if abs(number.as_tuple().exponent) > 1000:
        return None
    return number


def _quantity(value: str | None, row: RawRow, issues: list[Review]) -> int | None:
    if value is None:
        return None
    number = _decimal(value)
    if number is None or number < 0 or number != number.to_integral_value():
        issues.append(Review("invalid_quantity", "Quantity must be a nonnegative integer.", (row.source,)))
        return None
    return int(number)


def normalize_bom(table: Table, mapping: FileMapping) -> BomInput:
    """Normalize a BOM table and retain unresolved source rows and reviews."""
    _validate_mapping(table, mapping, _BOM_FIELDS, {"ref"})
    if mapping.unit is not None:
        raise ValueError("unit is only valid for a coordinate mapping")
    entries: list[BomEntry] = []
    issues: list[Review] = []
    unresolved: list[Unresolved] = []
    for row in table.rows:
        values = _mapped_values(row, mapping, issues)
        refs = _row_refs(row, values.get("ref"), issues, unresolved)
        quantity = _quantity(values.get("quantity"), row, issues)
        if quantity is not None and quantity != len({ref for ref, _ in refs}):
            issues.append(Review(
                "quantity_mismatch", f"Quantity {quantity} differs from the number of unique valid Ref designators.",
                (row.source,),
            ))
        population = _category(values, "population", _POPULATIONS, row, issues)
        mount = _category(values, "mount", _MOUNTS, row, issues)
        sourcing = _category(values, "sourcing", _SOURCING, row, issues)
        for ref, token in refs:
            entries.append(BomEntry(
                source=row.source, raw=dict(row.raw), ref=ref, token=token, quantity=quantity,
                population=population, mount=mount, sourcing=sourcing,
                manufacturer=values.get("manufacturer"), mpn=values.get("mpn"),
                specification=values.get("specification"), value=values.get("value"),
                package=values.get("package"),
            ))
    return BomInput(tuple(entries), tuple(unresolved), tuple(issues))


def _coordinate(
    field: str, value: str | None, factor: Decimal, row: RawRow, issues: list[Review],
) -> Decimal | None:
    number = _decimal(value)
    if number is None:
        if field != "rotation" or value is not None:
            code = "invalid_rotation" if field == "rotation" else "invalid_coordinate"
            issues.append(Review(code, f"Missing or invalid numeric {field}.", (row.source,)))
        return None
    # Multiplication uses enough precision even if the caller set a low decimal context.
    precision = len(number.as_tuple().digits) + len(factor.as_tuple().digits)
    with localcontext(Context(prec=precision)):
        return number * factor


def normalize_coordinates(table: Table, mapping: FileMapping) -> CoordinateInput:
    """Normalize placement X/Y into millimeters and preserve row evidence."""
    _validate_mapping(table, mapping, _COORDINATE_FIELDS, {"ref", "x", "y"})
    unit = mapping.unit
    if unit not in _MM_PER_UNIT:
        raise ValueError(f"coordinate unit must be one of {', '.join(_MM_PER_UNIT)}")
    factor = _MM_PER_UNIT[unit]
    entries: list[CoordinateEntry] = []
    issues: list[Review] = []
    unresolved: list[Unresolved] = []
    for row in table.rows:
        values = _mapped_values(row, mapping, issues)
        refs = _row_refs(row, values.get("ref"), issues, unresolved, single=True)
        x = _coordinate("x", values.get("x"), factor, row, issues)
        y = _coordinate("y", values.get("y"), factor, row, issues)
        rotation = _coordinate("rotation", values.get("rotation"), Decimal("1"), row, issues)
        side = _category(values, "side", _SIDES, row, issues)
        for ref, token in refs:
            entries.append(CoordinateEntry(row.source, dict(row.raw), ref, token, x, y, unit, rotation, side))
    return CoordinateInput(tuple(entries), tuple(unresolved), tuple(issues))


def read_bom(path: str | Path, mapping: FileMapping) -> BomInput:
    """Read and normalize a mapped BOM file."""
    return normalize_bom(read_table(path, mapping), mapping)


def read_coordinates(path: str | Path, mapping: FileMapping) -> CoordinateInput:
    """Read and normalize a mapped placement file."""
    return normalize_coordinates(read_table(path, mapping), mapping)
