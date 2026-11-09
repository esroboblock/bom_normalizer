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


def _mapping(path: Path) -> tuple[FileMapping, FileMapping]:
    data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or set(data) != {"bom", "coordinates"}:
        raise ValueError("mapping must contain exactly the bom and coordinates objects")
    try:
        return FileMapping(**data["bom"]), FileMapping(**data["coordinates"])
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
    normalize.add_argument("--coordinates", required=True, type=Path)
    normalize.add_argument("--mapping", required=True, type=Path, help="JSON column mapping")
    normalize.add_argument("--output", required=True, type=Path, help="New JSON output file")
    args = parser.parse_args(argv)

    try:
        bom_mapping, coordinate_mapping = _mapping(args.mapping)
        result = merge(read_bom(args.bom, bom_mapping), read_coordinates(args.coordinates, coordinate_mapping))
        payload = json.dumps(result_dict(result), indent=2, ensure_ascii=False, allow_nan=False)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(payload + "\n")
    except (OSError, ValueError, ParseError, DefusedXmlException) as error:
        parser.exit(2, f"bom-normalizer: {error}\n")

    print(format_report(result), file=sys.stderr)
    return 0
