import csv

import pytest

import bom_normalizer as bn


def reconcile(tmp_path, rows):
    path = tmp_path / "bom.csv"
    columns = ("ref", "sourcing", "manufacturer", "mpn", "specification", "value", "package", "mount")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)
    bom = bn.read_bom(path, bn.FileMapping({column: column for column in columns}))
    return bn.merge(bom)


def test_equivalent_resistor_specs_are_candidates_without_merging(tmp_path):
    result = reconcile(tmp_path, [
        ("R1", "spec_based", "", "", "resistor ±1% 0.1W thick-film", "10kΩ", "0603 imperial"),
        ("R2", "spec_based", "", "", "Resistor 100 mW 1% thick-film", "10K", "0603 IMPERIAL"),
        ("R3", "spec_based", "", "", "resistor 1% 0.25W thick-film", "10K", "0603 imperial"),
    ])
    first, second, different = bn.result_dict(result)["placements"]
    a, b, c = (row["material_assessment"] for row in (first, second, different))
    assert a["status"] == b["status"] == "candidate"
    assert a["candidate_group"] == b["candidate_group"] == "candidate-1"
    assert a["specification"] == b["specification"]
    assert a["specification"]["resistance_ohm"] == "10000"
    assert a["specification"]["power_w"] == "0.1"
    assert c["status"] == "held"
    assert c["candidate_group"] is None
    assert first["material_group"] != second["material_group"]
    assert first["bom"][0]["source"]["row"] == 2
    assert first["bom"][0]["mpn"] is None


@pytest.mark.parametrize(("specification", "value", "package", "reason"), [
    ("capacitor 100nF 10% X7R", "", "0603 imperial", "incomplete_specification"),
    ("capacitor 100nF 10% 50V X7R", "", "0603", "ambiguous_package"),
    ("capacitor 100nF 10% 50V X7R 85°C", "", "0603 imperial", "unsupported_specification"),
    ("capacitor 100nF 10% 50V X7R", "0.2uF", "0603 imperial", "conflicting_specification"),
    ("resistor 10kΩ 10% 50V thick-film", "", "0603 imperial", "incomplete_specification"),
])
def test_identical_text_with_unresolved_evidence_stays_separate(tmp_path, specification, value, package, reason):
    result = reconcile(tmp_path, [
        (ref, "spec_based", "", "", specification, value, package) for ref in ("C1", "C2")
    ])
    first, second = result.placements
    assert first.material_group != second.material_group
    assert first.material_assessment.status == second.material_assessment.status == "held"
    assert first.material_assessment.reason == second.material_assessment.reason == reason
    assert {issue.ref for issue in result.issues if issue.code == "ambiguous_material_identity"} == {"C1", "C2"}


def test_capacitor_candidates_require_matching_dielectric_and_package_system(tmp_path):
    result = reconcile(tmp_path, [
        ("C1", "spec_based", "", "", "capacitor 100 nF 10% 50 V X7R", "", "0603 imperial"),
        ("C2", "spec_based", "", "", "capacitor 10% 50V X7R", "0.1µF", "0603 imperial"),
        ("C3", "spec_based", "", "", "capacitor 10% 50V X5R", "0.1uF", "0603 imperial"),
        ("C4", "spec_based", "", "", "capacitor 10% 50V X7R", "0.1uF", "0603 metric"),
    ])
    first, second, *different = (row.material_assessment for row in result.placements)
    assert first.candidate_group == second.candidate_group == "candidate-1"
    assert first.specification == second.specification
    assert all(row.candidate_group is None for row in different)


@pytest.mark.parametrize(("mounts", "mount_status"), [
    (("smd", "smd"), "matched"),
    (("smd", "", "unknown"), "unverified"),
    (("", "unknown"), "unverified"),
])
def test_candidate_group_tracks_mount_verification_separately(tmp_path, mounts, mount_status):
    specification = "resistor 10kΩ 1% 0.1W thick-film"
    result = reconcile(tmp_path, [
        (f"R{index}", "spec_based", "", "", specification, "", "0603 imperial", mount)
        for index, mount in enumerate((*mounts, "tht"), start=1)
    ])
    *candidates, held = bn.result_dict(result)["placements"]
    assert [row["mount"] for row in candidates] == [mount or "unknown" for mount in mounts]
    assert len({row["material_group"] for row in candidates}) == len(candidates)
    for row in candidates:
        assessment = row["material_assessment"]
        assert assessment["status"] == "candidate"
        assert assessment["reason"] == "matching_specification"
        assert assessment["candidate_group"] == "candidate-1"
        assert assessment["mount_status"] == mount_status
    assert held["mount"] == "tht"
    assert held["material_assessment"]["status"] == "held"
    assert held["material_assessment"]["reason"] == "conflicting_specification"
    assert held["material_assessment"]["mount_status"] is None


def test_fixed_parts_require_both_identifiers_and_consistent_evidence(tmp_path):
    result = reconcile(tmp_path, [
        ("R1", "fixed", "Example A", "X1", "", "10k", ""),
        ("R2", "fixed", "Example A", "X1", "", "10k", ""),
        ("R3", "fixed", "Example B", "X1", "", "10k", ""),
        ("R4", "fixed", "", "X1", "", "10k", ""),
        ("R5", "unknown", "Example A", "X1", "", "10k", ""),
    ])
    first, second, other_maker, incomplete, unknown = result.placements
    assert first.material_group == second.material_group
    assert first.material_assessment.status == second.material_assessment.status == "confirmed"
    assert other_maker.material_group != first.material_group
    assert incomplete.material_assessment.reason == "missing_fixed_identifier"
    assert unknown.material_assessment.reason == "unknown_sourcing"


def test_invalid_mapped_material_cell_blocks_candidate(tmp_path):
    result = reconcile(tmp_path, [
        ("R1", "spec_based", "", "", "resistor 10kΩ 1% 0.1W thick-film", "=1", "0603 imperial"),
        ("R2", "spec_based", "", "", "resistor 10kΩ 1% 0.1W thick-film", "", "0603 imperial"),
    ])
    first, second = (row.material_assessment for row in result.placements)
    assert first.reason == "invalid_material_evidence"
    assert second.reason == "no_matching_specification"


def test_zero_ohm_resistors_can_be_candidates(tmp_path):
    result = reconcile(tmp_path, [
        (ref, "spec_based", "", "", "resistor 0Ω 1% 0.1W thick-film", "", "0603 imperial")
        for ref in ("R1", "R2")
    ])
    assert all(row.material_assessment.status == "candidate" for row in result.placements)


def test_fixed_identifier_does_not_override_conflicting_zero_ohm_value(tmp_path):
    result = reconcile(tmp_path, [
        ("R1", "fixed", "Example", "X1", "resistor 10Ω", "0Ω", "0603 imperial"),
    ])
    (row,) = result.placements
    assert row.material_assessment.reason == "conflicting_specification"


@pytest.mark.parametrize(("specification", "package", "reason"), [
    ("resistor 10kΩ 1% 0.1W thick-film 85°C", "0603 imperial", "unsupported_specification"),
    ("capacitor 100nF 10% 50V X7R", "0603", "ambiguous_package"),
])
def test_fixed_chip_with_uninterpretable_evidence_is_held(tmp_path, specification, package, reason):
    result = reconcile(tmp_path, [
        (ref, "fixed", "Example", "X100", specification, "", package) for ref in ("R1", "R2")
    ])
    first, second = bn.result_dict(result)["placements"]
    assert first["material_group"] != second["material_group"]
    assert all(row["material_assessment"]["status"] == "held" for row in (first, second))
    assert all(row["material_assessment"]["reason"] == reason for row in (first, second))
    assert {issue.ref for issue in result.issues if issue.code == "ambiguous_material_identity"} == {"R1", "R2"}


@pytest.mark.parametrize(("specification", "package"), [
    ("connector 8-pin 85°C", "Through-hole connector"),
    ("resistor 10kΩ", "0603 imperial"),
])
def test_fixed_identity_can_use_mpn_without_complete_chip_spec(tmp_path, specification, package):
    result = reconcile(tmp_path, [
        (ref, "fixed", "Example", "X100", specification, "", package) for ref in ("X1", "X2")
    ])
    first, second = result.placements
    assert first.material_group == second.material_group
    assert first.material_assessment.status == second.material_assessment.status == "confirmed"


def test_explicit_mount_conflicts_hold_material_identity(tmp_path):
    result = reconcile(tmp_path, [
        ("R1", "fixed", "Example", "X1", "", "", "", "smd"),
        ("R2", "fixed", "Example", "X1", "", "", "", "tht"),
        ("R3", "spec_based", "", "", "resistor 10kΩ 1% 0.1W thick-film", "", "0603 imperial", "tht"),
        ("R4", "spec_based", "", "", "resistor 10kΩ 1% 0.1W thick-film", "", "0603 imperial", "tht"),
    ])
    first, second, third, fourth = (row.material_assessment for row in result.placements)
    assert first.reason == second.reason == "conflicting_fixed_evidence"
    assert third.reason == fourth.reason == "conflicting_specification"
