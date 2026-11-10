"""Deterministic Ref joins and conservative result-local material grouping."""

from collections import defaultdict
from dataclasses import replace

from .materials import parse_specification
from .models import BomEntry, BomInput, CoordinateInput, MaterialAssessment, Placement, Result, Review

_MATERIAL_FIELDS = ("sourcing", "manufacturer", "mpn", "specification", "value", "package")
_BOM_FIELDS = ("population", "mount", *_MATERIAL_FIELDS)
_COORDINATE_FIELDS = ("x_mm", "y_mm", "rotation_deg", "side")


def _signature(entry, attributes):
    return tuple(getattr(entry, attribute) for attribute in attributes)


def _fixed_identity(entry: BomEntry) -> tuple[str, ...] | None:
    if entry.sourcing == "fixed" and entry.manufacturer and entry.mpn:
        return ("fixed", entry.manufacturer, entry.mpn)
    return None


_HELD_MESSAGES = {
    "unknown_sourcing": "Material sourcing is unknown.",
    "missing_fixed_identifier": "A fixed part requires both manufacturer and MPN.",
    "conflicting_fixed_evidence": "BOM evidence for this manufacturer and MPN disagrees or is unreadable.",
    "invalid_material_evidence": "A mapped material field could not be read.",
    "conflicting_specification": "Declared specification values disagree.",
    "unsupported_specification": "The specification contains unrecognized values or conditions.",
    "ambiguous_package": "The package must declare its code and unit system.",
    "incomplete_specification": "The declared specification is incomplete.",
}


def _reconcile_side(ref, kind, entries, attributes, issues):
    if len(entries) < 2:
        return
    sources = tuple(dict.fromkeys(entry.source for entry in entries))
    issues.append(Review(f"duplicate_{kind}_ref", f"Multiple {kind} entries for this Ref.", sources, ref))
    if len({_signature(entry, attributes) for entry in entries}) > 1:
        issues.append(Review(f"{kind}_conflict", f"{kind.capitalize()} entries disagree.", sources, ref))


def merge(bom: BomInput, coordinates: CoordinateInput | None = None) -> Result:
    """Validate and join by Ref while retaining all evidence.

    Pass None to skip coordinate comparison. An empty input still participates
    in the comparison and produces a review for each unmatched BOM Ref.
    """
    bom_by_ref = defaultdict(list)
    coord_by_ref = defaultdict(list)
    coordinates_provided = coordinates is not None
    if coordinates is None:
        coordinates = CoordinateInput((), (), ())
    for entry in bom.entries:
        bom_by_ref[entry.ref].append(entry)
    for entry in coordinates.entries:
        coord_by_ref[entry.ref].append(entry)

    issues = [replace(issue, input_role="bom") for issue in bom.issues]
    issues.extend(replace(issue, input_role="coordinates") for issue in coordinates.issues)
    invalid_sources = {
        source for issue in bom.issues
        if (issue.severity == "error" and issue.field in (*_MATERIAL_FIELDS, "mount"))
        or issue.code == "unknown_mount"
        for source in issue.sources
    }
    materials = defaultdict(list)
    for entry in bom.entries:
        if (identity := _fixed_identity(entry)) is not None:
            materials[identity].append(entry)
    identity_conflicts = {
        identity: tuple(dict.fromkeys(entry.source for entry in entries))
        for identity, entries in materials.items()
        if len({_signature(entry, _MATERIAL_FIELDS) for entry in entries}) > 1
        or len({entry.mount for entry in entries} - {"unknown"}) > 1
        or any(entry.source in invalid_sources for entry in entries)
    }

    parsed = {}
    candidate_keys = {}
    candidate_counts = defaultdict(int)
    for ref, entries in bom_by_ref.items():
        if len(entries) != 1:
            continue
        entry = entries[0]
        specification, reason = parse_specification(entry)
        if entry.source in invalid_sources:
            reason = "invalid_material_evidence"
        parsed[ref] = specification, reason
        if entry.sourcing == "spec_based" and reason is None:
            key = (specification, entry.mount, entry.manufacturer, entry.mpn)
            candidate_keys[ref] = key
            candidate_counts[key] += 1

    groups = {}
    candidates = {}
    placements = []
    for ref in sorted(bom_by_ref.keys() | coord_by_ref.keys()):
        bom_entries = tuple(bom_by_ref.get(ref, ()))
        coordinate_entries = tuple(coord_by_ref.get(ref, ()))
        evidence = bom_entries + coordinate_entries
        sources = tuple(dict.fromkeys(entry.source for entry in evidence))
        if bom_entries and coordinate_entries:
            status = "matched" if len(bom_entries) == len(coordinate_entries) == 1 else "ambiguous"
        elif bom_entries:
            status = "bom_only"
            if coordinates_provided:
                issues.append(Review(status, "Ref appears in the BOM only.", sources, ref))
        else:
            status = "coordinate_only"
            issues.append(Review(status, "Ref appears in the coordinates only.", sources, ref))

        _reconcile_side(ref, "bom", bom_entries, _BOM_FIELDS, issues)
        _reconcile_side(ref, "coordinate", coordinate_entries, _COORDINATE_FIELDS, issues)
        group = None
        assessment = MaterialAssessment("held", "duplicate_bom" if bom_entries else "no_bom")
        if len(bom_entries) == 1:
            entry = bom_entries[0]
            specification, spec_reason = parsed[ref]
            identity = _fixed_identity(entry)
            group_key = ("ref", ref)
            if spec_reason == "invalid_material_evidence":
                assessment = MaterialAssessment("held", spec_reason, specification)
            elif entry.sourcing == "fixed":
                if identity is None:
                    assessment = MaterialAssessment("held", "missing_fixed_identifier", specification)
                elif identity in identity_conflicts:
                    assessment = MaterialAssessment("held", "conflicting_fixed_evidence", specification)
                elif spec_reason == "conflicting_specification" or (
                    specification is not None
                    and specification.category is not None
                    and spec_reason in {"unsupported_specification", "ambiguous_package"}
                ):
                    assessment = MaterialAssessment("held", spec_reason, specification)
                else:
                    assessment = MaterialAssessment("confirmed", "manufacturer_mpn", specification)
                    group_key = identity
            elif entry.sourcing == "spec_based":
                key = candidate_keys.get(ref)
                if key is not None and candidate_counts[key] > 1:
                    candidate = candidates.setdefault(key, f"candidate-{len(candidates) + 1}")
                    assessment = MaterialAssessment("candidate", "matching_specification", specification, candidate)
                else:
                    assessment = MaterialAssessment("held", spec_reason or "no_matching_specification", specification)
            else:
                assessment = MaterialAssessment("held", "unknown_sourcing", specification)

            if assessment.reason in _HELD_MESSAGES:
                issues.append(Review(
                    "ambiguous_material_identity",
                    _HELD_MESSAGES[assessment.reason],
                    identity_conflicts.get(identity, (entry.source,)), ref,
                ))
            group = groups.setdefault(group_key, f"material-{len(groups) + 1}")

        populations = {entry.population for entry in bom_entries}
        mounts = {entry.mount for entry in bom_entries}
        population = next(iter(populations)) if len(populations) == 1 else "unknown"
        mount = next(iter(mounts)) if len(mounts) == 1 else "unknown"
        placements.append(Placement(ref, status, bom_entries, coordinate_entries, population, mount, group, assessment))

    return Result(
        tuple(placements), bom.unresolved, coordinates.unresolved, tuple(issues),
        coordinates_provided=coordinates_provided,
        bom_evidence=bom.evidence,
        coordinate_evidence=coordinates.evidence,
    )
