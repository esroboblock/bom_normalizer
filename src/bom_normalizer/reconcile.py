"""Deterministic Ref joins and conservative result-local material grouping."""

from collections import defaultdict
from dataclasses import replace

from .models import BomEntry, BomInput, CoordinateInput, Placement, Result, Review

_MATERIAL_FIELDS = ("sourcing", "manufacturer", "mpn", "specification", "value", "package")
_BOM_FIELDS = ("population", "mount", *_MATERIAL_FIELDS)
_COORDINATE_FIELDS = ("x_mm", "y_mm", "rotation_deg", "side")


def _signature(entry, attributes):
    return tuple(getattr(entry, attribute) for attribute in attributes)


def _material_identity(entry: BomEntry) -> tuple[str, ...] | None:
    if entry.sourcing == "fixed" and entry.manufacturer and entry.mpn:
        return ("fixed", entry.manufacturer, entry.mpn)
    if entry.sourcing == "spec_based" and entry.specification:
        return ("spec_based", entry.specification)
    return None


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
    materials = defaultdict(list)
    for entry in bom.entries:
        if (identity := _material_identity(entry)) is not None:
            materials[identity].append(entry)
    identity_conflicts = {
        identity: tuple(dict.fromkeys(entry.source for entry in entries))
        for identity, entries in materials.items()
        if len({_signature(entry, _MATERIAL_FIELDS) for entry in entries}) > 1
    }

    groups = {}
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
        if len(bom_entries) == 1:
            entry = bom_entries[0]
            identity = _material_identity(entry)
            if identity is None or identity in identity_conflicts:
                group_key = ("ref", ref)
                issues.append(Review(
                    "ambiguous_material_identity",
                    "Material evidence is incomplete or disagrees with another entry.",
                    identity_conflicts.get(identity, (entry.source,)), ref,
                ))
            else:
                group_key = identity
            group = groups.setdefault(group_key, f"material-{len(groups) + 1}")

        populations = {entry.population for entry in bom_entries}
        mounts = {entry.mount for entry in bom_entries}
        population = next(iter(populations)) if len(populations) == 1 else "unknown"
        mount = next(iter(mounts)) if len(mounts) == 1 else "unknown"
        placements.append(Placement(ref, status, bom_entries, coordinate_entries, population, mount, group))

    return Result(
        tuple(placements), bom.unresolved, coordinates.unresolved, tuple(issues),
        coordinates_provided=coordinates_provided,
        bom_evidence=bom.evidence,
        coordinate_evidence=coordinates.evidence,
    )
