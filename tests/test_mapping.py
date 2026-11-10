import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator
from openpyxl import Workbook

import bom_normalizer as bn
from bom_normalizer.cli import main

DOCS = Path(__file__).resolve().parents[1] / "docs"
COORDINATES = {"columns": {"ref": "Ref", "x": "X", "y": "Y"}, "unit": "mm"}


def run_cli(tmp_path, mapping, bom, coordinates=None, output=None, options=()):
    config = tmp_path / "mapping.json"
    config.write_text(mapping if isinstance(mapping, str) else json.dumps(mapping), encoding="utf-8")
    args = ["normalize", "--bom", str(bom), "--mapping", str(config),
            "--output", str(output or tmp_path / "result.json"), *options]
    if coordinates is not None:
        args.extend(["--coordinates", str(coordinates)])
    return subprocess.run([sys.executable, "-m", "bom_normalizer", *args],
                          capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})


@pytest.mark.parametrize(("mapping", "diagnostic", "schema_valid"), [
    ({}, "bom is required", False),
    ({"extra": {}}, "extra: unsupported mapping section", False),
    ({"bom": {}}, "bom.columns is required", False),
    ({"bom": []}, "bom must be an object", False),
    ({"bom": {"columns": []}}, "bom.columns", False),
    ({"bom": {"columns": {"Ref": "Ref"}}}, "bom.columns.Ref", False),
    ({"bom": {"columns": {}}}, "bom.columns.ref", False),
    ({"bom": {"columns": {"ref": "   "}}}, "bom.columns.ref", False),
    ({"bom": {"columns": {"ref": "Ref", "value": "Ref"}}}, "bom.columns.value", True),
    ({"bom": {"columns": {"ref": "Ref"}, "header_row": True}}, "bom.header_row", False),
    ({"bom": {"columns": {"ref": "Ref"}, "header_row": 2.0}}, "bom.header_row", True),
    ({"bom": {"columns": {"ref": "Ref"}, "delimiter": "\n"}}, "bom.delimiter", False),
    ({"bom": {"columns": {"ref": "Ref"}, "encoding": "base64_codec"}}, "bom.encoding", True),
    ({"bom": {"columns": {"ref": "Ref"}, "encoding": "not-an-encoding"}}, "bom.encoding", True),
    ({"bom": {"columns": {"ref": "Ref"}, "sheet": 1}}, "bom.sheet", False),
    ({"bom": {"columns": {"ref": "Ref"}, "sheets": "A"}}, "bom.sheets", False),
    ({"bom": {"columns": {"ref": "Ref"}, "unit": "mm"}}, "bom.unit", False),
    ({"bom": {"columns": {"ref": "Ref"}}, "coordinates": {"columns": {"ref": "Ref", "x": "X"}}},
     "coordinates.columns.y", False),
    ({"bom": {"columns": {"ref": "Ref"}}, "coordinates": {"columns": COORDINATES["columns"]}},
     "coordinates.unit", False),
    ({"bom": {"columns": {"ref": "Ref"}}, "coordinates": {**COORDINATES, "unit": "inch"}},
     "coordinates.unit", False),
])
def test_invalid_mapping_diagnostics(tmp_path, mapping, diagnostic, schema_valid):
    schema = json.loads((DOCS / "mapping.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(mapping) == schema_valid
    bom = tmp_path / "bom.csv"
    bom.write_bytes(b"Ref\nR1\n")
    process = run_cli(tmp_path, mapping, bom)
    assert process.returncode == 2
    assert diagnostic in process.stderr
    assert "mapping.json" in process.stderr
    assert process.stdout == ""
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(("mapping", "diagnostic"), [
    ('{"bom":{"columns":{"ref":"Ref","ref":"Other"}}}', "duplicate key: bom.columns.ref"),
    ('{"bom":{},"bom":{}}', "duplicate key: bom"),
    ('{"bom":', "invalid JSON"),
])
def test_invalid_json(tmp_path, mapping, diagnostic):
    process = run_cli(tmp_path, mapping, tmp_path / "bom.csv")
    assert process.returncode == 2
    assert diagnostic in process.stderr


@pytest.mark.parametrize(("contents", "options", "diagnostic"), [
    (b"Ref,Ref\nR1,R2\n", {}, "duplicate column heading 'Ref'"),
    (b"ref\nR1\n", {}, "bom.columns.ref references absent header 'Ref'"),
    (b"Ref\nR1\n", {"header_row": 3}, "missing header row 3"),
    (b"Ref\n\"R1\n", {}, "invalid CSV near line 2"),
    (b"Ref\nR1\n", {"sheet": "Parts"}, "bom.sheet"),
    (b"Ref\n\xff\n", {}, "bom.encoding"),
])
def test_invalid_input_preserves_input_and_output(tmp_path, contents, options, diagnostic):
    bom = tmp_path / "bom.csv"
    bom.write_bytes(contents)
    output = tmp_path / "result.json"
    output.write_bytes(b"existing output")
    process = run_cli(tmp_path, {"bom": {"columns": {"ref": "Ref"}, **options}}, bom)
    assert process.returncode == 2
    assert diagnostic in process.stderr
    assert str(bom) in process.stderr
    assert bom.read_bytes() == contents
    assert output.read_bytes() == b"existing output"


def test_xlsx_wrong_sheet(tmp_path):
    bom = tmp_path / "bom.xlsx"
    workbook = Workbook()
    workbook.active.append(["Ref"])
    workbook.create_sheet("Other")
    workbook.save(bom)
    process = run_cli(tmp_path, {"bom": {"columns": {"ref": "Ref"}, "sheet": "Missing"}}, bom)
    assert process.returncode == 2
    assert "bom.sheet" in process.stderr
    assert "Missing" in process.stderr and "Other" in process.stderr
    assert str(bom) in process.stderr


def test_corrupt_xlsx_is_an_input_error(tmp_path):
    bom = tmp_path / "bom.xlsx"
    bom.write_bytes(b"not an XLSX archive")
    process = run_cli(tmp_path, {"bom": {"columns": {"ref": "Ref"}}}, bom)
    assert process.returncode == 2
    assert str(bom) in process.stderr
    assert "invalid XLSX file" in process.stderr
    assert not (tmp_path / "result.json").exists()


def test_python_mapping_and_memory_headers_validate():
    with pytest.raises(ValueError, match=r"columns\.value"):
        bn.FileMapping({"ref": "Ref", "value": "Ref"})
    with pytest.raises(ValueError, match="duplicate column heading 'Ref'"):
        bn.normalize_bom(bn.Table(("Ref", "Ref"), ()), bn.FileMapping({"ref": "Ref"}))


def write_xlsx(path, heading, values):
    workbook = Workbook()
    workbook.active.title = "Notes"
    sheet = workbook.create_sheet("Data")
    sheet.append(["Synthetic"])
    sheet.append(heading)
    sheet.append(values)
    workbook.save(path)


@pytest.mark.parametrize("formats", [("csv", None), ("xlsx", "csv"), ("csv", "xlsx")])
@pytest.mark.parametrize("options", [(), ("--include-paths", "--omit-raw"), ("--schema-version", "1")])
def test_cli_matches_public_api(tmp_path, formats, options):
    bom = tmp_path / f"bom.{formats[0]}"
    bom_config = {"columns": {"ref": "부품", "value": "Value"}, "header_row": 2}
    if formats[0] == "csv":
        bom.write_bytes("합성\n부품;Value\nR1;저항\n".encode("cp949"))
        bom_config.update(encoding="cp949", delimiter=";")
    else:
        write_xlsx(bom, ["부품", "Value"], ["R1", "저항"])
        bom_config["sheet"] = "Data"
    mappings = {"bom": bom_config}
    normalized_coordinates = coordinates = None
    if formats[1]:
        coordinates = tmp_path / f"coordinates.{formats[1]}"
        coordinate_config = {**COORDINATES, "header_row": 2}
        if formats[1] == "csv":
            coordinates.write_text("Synthetic\nRef,X,Y\nR2,1,2\n", encoding="utf-8")
        else:
            write_xlsx(coordinates, ["Ref", "X", "Y"], ["R2", 1, 2])
            coordinate_config["sheet"] = "Data"
        mappings["coordinates"] = coordinate_config
        normalized_coordinates = bn.read_coordinates(coordinates, bn.FileMapping(**coordinate_config))
    mapping_schema = json.loads((DOCS / "mapping.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(mapping_schema).validate(mappings)
    process = run_cli(tmp_path, mappings, bom, coordinates, options=options)
    if "--schema-version" in options and coordinates is None:
        assert process.returncode == 2
        assert "schema version 1 requires coordinates" in process.stderr
        assert not (tmp_path / "result.json").exists()
        return
    assert process.returncode == 0, process.stderr
    assert process.stdout == ""
    assert ("bom_only [R1]" in process.stderr) == bool(coordinates)
    actual = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    expected = bn.merge(bn.read_bom(bom, bn.FileMapping(**bom_config)), normalized_coordinates)
    version = 1 if "--schema-version" in options else 2
    assert actual == bn.result_dict(expected, schema_version=version,
                                    include_paths=True if "--include-paths" in options else None,
                                    include_raw="--omit-raw" not in options)
    assert expected.placements[0].bom[0].sourcing == "unknown"
    assert expected.placements[0].bom[0].mpn is None
    if version == 2:
        schema = json.loads((DOCS / "result-v2.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(actual)
        assert actual["inputs"]["coordinates"]["status"] == ("provided" if coordinates else "not_provided")


def test_cli_existing_result_and_partial_write(tmp_path, monkeypatch, capsys):
    bom, config, output = (tmp_path / name for name in ("bom.csv", "mapping.json", "result.json"))
    bom.write_bytes(b"Ref\nR1\n")
    config.write_text('{"bom":{"columns":{"ref":"Ref"}}}', encoding="utf-8")
    args = ["normalize", "--bom", str(bom), "--mapping", str(config), "--output", str(output)]
    output.write_bytes(b"existing")
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert output.read_bytes() == b"existing"
    output.unlink()

    original_open = Path.open

    class FailedWrite:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:5])
            raise OSError("simulated full disk")

    def open_with_failed_write(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == output:
            failure = FailedWrite()
            failure.stream = stream
            return failure
        return stream

    monkeypatch.setattr(Path, "open", open_with_failed_write)
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert "simulated full disk" in capsys.readouterr().err
    assert not output.exists()
    assert bom.read_bytes() == b"Ref\nR1\n"
