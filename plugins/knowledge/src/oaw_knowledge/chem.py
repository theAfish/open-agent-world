"""Chemical formula normalisation shared by the stores (no third-party dependency).

A material is matched by keys, not by how a paper spells it:

* ``reduced``: the reduced formula in Hill-like element order (e.g. ``FeLiO4P``) for
  exact composition matches; fractional amounts are kept as given (``Li0.5CoO2`` -> ``CoLi0.5O2``);
* ``chemsys``: the sorted element set joined by ``-`` (``Fe-Li-O-P``), for "anything in
  this chemical system";
* ``elements``: the element symbols.

Unicode subscripts, middle dots (hydrates), parentheses, brackets and decimal amounts are
understood. Names such as "lithium iron phosphate" or abbreviations such as "LFP" are not
formulas: the Ontology store's aliases resolve those to a formula.
"""
from __future__ import annotations

import math
import re
import unicodedata
from fractions import Fraction

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr "
    "Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu "
    "Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr "
    "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og D T"
).split()
_SYMBOLS = set(ELEMENTS)


class FormulaError(ValueError):
    pass


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip()
    # Adduct dots become "·"; a plain "." is always a decimal point (O4.5 is not a hydrate).
    return re.sub(r"[•∙⋅*]", "·", text)


def parse(formula: str) -> dict[str, Fraction]:
    """Element amounts of ``formula``. Raises FormulaError for anything that is not a formula."""
    text = _clean(formula)
    if not text or len(text) > 200:
        raise FormulaError("Empty or too long formula")
    total: dict[str, Fraction] = {}
    # Hydrates and adducts: "CuSO4·5H2O" -> CuSO4 + 5 H2O.
    for index, part in enumerate(text.split("·")):
        multiplier = Fraction(1)
        if index:
            match = re.match(r"(\d+(?:\.\d+)?)", part)
            if match:
                multiplier, part = Fraction(match.group(1)), part[match.end():]
        for element, amount in _parse_group(part).items():
            total[element] = total.get(element, Fraction(0)) + amount * multiplier
    total = {element: amount for element, amount in total.items() if amount > 0}
    if not total:
        raise FormulaError(f"Not a formula: {formula!r}")
    return total


def _parse_group(text: str) -> dict[str, Fraction]:
    text = re.sub(r"\s+", "", text)
    amounts, position = _sequence(text, 0)
    if position != len(text):
        raise FormulaError(f"Unexpected {text[position]!r} in formula")
    return amounts


_ELEMENT = re.compile(r"[A-Z][a-z]?")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CLOSING = {"(": ")", "[": "]", "{": "}"}


def _sequence(text: str, position: int) -> tuple[dict[str, Fraction], int]:
    """Parse elements and bracketed groups, each with an optional amount, until a closing bracket."""
    amounts: dict[str, Fraction] = {}
    while position < len(text) and text[position] not in ")]}":
        if text[position] in _CLOSING:
            closing = _CLOSING[text[position]]
            group, position = _sequence(text, position + 1)
            if position >= len(text) or text[position] != closing:
                raise FormulaError("Unbalanced brackets")
            position += 1
        else:
            match = _ELEMENT.match(text, position)
            if not match:
                raise FormulaError(f"Unexpected {text[position]!r} in formula")
            if match.group() not in _SYMBOLS:
                raise FormulaError(f"Unknown element {match.group()!r}")
            group, position = {match.group(): Fraction(1)}, match.end()
        number = _NUMBER.match(text, position)
        multiplier = Fraction(number.group()) if number else Fraction(1)
        position = number.end() if number else position
        _merge(amounts, {element: amount * multiplier for element, amount in group.items()})
    return amounts, position


def _merge(into: dict[str, Fraction], group: dict[str, Fraction]) -> None:
    for element, amount in group.items():
        into[element] = into.get(element, Fraction(0)) + amount


def _order(elements) -> list[str]:
    # Hill order: C, then H, then the rest alphabetically (without carbon: all alphabetical).
    elements = sorted(elements)
    if "C" in elements:
        rest = [e for e in elements if e not in {"C", "H"}]
        return ["C", *(["H"] if "H" in elements else []), *rest]
    return elements


def _amount(value: Fraction) -> str:
    if value == 1:
        return ""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.6g}"


def reduced(amounts: dict[str, Fraction]) -> str:
    """Reduced formula: whole-number amounts divided by their gcd; fractional amounts kept as given."""
    if all(value.denominator == 1 for value in amounts.values()):
        divisor = math.gcd(*(value.numerator for value in amounts.values()))
        amounts = {element: value / divisor for element, value in amounts.items()}
    return "".join(f"{element}{_amount(amounts[element])}" for element in _order(amounts))


def keys(formula: str) -> dict:
    """``{"reduced", "chemsys", "elements"}`` for a formula string; raises FormulaError."""
    amounts = parse(formula)
    elements = sorted(amounts)
    return {"reduced": reduced(amounts), "chemsys": "-".join(elements), "elements": elements}


def try_keys(formula: str | None) -> dict | None:
    try:
        return keys(formula) if formula else None
    except FormulaError:
        return None


def chemsys_of(elements: list[str] | str) -> str:
    """Normalise an element list or 'Li-Fe-O' / 'Li,Fe,O' into the sorted chemsys key."""
    if isinstance(elements, str):
        elements = re.split(r"[-,\s]+", elements.strip())
    unknown = [e for e in elements if e and e not in _SYMBOLS]
    if unknown:
        raise FormulaError(f"Unknown element {unknown[0]!r}")
    return "-".join(sorted({e for e in elements if e}))
