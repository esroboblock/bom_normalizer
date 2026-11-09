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


def _table(
    path: Path,
    sheet: str | None,
    records: Iterable[tuple[int, tuple[object, ...]]],
    header_row: int,
) -> Table:
    records = iter(records)
    for row, cells in records:
        if row == header_row:
            break
        if row > header_row:
            raise ValueError(f"{path}: header row {header_row} must start a record")
    else:
        raise ValueError(f"{path}: missing header row {header_row}")

    # Standard XLSX formulas retain their original text as column headings.
    header_values = (cell.text if isinstance(cell, _XlsxStandardFormula) else cell for cell in cells)
    header_cells = tuple("" if _is_blank(cell) else str(cell) for cell in header_values)
    headers = tuple(name for name in header_cells if name)
    if not headers:
        raise ValueError(f"{path}: empty header row {header_row}")
    if len(set(headers)) != len(headers):
        raise ValueError(f"{path}: duplicate column heading in row {header_row}")

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
    return Table(headers, tuple(rows), Source(str(path), header_row, sheet))


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
    """Read a UTF-8 CSV or XLSX table with physical row numbers."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        if mapping.sheet is not None:
            raise ValueError("sheet is only valid for XLSX input")
        try:
            with path.open(newline="", encoding=mapping.encoding) as stream:
                reader = csv.reader(stream, delimiter=mapping.delimiter, strict=True)
                return _table(path, None, _csv_records(reader), mapping.header_row)
        except (csv.Error, LookupError) as error:
            raise ValueError(f"{path}: {error}") from error

    if path.suffix.lower() == ".xlsx":
        try:
            workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        except (BadZipFile, InvalidFileException, KeyError) as error:
            raise ValueError(f"{path}: invalid XLSX file") from error
        try:
            if mapping.sheet is None and len(workbook.worksheets) != 1:
                raise ValueError(f"{path}: select an XLSX worksheet with sheet")
            sheet = workbook[mapping.sheet] if mapping.sheet is not None else workbook.worksheets[0]
            if sheet not in workbook.worksheets:
                raise ValueError(f"{path}: {sheet.title!r} is not a worksheet")
            sheet.reset_dimensions()
            return _table(path, sheet.title, _xlsx_records(sheet.iter_rows()), mapping.header_row)
        except KeyError as error:
            raise ValueError(f"{path}: missing worksheet {mapping.sheet!r}") from error
        finally:
            workbook.close()

    raise ValueError(f"{path}: expected a .csv or .xlsx file")
