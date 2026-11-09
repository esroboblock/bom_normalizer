import json
import subprocess
import sys


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
