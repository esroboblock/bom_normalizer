from decimal import Decimal, localcontext

import pytest

import bom_normalizer as bn


BOM_MAPPING = {
    "ref": "Designators", "quantity": "Qty", "population": "Fit", "mount": "Mount",
    "sourcing": "Policy", "manufacturer": "Maker", "mpn": "MPN",
    "specification": "Spec", "value": "Value", "package": "Package",
}
COORD_MAPPING = {"ref": "Ref", "x": "East", "y": "North", "rotation": "Angle", "side": "Layer"}


def write(path, content):
    path.write_text(content, encoding="utf-8")
    return path


def normalize(tmp_path, bom, coords, unit="mm"):
    return bn.merge(
        bn.read_bom(
            write(tmp_path / "bom.csv", "Designators,Qty,Fit,Mount,Policy,Maker,MPN,Spec,Value,Package\n" + bom),
            bn.FileMapping(BOM_MAPPING),
        ),
        bn.read_coordinates(
            write(tmp_path / "placement.csv", "Ref,East,North,Angle,Layer\n" + coords),
            bn.FileMapping(COORD_MAPPING, unit=unit),
        ),
    )


def test_ref_ranges_and_delimiters():
    assert bn.expand_refs(" r001–r003; C2\nU1 ") == ("R001", "R002", "R003", "C2", "U1")


@pytest.mark.parametrize("ref", ["R1-C3", "R3-R1", "R01-R3", "R0-R99999999", "R1/2"])
def test_uncertain_ranges_raise(ref):
    with pytest.raises(ValueError):
        bn.expand_refs(ref)


def test_match_and_preserve_unmatched_and_provenance(tmp_path):
    result = normalize(tmp_path, "r1,1,yes,smd,fixed,Sample Parts,TEST-1,,,\nR2,1,dnp,,,,,,,\n", "R1,1,2,90,top\nC1,3,4,,\n")
    placements = {row.ref: row for row in result.placements}
    assert {ref: row.status for ref, row in placements.items()} == {
        "R1": "matched", "R2": "bom_only", "C1": "coordinate_only",
    }
    assert placements["R1"].bom[0].source.file.endswith("bom.csv")
    assert placements["R1"].bom[0].source.row == 2
    assert placements["R1"].bom[0].raw["Designators"] == "r1"
    assert placements["R1"].coordinates[0].side == "top"
    assert placements["R2"].population == "dnp"
    assert placements["R2"].mount == placements["C1"].mount == "unknown"
    assert {issue.code for issue in result.issues} >= {"bom_only", "coordinate_only"}


def test_duplicates_conflicts_and_invalid_refs_preserve_all_evidence(tmp_path):
    result = normalize(
        tmp_path,
        '"R1,R1,?oops",3,yes,smd,,,,,,\nR1,1,dnp,tht,,,,,,\n',
        "R1,1,2,0,top\nR1,1,3,0,top\n",
    )
    (row,) = result.placements
    assert len(row.bom) == 3
    assert len(row.coordinates) == 2
    assert row.status == "ambiguous"
    assert row.population == row.mount == "unknown"
    assert row.material_group is None
    assert result.unresolved_bom[0].token == "?oops"
    assert result.unresolved_bom[0].raw["Designators"] == "R1,R1,?oops"
    assert {issue.code for issue in result.issues} >= {
        "duplicate_bom_ref", "duplicate_coordinate_ref", "bom_conflict", "coordinate_conflict",
        "invalid_ref", "quantity_mismatch",
    }


def test_coordinate_units_are_exact_and_invalid_values_survive(tmp_path):
    with localcontext() as context:
        context.prec = 5
        result = normalize(tmp_path, "", "R1,1.234567890123456789,NaN,bad,maybe\n", "mil")
    (coordinate,) = result.placements[0].coordinates
    assert coordinate.x_mm == Decimal("0.0313580244091358024406")
    assert coordinate.y_mm is None
    assert coordinate.raw["North"] == "NaN"
    assert {issue.code for issue in result.issues} >= {"invalid_coordinate", "invalid_rotation", "unknown_side"}


def test_materials_preserve_spec_and_keep_ambiguous_identity_separate(tmp_path):
    result = normalize(
        tmp_path,
        'R1-R2,2,,,spec_based,,,10 kOhm 1% 0.1W,10k,0603\n'
        'R3,1,,,,,,,10k,0603\nR4,1,,,,,,,10k,0603\n'
        'R5,1,,,fixed,Sample Parts,X1,,10k,0603\nR6,1,,,fixed,Sample Parts,X1,,20k,0603\n',
        "",
    )
    rows = {row.ref: row for row in result.placements}
    assert rows["R1"].bom[0].mpn is None
    assert rows["R1"].bom[0].specification == "10 kOhm 1% 0.1W"
    assert rows["R1"].material_group == rows["R2"].material_group
    assert rows["R3"].material_group != rows["R4"].material_group
    assert rows["R5"].material_group != rows["R6"].material_group
    assert "ambiguous_material_identity" in {issue.code for issue in result.issues}


def test_xlsx_mapping_selects_sheet_and_physical_header_row(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "board.xlsx"
    workbook = Workbook()
    workbook.active.title = "Notes"
    sheet = workbook.create_sheet("Points")
    sheet.append(["Placement list"])
    sheet.append(["Name", "X", "Y"])
    sheet.append([])
    sheet.append(["C3", 1.5, -2])
    workbook.save(path)
    data = bn.read_coordinates(path, bn.FileMapping({"ref": "Name", "x": "X", "y": "Y"}, sheet="Points", header_row=2, unit="in"))
    (entry,) = data.entries
    assert (entry.x_mm, entry.y_mm) == (Decimal("38.1"), Decimal("-50.8"))
    assert (entry.source.sheet, entry.source.row) == ("Points", 4)


def test_missing_or_duplicate_headers_are_rejected(tmp_path):
    mapping = bn.FileMapping({"ref": "Name"})
    for contents in ("Other\nR1\n", "Name,Name\nR1,R2\n"):
        with pytest.raises(ValueError):
            bn.read_bom(write(tmp_path / "bom.csv", contents), mapping)


def test_coordinate_rows_with_mixed_ref_tokens_remain_unresolved(tmp_path):
    result = normalize(tmp_path, "R1,1,,,,,,,,\n", '"R1,?bad",1,2,,\n')
    assert result.placements[0].status == "bom_only"
    assert {entry.token for entry in result.unresolved_coordinates} == {"R1", "?bad"}
    assert {issue.code for issue in result.issues} >= {"multiple_coordinate_refs", "invalid_ref"}


def test_extreme_exponents_remain_invalid_evidence(tmp_path):
    result = normalize(tmp_path, "", "R1,1e9999999999999999999,2,,\n")
    (entry,) = result.placements[0].coordinates
    assert entry.x_mm is None
    assert entry.raw["East"] == "1e9999999999999999999"
    assert "invalid_coordinate" in {issue.code for issue in result.issues}


def test_formula_does_not_create_a_ref(tmp_path):
    result = normalize(tmp_path, '=R1+ R2,1,,,,,,,,\n', "")
    assert not result.placements
    assert result.unresolved_bom[0].token == "=R1+ R2"
    assert "unsupported_formula" in {issue.code for issue in result.issues}


def test_one_sided_duplicates_keep_absence_and_duplicate_evidence(tmp_path):
    result = normalize(tmp_path, "R1,1,,,,,,,,\nR1,1,,,,,,,,\n", "")
    (row,) = result.placements
    assert row.status == "bom_only"
    assert len(row.bom) == 2
    assert "duplicate_bom_ref" in {issue.code for issue in result.issues}


def test_xlsx_special_formulas_remain_serializable_evidence(tmp_path):
    import json

    from openpyxl import Workbook
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

    path = tmp_path / "board.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Ref", "X", "Y", "Unmapped"])
    sheet.append(["R1", ArrayFormula("B2:B2", "=SUM(1,2)"), 1, DataTableFormula("D2:D2", dt2D=True)])
    sheet.append([DataTableFormula("A3:A3", r1="C3"), 2, 3, None])
    workbook.save(path)

    coordinates = bn.read_coordinates(path, bn.FileMapping({"ref": "Ref", "x": "X", "y": "Y"}, unit="mm"))
    result = bn.merge(bn.BomInput((), (), ()), coordinates)
    payload = json.loads(json.dumps(bn.result_dict(result), allow_nan=False))
    (entry,) = payload["placements"][0]["coordinates"]
    assert entry["x_mm"] is None
    assert entry["raw"]["X"] == {"text": "=SUM(1,2)", "attributes": {"t": "array", "ref": "B2:B2"}}
    assert entry["raw"]["Unmapped"]["attributes"]["dt2D"] == "1"
    assert payload["unresolved_coordinates"][0]["raw"]["Ref"]["attributes"]["r1"] == "C3"
    assert [issue.code for issue in coordinates.issues].count("unsupported_formula") == 2


def test_duplicate_refs_still_supply_material_conflict_evidence(tmp_path):
    result = normalize(
        tmp_path,
        'R1,1,,,fixed,Example,X1,,10k,0603\nR2,1,,,fixed,Example,X1,,10k,0603\n'
        'R3,1,,,fixed,Example,X1,,20k,0603\nR3,1,,,fixed,Example,X2,,10k,0603\n',
        "",
    )
    first, second, duplicate = result.placements
    assert first.material_group != second.material_group
    assert duplicate.material_group is None
    conflict = next(issue for issue in result.issues if issue.ref == "R1" and issue.code == "ambiguous_material_identity")
    assert {source.row for source in conflict.sources} == {2, 3, 4}


def test_xlsx_error_is_distinct_from_literal_text(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "bom.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Ref", "Source", "Maker", "MPN"])
    sheet.append(["R1", "fixed", "Example", "#N/A"])
    sheet.append(["R2", "fixed", "Example", "#N/A"])
    sheet["D3"].data_type = "s"
    workbook.save(path)

    bom = bn.read_bom(path, bn.FileMapping({
        "ref": "Ref", "sourcing": "Source", "manufacturer": "Maker", "mpn": "MPN",
    }))
    first, second = bom.entries
    assert first.mpn is None
    assert second.mpn == "#N/A"
    result = bn.merge(bom, bn.CoordinateInput((), (), ()))
    assert bn.result_dict(result)["placements"][0]["bom"][0]["raw"]["MPN"] == {"error": "#N/A"}
    assert [issue.code for issue in bom.issues] == ["source_cell_error"]
