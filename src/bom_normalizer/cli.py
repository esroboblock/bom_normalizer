"""Command line interface for normalizing a BOM and optional placement data."""

import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException

from .models import FileMapping
from .normalize import _validate_fields, read_bom, read_coordinates
from .reconcile import merge
from .report import format_report, result_dict


class _ObjectPairs(list):
    """Keep object keys until their full mapping path is known."""


def _unique_objects(value: object, path: str = "") -> object:
    if not isinstance(value, _ObjectPairs):
        return value
    result = {}
    for key, item in value:
        key_path = f"{path}.{key}" if path else key
        if key in result:
            raise ValueError(f"duplicate key: {key_path}")
        result[key] = _unique_objects(item, key_path)
    return result


def _file_mapping(data: object, kind: str) -> FileMapping:
    if not isinstance(data, dict):
        raise ValueError(f"{kind} must be an object")
    unknown = data.keys() - {field.name for field in fields(FileMapping)}
    if unknown:
        raise ValueError(f"{kind}.{min(unknown)}: unsupported mapping option")
    if "columns" not in data:
        raise ValueError(f"{kind}.columns is required")
    try:
        mapping = FileMapping(**data)
    except ValueError as error:
        raise ValueError(f"{kind}.{error}") from error
    _validate_fields(mapping, kind)
    return mapping


def _mapping(path: Path, *, coordinates_provided: bool) -> tuple[FileMapping, FileMapping | None]:
    try:
        data = _unique_objects(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_ObjectPairs))
        if not isinstance(data, dict):
            raise ValueError("mapping must be a JSON object")
        unknown = data.keys() - {"bom", "coordinates"}
        if unknown:
            raise ValueError(f"{min(unknown)}: unsupported mapping section")
        if "bom" not in data:
            raise ValueError("bom is required")
        if coordinates_provided and "coordinates" not in data:
            raise ValueError("a coordinates mapping is required with --coordinates")
        bom = _file_mapping(data["bom"], "bom")
        coordinates = _file_mapping(data["coordinates"], "coordinates") if "coordinates" in data else None
        return bom, coordinates
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise ValueError(f"{path}: invalid JSON mapping ({error}); use UTF-8 JSON") from error
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def _write_output(path: Path, payload: str) -> None:
    # Only clean up files created by this invocation. Exclusive creation protects existing files.
    output = path.open("x", encoding="utf-8")
    try:
        with output:
            output.write(payload + "\n")
    except (OSError, UnicodeError) as error:
        try:
            path.unlink()
        except OSError as cleanup_error:
            raise OSError(f"{path}: {error}; could not remove incomplete output ({cleanup_error})") from error
        raise OSError(f"{path}: could not write output ({error})") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bom-normalizer")
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize", help="Normalize a mapped BOM and optional placement file")
    normalize.add_argument("--bom", required=True, type=Path)
    normalize.add_argument("--coordinates", type=Path, help="Placement file to compare against the BOM")
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
        _write_output(args.output, payload)
    except (OSError, ValueError, ParseError, DefusedXmlException) as error:
        parser.exit(2, f"bom-normalizer: {error}\n")

    print(format_report(
        result, include_paths=bool(args.include_paths or args.schema_version == 1), include_raw=not args.omit_raw,
    ), file=sys.stderr)
    return 0
