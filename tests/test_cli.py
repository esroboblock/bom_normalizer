import json
import subprocess
import sys

import pytest
from openpyxl import Workbook


@pytest.mark.parametrize("coordinate_rows", [None, "", "R2,1,2\nR2,1,3\nC1,3,4\n"])
def test_cli_distinguishes_skipped_empty_and_partial_comparison(tmp_path, coordinate_rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Ref", "Qty", "Fit", "Mount", "Policy", "Spec"])
    sheet.append(["r1-r2 ?", 3, "dnf", None, "spec_based", "10 kΩ"])
    sheet.append(["R2", 1, "yes", "tht", "spec_based", "20 kΩ"])
    bom = tmp_path / "bom.xlsx"
    workbook.save(bom)

    mapping = tmp_path / "mapping.json"
    mappings = {"bom": {"columns": {
        "ref": "Ref", "quantity": "Qty", "population": "Fit", "mount": "Mount",
        "sourcing": "Policy", "specification": "Spec",
    }}}
    output = tmp_path / "result.json"
    command = [
        sys.executable, "-m", "bom_normalizer", "normalize", "--bom", str(bom),
        "--mapping", str(mapping), "--output", str(output),
    ]
    if coordinate_rows is not None:
        coordinates = tmp_path / "coordinates.csv"
        coordinates.write_text("Ref,X,Y\n" + coordinate_rows)
        command.extend(["--coordinates", str(coordinates)])
        mapping.write_text(json.dumps(mappings))
        rejected = subprocess.run(command, capture_output=True, text=True)
        assert rejected.returncode == 2
        assert "coordinates mapping is required" in rejected.stderr
        assert not output.exists()
        mappings["coordinates"] = {"columns": {"ref": "Ref", "x": "X", "y": "Y"}, "unit": "mm"}

    mapping.write_text(json.dumps(mappings))
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(output.read_text())
    placements = {row["ref"]: row for row in payload["placements"]}
    assert placements["R1"]["population"] == "dnp"
    assert placements["R1"]["mount"] == "unknown"
    assert placements["R1"]["bom"][0]["specification"] == "10 kΩ"
    assert placements["R2"]["population"] == placements["R2"]["mount"] == "unknown"
    assert len(placements["R2"]["bom"]) == 2
    assert payload["unresolved_bom"][0]["token"] == "?"
    issues = {(issue["code"], issue["ref"]) for issue in payload["issues"]}
    assert {("invalid_ref", None), ("quantity_mismatch", None),
            ("duplicate_bom_ref", "R2"), ("bom_conflict", "R2")} <= issues
    assert "quantity_mismatch" in process.stderr
    assert "invalid_ref" in process.stderr

    if coordinate_rows is None:
        assert payload["inputs"]["coordinates"]["status"] == "not_provided"
        assert issues.isdisjoint({("bom_only", "R1"), ("bom_only", "R2")})
        assert "comparison skipped" in process.stderr
        assert "bom_only" not in process.stderr
    else:
        assert payload["inputs"]["coordinates"]["status"] == "provided"
        assert ("bom_only", "R1") in issues
        assert "bom_only [R1]" in process.stderr
        assert "comparison skipped" not in process.stderr
        if coordinate_rows:
            assert placements["R2"]["status"] == "ambiguous"
            assert placements["C1"]["status"] == "coordinate_only"
            assert {("coordinate_conflict", "R2"), ("coordinate_only", "C1")} <= issues
            assert "coordinate_conflict [R2]" in process.stderr
        else:
            assert ("bom_only", "R2") in issues
            assert "bom_only [R2]" in process.stderr


def test_cli_writes_json_and_prints_review_summary(tmp_path):
    bom = tmp_path / "bom.csv"
    bom.write_text("Ref,Qty\nR1,1\n")
    coordinates = tmp_path / "coords.csv"
    coordinates.write_text("Ref,X,Y\nR1,1,2\nC1,3,4\n")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "bom": {"columns": {"ref": "Ref", "quantity": "Qty"}},
        "coordinates": {"columns": {"ref": "Ref", "x": "X", "y": "Y"}, "unit": "mm"},
    }))
    output = tmp_path / "result.json"
    result = subprocess.run(
        [sys.executable, "-m", "bom_normalizer", "normalize", "--bom", str(bom),
         "--coordinates", str(coordinates), "--mapping", str(mapping), "--output", str(output)],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == 2
    assert payload["placements"][0]["status"] == "coordinate_only"
    assert payload["placements"][1]["coordinates"][0]["x_mm"] == "1"
    assert "coordinate_only" in result.stderr
    assert "C1" in result.stderr


def test_cli_refuses_to_overwrite_input(tmp_path):
    bom = tmp_path / "bom.csv"
    original = "Ref,Qty\nR1,1\n"
    bom.write_text(original)
    coordinates = tmp_path / "coords.csv"
    coordinates.write_text("Ref,X,Y\nR1,1,2\n")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "bom": {"columns": {"ref": "Ref"}},
        "coordinates": {"columns": {"ref": "Ref", "x": "X", "y": "Y"}, "unit": "mm"},
    }))
    result = subprocess.run(
        [sys.executable, "-m", "bom_normalizer", "normalize", "--bom", str(bom),
         "--coordinates", str(coordinates), "--mapping", str(mapping), "--output", str(bom)],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert bom.read_text() == original
