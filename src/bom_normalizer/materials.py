"""Parse a bounded vocabulary of explicitly declared chip specifications."""

import re
from decimal import Decimal

from .models import BomEntry, MaterialSpecification

_CATEGORY = re.compile(r"^(resistor|capacitor)(?=$|[\s,;])", re.IGNORECASE)
_SEPARATOR = re.compile(r"[\s,;]+")
_NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_MEASUREMENT = re.compile(
    rf"(?P<sign>±|\+/-)?\s*(?P<number>{_NUMBER})\s*"
    r"(?P<unit>[kKMm]?(?:[ΩΩ]|(?i:ohms?))|[pnuµμm]?[Ff]|m?[WwVv]|[kKMm]|%)"
    r"(?=$|[\s,;])"
)
_WORD = re.compile(r"[^\s,;]+")
_PACKAGE = re.compile(r"([0-9]{4})\s+(metric|imperial)", re.IGNORECASE)
_PREFIX_EXPONENTS = {"": 0, "m": -3, "k": 3, "K": 3, "M": 6, "u": -6, "µ": -6, "μ": -6, "n": -9, "p": -12}
_TECHNOLOGIES = {
    "resistor": {"thick-film", "thin-film"},
    "capacitor": {"c0g", "x5r", "x7r"},
}


def _measurement(match: re.Match, category: str | None) -> tuple[str, str] | None:
    number, unit = match["number"], match["unit"]
    if len(number) > 32 or (match["sign"] and unit != "%"):
        return None
    if unit == "%":
        field, prefix = "tolerance_percent", ""
    elif unit in {"k", "K", "M", "m"}:
        if category != "resistor":
            return None
        field, prefix = "resistance_ohm", unit
    elif unit.casefold().endswith(("ω", "ohm", "ohms")):
        field = "resistance_ohm"
        prefix = unit[0] if unit[0] in _PREFIX_EXPONENTS else ""
    else:
        field = {"f": "capacitance_f", "w": "power_w", "v": "voltage_v"}[unit[-1].lower()]
        prefix = unit[:-1]

    decimal = Decimal(number)
    if decimal.is_zero() and field not in {"resistance_ohm", "tolerance_percent"}:
        return None
    sign, digits, exponent = decimal.as_tuple()
    scaled = Decimal((sign, digits, exponent + _PREFIX_EXPONENTS[prefix]))
    value = format(scaled, "f")
    return field, value.rstrip("0").rstrip(".") if "." in value else value


def parse_specification(entry: BomEntry) -> tuple[MaterialSpecification | None, str | None]:
    """Return supported fields and a reason if the spec cannot support a candidate."""
    text = entry.specification or ""
    category_match = _CATEGORY.match(text)
    category = category_match[0].lower() if category_match else None
    attributes = {}
    conflicts = set()
    unsupported = False

    def record(field: str, value: str) -> None:
        if attributes.setdefault(field, value) != value:
            conflicts.add(field)

    position = category_match.end() if category_match else 0
    while position < len(text):
        if separator := _SEPARATOR.match(text, position):
            position = separator.end()
            continue
        if match := _MEASUREMENT.match(text, position):
            measurement = _measurement(match, category)
            if measurement is None:
                unsupported = True
            else:
                record(*measurement)
            position = match.end()
            continue
        word = _WORD.match(text, position)
        technology = word[0].lower()
        if technology in _TECHNOLOGIES.get(category, ()):
            record("technology", technology)
        else:
            unsupported = True
        position = word.end()

    if entry.value:
        match = _MEASUREMENT.fullmatch(entry.value)
        measurement = _measurement(match, category) if match else None
        if measurement and measurement[0] in {"resistance_ohm", "capacitance_f"}:
            record(*measurement)
        else:
            unsupported = True

    package = _PACKAGE.fullmatch(entry.package or "")
    if package:
        attributes["package"] = f"{package[1]} {package[2].lower()}"

    if category == "resistor" and "capacitance_f" in attributes:
        conflicts.add("capacitance_f")
    if category == "capacitor":
        conflicts.update(attributes.keys() & {"resistance_ohm", "power_w"})

    specification = MaterialSpecification(
        category=category, **{field: value for field, value in attributes.items() if field not in conflicts}
    ) if category or attributes else None
    if conflicts or (package and category and entry.mount == "tht"):
        return specification, "conflicting_specification"
    if unsupported:
        return specification, "unsupported_specification"
    if entry.package and not package:
        return specification, "ambiguous_package"
    required = {"tolerance_percent", "package", "technology"}
    if category == "resistor":
        required.update(("resistance_ohm", "power_w"))
    elif category == "capacitor":
        required.update(("capacitance_f", "voltage_v"))
    if category is None or required - attributes.keys():
        return specification, "incomplete_specification"
    return specification, None
