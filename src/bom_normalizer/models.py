"""Independent input, source evidence, and reconciled output models."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping

Population = Literal["populated", "dnp", "unknown"]
Mount = Literal["smd", "tht", "unknown"]
Sourcing = Literal["fixed", "spec_based", "unknown"]
Status = Literal["matched", "ambiguous", "bom_only", "coordinate_only"]


@dataclass(frozen=True)
class FileMapping:
    """Map normalized field names to exact input column headings."""

    columns: Mapping[str, str]
    sheet: str | None = None
    header_row: int = 1
    unit: str | None = None
    delimiter: str = ","
    encoding: str = "utf-8-sig"

    def __post_init__(self) -> None:
        if not isinstance(self.columns, Mapping) or any(
            not isinstance(name, str) or not isinstance(heading, str) or not heading.strip()
            for name, heading in self.columns.items()
        ):
            raise ValueError("columns must map field names to nonblank header strings")
        if type(self.header_row) is not int or self.header_row < 1:
            raise ValueError("header_row must be a positive integer")
        if self.sheet is not None and (not isinstance(self.sheet, str) or not self.sheet):
            raise ValueError("sheet must be a nonempty name")
        if self.unit is not None and not isinstance(self.unit, str):
            raise ValueError("unit must be a string")
        if not isinstance(self.delimiter, str) or len(self.delimiter) != 1:
            raise ValueError("delimiter must be one character")
        if not isinstance(self.encoding, str) or not self.encoding:
            raise ValueError("encoding must be a nonempty string")


@dataclass(frozen=True)
class Source:
    file: str
    row: int
    sheet: str | None = None


@dataclass(frozen=True)
class Formula:
    """Source evidence for a formula."""

    text: str | None
    attributes: Mapping[str, str]


class _XlsxStandardFormula(Formula):
    """A workbook formula represented as plain text in legacy JSON."""


@dataclass(frozen=True)
class CellError:
    """A native XLSX cell error, distinct from the same text entered literally."""

    error: str


@dataclass(frozen=True)
class RawRow:
    source: Source
    raw: Mapping[str, object]


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[RawRow, ...]
    header_source: Source | None = None


@dataclass(frozen=True)
class InputEvidence:
    """Mapping and optional header location used to normalize one input."""

    mapping: FileMapping
    source: Source | None = None


@dataclass(frozen=True)
class Review:
    code: str
    message: str
    sources: tuple[Source, ...]
    ref: str | None = None
    severity: Literal["warning", "error"] = "warning"
    field: str | None = None
    input_role: Literal["bom", "coordinates"] | None = None


@dataclass(frozen=True)
class Unresolved(RawRow):
    token: str | None


@dataclass(frozen=True)
class BomEntry(RawRow):
    ref: str
    token: str
    quantity: int | None
    population: Population
    mount: Mount
    sourcing: Sourcing
    manufacturer: str | None
    mpn: str | None
    specification: str | None
    value: str | None
    package: str | None


@dataclass(frozen=True)
class CoordinateEntry(RawRow):
    ref: str
    token: str
    x_mm: Decimal | None
    y_mm: Decimal | None
    unit: str
    rotation_deg: Decimal | None
    side: Literal["top", "bottom", "unknown"]


@dataclass(frozen=True)
class BomInput:
    entries: tuple[BomEntry, ...]
    unresolved: tuple[Unresolved, ...]
    issues: tuple[Review, ...]
    evidence: InputEvidence | None = None


@dataclass(frozen=True)
class CoordinateInput:
    entries: tuple[CoordinateEntry, ...]
    unresolved: tuple[Unresolved, ...]
    issues: tuple[Review, ...]
    evidence: InputEvidence | None = None


@dataclass(frozen=True)
class Placement:
    ref: str
    status: Status
    bom: tuple[BomEntry, ...]
    coordinates: tuple[CoordinateEntry, ...]
    population: Population
    mount: Mount
    material_group: str | None


@dataclass(frozen=True)
class Result:
    placements: tuple[Placement, ...]
    unresolved_bom: tuple[Unresolved, ...]
    unresolved_coordinates: tuple[Unresolved, ...]
    issues: tuple[Review, ...]
    coordinates_provided: bool = True
    bom_evidence: InputEvidence | None = None
    coordinate_evidence: InputEvidence | None = None
