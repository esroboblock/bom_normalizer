import json
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

import bom_normalizer as bn


def test_v2_preserves_evidence_and_distinguishes_missing_coordinates(tmp_path):
    path = tmp_path / "private.csv"
    path.write_text("Ref,MPN,Population,Note\n\"r1,R2,?\",,bad,private note\nR1,X,,other\n")
    mapping = bn.FileMapping({"ref": "Ref", "mpn": "MPN", "population": "Population"})
    bom = bn.read_bom(path, mapping)
    result = bn.merge(bom)
    payload = bn.result_dict(result)
    assert payload["schema_version"] == 2
    assert payload["inputs"]["coordinates"]["status"] == "not_provided"
    assert payload["inputs"]["bom"]["mapping"]["columns"] == mapping.columns
    assert payload["inputs"]["bom"]["source"]["file"] == "source-1"
    first, second = payload["placements"]
    assert first["status"] == "bom_only"
    assert len(first["bom"]) == 2
    assert first["bom"][0]["mpn"] is None
    assert second["population"] == "unknown"
    assert payload["unresolved_bom"][0]["token"] == "?"
    review = next(issue for issue in payload["issues"] if issue["code"] == "unknown_population")
    assert review["field"] == "population"
    assert review["input_role"] == "bom"
    assert review["severity"] == "warning"
    assert review["sources"] == [first["bom"][0]["source"]]
    assert "bom_only" not in {issue["code"] for issue in payload["issues"]}
    assert str(path) not in json.dumps(payload)
    assert payload == json.loads(json.dumps(bn.result_dict(bn.merge(bom)), allow_nan=False))

    reordered = bn.FileMapping(dict(reversed(list(mapping.columns.items()))))
    assert json.dumps(payload) == json.dumps(bn.result_dict(bn.merge(bn.read_bom(path, reordered))))

    coordinate_path = tmp_path / "empty.csv"
    coordinate_path.write_text("Ref,X,Y\n")
    coordinates = bn.read_coordinates(coordinate_path, bn.FileMapping({"ref": "Ref", "x": "X", "y": "Y"}, unit="mm"))
    empty = bn.merge(bom, coordinates)
    coordinate_input = bn.result_dict(empty)["inputs"]["coordinates"]
    assert coordinate_input["status"] == "provided"
    assert coordinate_input["source"] == {"file": "source-2", "row": 1, "sheet": None}
    assert "bom_only" in {issue.code for issue in empty.issues}
    assert bn.result_dict(empty, schema_version=1)["schema_version"] == 1
    assert str(path) in json.dumps(bn.result_dict(result, include_paths=True))
    redacted = bn.result_dict(result, include_raw=False)
    assert redacted["evidence_mode"] == "omit_raw"
    assert redacted["placements"][0]["bom"][0]["raw"] is None
    assert redacted["issues"][0]["message"] is None
    assert redacted["unresolved_bom"][0]["token"] is None
    assert "private note" not in json.dumps(redacted)


def test_v2_preserves_raw_types():
    row = bn.RawRow(bn.Source("file", 2, "sheet"), {
        "Ref": "R1", "date": date(2026, 1, 1), "decimal": Decimal("1.20"),
        "formula": bn.Formula("=1", {"t": "normal"}), "error": bn.CellError("#N/A"),
        "text": "#N/A",
    })
    bom = bn.normalize_bom(bn.Table(tuple(row.raw), (row,)), bn.FileMapping({"ref": "Ref"}))
    raw = bn.result_dict(bn.merge(bom))["placements"][0]["bom"][0]["raw"]
    assert raw["date"] == {"type": "date", "value": "2026-01-01"}
    assert raw["decimal"] == {"type": "decimal", "value": "1.20"}
    assert raw["formula"] == {"type": "formula", "text": "=1", "attributes": {"t": "normal"}}
    assert raw["error"] == {"type": "cell_error", "error": "#N/A"}
    assert raw["text"] == "#N/A"


def test_reviews_distinguish_inputs_that_share_a_source():
    row = bn.RawRow(bn.Source("workbook", 2, "Board"), {"BomRef": "?", "CoordRef": "*", "X": "1", "Y": "1"})
    table = bn.Table(tuple(row.raw), (row,))
    bom = bn.normalize_bom(table, bn.FileMapping({"ref": "BomRef"}))
    coordinates = bn.normalize_coordinates(table, bn.FileMapping({"ref": "CoordRef", "x": "X", "y": "Y"}, unit="mm"))
    bom_issue, coordinate_issue = bn.result_dict(bn.merge(bom, coordinates), include_raw=False)["issues"]
    assert bom_issue["sources"] == coordinate_issue["sources"]
    assert bom_issue["input_role"] == "bom"
    assert coordinate_issue["input_role"] == "coordinates"


def test_cli_bom_only_and_privacy(tmp_path):
    bom = tmp_path / "private.csv"
    bom.write_text("Ref,Note\n?,private note\n")
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"bom":{"columns":{"ref":"Ref"}}}')
    output = tmp_path / "output.json"
    process = subprocess.run(
        [sys.executable, "-m", "bom_normalizer", "normalize", "--bom", str(bom),
         "--mapping", str(mapping), "--output", str(output), "--omit-raw"],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(output.read_text())["inputs"]["coordinates"]["status"] == "not_provided"
    assert "private" not in output.read_text() + process.stderr


def test_v2_schema_accepts_example_and_cli_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "docs/result-v2.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    example = json.loads((root / "examples/result-v2.json").read_text())
    validator.validate(example)
    for name, flags in (("full", []), ("omit", ["--omit-raw"]), ("legacy", ["--schema-version", "1"])):
        output = tmp_path / f"{name}.json"
        command = [
            sys.executable, "-m", "bom_normalizer", "normalize",
            "--bom", "examples/bom.csv", "--coordinates", "examples/coordinates.csv",
            "--mapping", "examples/mapping.json", "--output", str(output), *flags,
        ]
        subprocess.run(command, cwd=root, capture_output=True, text=True, check=True)
        payload = json.loads(output.read_text())
        if name == "legacy":
            assert set(payload) == {"schema_version", "placements", "unresolved_bom", "unresolved_coordinates", "issues"}
            assert payload["schema_version"] == 1
            assert "severity" not in payload["issues"][0]
        else:
            validator.validate(payload)
            if name == "full":
                assert payload == example
                repeat = tmp_path / "repeat.json"
                command[-1] = str(repeat)
                subprocess.run(command, cwd=root, capture_output=True, text=True, check=True)
                assert output.read_bytes() == repeat.read_bytes()
