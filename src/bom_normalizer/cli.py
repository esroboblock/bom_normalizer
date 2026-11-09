"""Command line interface for normalizing two mapped files."""

import argparse
import json
from pathlib import Path
import sys
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException

from .models import FileMapping
from .normalize import read_bom, read_coordinates
from .reconcile import merge
from .report import format_report, result_dict


def _mapping(path: Path, *, coordinates_provided: bool) -> tuple[FileMapping, FileMapping | None]:
    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or "bom" not in data or data.keys() - {"bom", "coordinates"}:
        raise ValueError("mapping must contain a bom object and may contain a coordinates object")
    if coordinates_provided and "coordinates" not in data:
        raise ValueError("a coordinates mapping is required with --coordinates")
    try:
        bom = FileMapping(**data["bom"])
        coordinates = FileMapping(**data["coordinates"]) if "coordinates" in data else None
        return bom, coordinates
    except TypeError as error:
        raise ValueError(f"invalid file mapping: {error}") from error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in mapping: {key!r}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bom-normalizer")
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize", help="Normalize mapped BOM and placement files")
    normalize.add_argument("--bom", required=True, type=Path)
    normalize.add_argument("--coordinates", type=Path)
    normalize.add_argument("--mapping", required=True, type=Path, help="JSON column mapping")
    normalize.add_argument("--output", required=True, type=Path, help="New JSON output file")
    normalize.add_argument("--schema-version", type=int, choices=(1, 2), default=2)
    normalize.add_argument("--include-paths", action="store_true", default=None, help="Export source file paths")
    normalize.add_argument("--omit-raw", action="store_true", help="Omit raw cells and related source text")
    args = parser.parse_args(argv)

    try:
        bom_mapping, coordinate_mapping = _mapping(args.mapping, coordinates_provided=args.coordinates is not None)
        bom = read_bom(args.bom, bom_mapping)
        coordinates = read_coordinates(args.coordinates, coordinate_mapping) if args.coordinates is not None else None
        result = merge(bom, coordinates)
        data = result_dict(
            result, schema_version=args.schema_version, include_paths=args.include_paths, include_raw=not args.omit_raw,
        )
        payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(payload + "\n")
    except (OSError, ValueError, ParseError, DefusedXmlException) as error:
        parser.exit(2, f"bom-normalizer: {error}\n")

    print(format_report(
        result, include_paths=bool(args.include_paths or args.schema_version == 1), include_raw=not args.omit_raw,
    ), file=sys.stderr)
    return 0
