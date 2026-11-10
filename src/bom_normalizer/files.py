"""CSV and XLSX readers. File access stays outside the normalization functions."""

import csv
from collections.abc import Iterable
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.cell.read_only import EmptyCell, ReadOnlyCell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

from .models import CellError, FileMapping, Formula, RawRow, Source, Table, _XlsxStandardFormula


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _header_location(source: Source) -> str:
    sheet = f", sheet {source.sheet!r}" if source.sheet is not None else ""
    return f"{source.file}{sheet}, header row {source.row}"


def _check_headers(headers: Iterable[str], location: str) -> None:
    seen = set()
    for heading in headers:
        if heading in seen:
            raise ValueError(f"{location}: duplicate column heading {heading!r}; use unique headings")
        seen.add(heading)


def _table(
    path: Path,
    sheet: str | None,
    records: Iterable[tuple[int, tuple[object, ...]]],
    header_row: int,
) -> Table:
    source = Source(str(path), header_row, sheet)
    location = _header_location(source)
    records = iter(records)
    for row, cells in records:
        if row == header_row:
            break
        if row > header_row:
            raise ValueError(f"{location}: header must start a record")
    else:
        raise ValueError(f"{location}: missing header row {header_row}; check the configured row number")

    # Standard XLSX formulas retain their original text as column headings.
    header_values = (cell.text if isinstance(cell, _XlsxStandardFormula) else cell for cell in cells)
    header_cells = tuple("" if _is_blank(cell) else str(cell) for cell in header_values)
    headers = tuple(name for name in header_cells if name)
    if not headers:
        raise ValueError(f"{location}: empty header row {header_row}; select the row with column titles")
    _check_headers(headers, location)

    rows = []
    for row, cells in records:
        if all(map(_is_blank, cells)):
            continue
        raw = {}
        for index, name in enumerate(header_cells):
            cell = cells[index] if index < len(cells) else None
            if name:
                raw[name] = cell
            elif not _is_blank(cell):
                raise ValueError(f"{path}: data in unnamed column {index + 1}, row {row}")
        if any(not _is_blank(cell) for cell in cells[len(header_cells):]):
            raise ValueError(f"{path}: extra cells in row {row}")
        rows.append(RawRow(Source(str(path), row, sheet), raw))
    return Table(headers, tuple(rows), source)


def _xlsx_value(cell: ReadOnlyCell | EmptyCell) -> object:
    value = cell.value
    if isinstance(value, (ArrayFormula, DataTableFormula)):
        return Formula(getattr(value, "text", None), dict(value))
    if cell.data_type == "f":
        return _XlsxStandardFormula(value, {"t": "normal"})
    if cell.data_type == "e":
        return CellError(value)
    return value


def _xlsx_records(
    rows: Iterable[tuple[ReadOnlyCell | EmptyCell, ...]],
) -> Iterable[tuple[int, tuple[object, ...]]]:
    for row, cells in enumerate(rows, start=1):
        yield row, tuple(map(_xlsx_value, cells))


def _csv_records(reader) -> Iterable[tuple[int, tuple[object, ...]]]:
    row = 1
    for cells in reader:
        yield row, tuple(cells)
        row = reader.line_num + 1


def read_table(path: str | Path, mapping: FileMapping) -> Table:
    """Read a CSV or XLSX table with physical row numbers."""
    return _read_table(path, mapping, "")


def _read_table(path: str | Path, mapping: FileMapping, prefix: str) -> Table:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        if mapping.sheet is not None:
            raise ValueError(f"{path}: {prefix}sheet is only valid for XLSX input; remove it for CSV")
        try:
            with path.open(newline="", encoding=mapping.encoding) as stream:
                reader = csv.reader(stream, delimiter=mapping.delimiter, strict=True)
                return _table(path, None, _csv_records(reader), mapping.header_row)
        except UnicodeError as error:
            raise ValueError(f"{path}: could not decode CSV using {prefix}encoding={mapping.encoding!r}; "
                             f"check the file encoding ({error})") from error
        except csv.Error as error:
            raise ValueError(f"{path}: invalid CSV near line {reader.line_num}; "
                             f"check {prefix}delimiter and quoting ({error})") from error

    if path.suffix.lower() == ".xlsx":
        try:
            workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        except (BadZipFile, InvalidFileException, KeyError) as error:
            raise ValueError(f"{path}: invalid XLSX file") from error
        try:
            worksheets = {sheet.title: sheet for sheet in workbook.worksheets}
            if mapping.sheet is None and len(worksheets) == 1:
                sheet = next(iter(worksheets.values()))
            elif mapping.sheet in worksheets:
                sheet = worksheets[mapping.sheet]
            else:
                names = ", ".join(repr(name) for name in worksheets) or "(none)"
                raise ValueError(f"{path}: set {prefix}sheet to an existing worksheet name "
                                 f"(requested {mapping.sheet!r}; available: {names})")
            sheet.reset_dimensions()
            try:
                return _table(path, sheet.title, _xlsx_records(sheet.iter_rows()), mapping.header_row)
            except (BadZipFile, IndexError, KeyError) as error:
                raise ValueError(f"{path}: could not read XLSX worksheet {sheet.title!r} ({error})") from error
        finally:
            workbook.close()

    raise ValueError(f"{path}: expected a .csv or .xlsx file")
