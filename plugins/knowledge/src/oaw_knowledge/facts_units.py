"""Unit normalisation for the fact table: compare like with like, never across dimensions.

A unit string is first canonicalised (Unicode, spacing, "g-1" exponents, "per" slashes),
so ``mAh g⁻¹``, ``mA h/g`` and ``mAh/g`` are one unit. A known unit maps to its dimension's
base unit by ``base = value * factor + offset`` (the offset is only used for °C/°F).
Unknown units are kept as given and match only the same canonical spelling.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

# dimension: (base unit as displayed, {canonical spelling: factor or (factor, offset)})
_TABLE: dict[str, tuple[str, dict[str, float | tuple[float, float]]]] = {
    "dimensionless": ("", {"": 1}),
    "percent": ("%", {"%": 1}),
    "energy": ("eV", {"eV": 1, "meV": 1e-3, "keV": 1e3, "MeV": 1e6, "Ry": 13.605693123, "Ha": 27.211386246, "hartree": 27.211386246}),
    "energy_per_atom": ("eV/atom", {"eV/atom": 1, "meV/atom": 1e-3, "eV/at": 1, "meV/at": 1e-3}),
    "molar_energy": ("kJ/mol", {"kJ/mol": 1, "J/mol": 1e-3, "kcal/mol": 4.184, "cal/mol": 4.184e-3}),
    "temperature": ("K", {"K": 1, "°C": (1, 273.15), "°F": (5 / 9, 273.15 - 32 * 5 / 9), "mK": 1e-3}),
    "specific_capacity": ("mAh/g", {"mAh/g": 1, "Ah/kg": 1, "Ah/g": 1e3, "uAh/g": 1e-3}),
    "areal_capacity": ("mAh/cm²", {"mAh/cm2": 1, "uAh/cm2": 1e-3, "Ah/m2": 0.1}),
    "conductivity": ("S/cm", {"S/cm": 1, "S/m": 1e-2, "mS/cm": 1e-3, "uS/cm": 1e-6, "mS/m": 1e-5, "nS/cm": 1e-9}),
    "resistivity": ("Ω·cm", {"ohmcm": 1, "ohmm": 100, "mohmcm": 1e-3, "uohmcm": 1e-6, "kohmcm": 1e3}),
    "pressure": ("GPa", {"GPa": 1, "MPa": 1e-3, "kPa": 1e-6, "Pa": 1e-9, "mPa": 1e-12, "TPa": 1e3, "bar": 1e-4, "kbar": 0.1,
                         "Mbar": 100, "mbar": 1e-7, "atm": 1.01325e-4, "Torr": 1.333223684e-7}),
    "length": ("Å", {"Å": 1, "angstrom": 1, "nm": 10, "pm": 1e-2, "um": 1e4, "mm": 1e7, "cm": 1e8, "m": 1e10,
                     "bohr": 0.529177210903}),
    "volume": ("Å³", {"Å3": 1, "angstrom3": 1, "nm3": 1e3, "pm3": 1e-6}),
    "density": ("g/cm³", {"g/cm3": 1, "g/cc": 1, "g/mL": 1, "g/ml": 1, "kg/m3": 1e-3, "kg/L": 1, "kg/l": 1, "mg/cm3": 1e-3}),
    "voltage": ("V", {"V": 1, "mV": 1e-3, "uV": 1e-6, "kV": 1e3, "MV": 1e6}),
    "current_density": ("mA/cm²", {"mA/cm2": 1, "A/cm2": 1e3, "uA/cm2": 1e-3, "A/m2": 0.1}),
    "specific_current": ("mA/g", {"mA/g": 1, "A/g": 1e3, "A/kg": 1, "uA/g": 1e-3}),
    "gravimetric_energy": ("Wh/kg", {"Wh/kg": 1, "mWh/g": 1, "kWh/kg": 1e3, "Wh/g": 1e3}),
    "volumetric_energy": ("Wh/L", {"Wh/L": 1, "Wh/l": 1, "mWh/cm3": 1, "Wh/dm3": 1, "kWh/m3": 1}),
    "gravimetric_power": ("W/kg", {"W/kg": 1, "kW/kg": 1e3, "mW/g": 1, "W/g": 1e3}),
    "diffusivity": ("cm²/s", {"cm2/s": 1, "m2/s": 1e4, "mm2/s": 1e-2, "um2/s": 1e-8}),
    "mobility": ("cm²/(V·s)", {"cm2/Vs": 1, "m2/Vs": 1e4}),
    "thermal_conductivity": ("W/(m·K)", {"W/mK": 1, "W/cmK": 100, "mW/mK": 1e-3}),
    "seebeck": ("μV/K", {"uV/K": 1, "mV/K": 1e3, "V/K": 1e6}),
    "surface_area": ("m²/g", {"m2/g": 1}),
    "time": ("s", {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "min": 60, "h": 3600, "hr": 3600, "d": 86400}),
    "frequency": ("Hz", {"Hz": 1, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}),
    "wavenumber": ("cm⁻¹", {"/cm": 1, "/m": 1e-2}),
    "magnetic_moment": ("μB", {"uB": 1}),
}

_LOOKUP: dict[str, tuple[str, str, float, float]] = {}
for _dimension, (_base, _units) in _TABLE.items():
    for _spelling, _factor in _units.items():
        _scale, _offset = _factor if isinstance(_factor, tuple) else (_factor, 0.0)
        _LOOKUP[_spelling] = (_dimension, _base, float(_scale), float(_offset))

_TEMPERATURE = re.compile(r"(?:°|º|o|deg\.?|degrees?)\s*([CF])", re.IGNORECASE)
_TOKEN = re.compile(r"([^\d\-+]+)([-+]?\d+)?")


@dataclass(frozen=True)
class Unit:
    given: str
    key: str                      # canonical spelling; the identity of an unknown unit
    dimension: str | None = None  # None when the unit is not in the table
    base: str | None = None
    factor: float = 1.0
    offset: float = 0.0

    def to_base(self, value: float | None) -> float | None:
        if value is None or self.dimension is None:
            return None
        # 12 significant digits: 3300 meV is 3.3 eV, not 3.3000000000000003. May be inf for huge values.
        return float(f"{value * self.factor + self.offset:.12g}")


def canonical(unit: str) -> str:
    """One spelling per unit: 'mA h g⁻¹' -> 'mAh/g', 'S cm-1' -> 'S/cm', '℃' -> '°C'."""
    text = unicodedata.normalize("NFKC", unit or "").strip()
    text = text.replace("−", "-").replace("–", "-").replace("μ", "u").replace("Ω", "ohm")
    text = re.sub(r"(?i)\bohms?\b", "ohm", text)
    text = re.sub(r"(?i)^(?:angstroms?|ångströms?)", "angstrom", text)
    if match := _TEMPERATURE.fullmatch(text):
        return "°" + match.group(1).upper()
    text = re.sub(r"[\^{}()\[\]]", "", text)
    text = re.sub(r"\s*[·*⋅•]\s*|\s+", " ", text)
    text = re.sub(r"\s*/\s*", "/", text)
    parts = text.split("/")
    numerator, denominator = [], []
    for position, part in enumerate(parts):
        for token in part.split():
            match = _TOKEN.fullmatch(token)
            if not match:
                if token != "1":
                    (numerator if position == 0 else denominator).append(token)
                continue
            symbol, exponent = match.group(1), int(match.group(2) or 1)
            below = (position > 0) != (exponent < 0)
            power = abs(exponent)
            (denominator if below else numerator).append(symbol + (str(power) if power != 1 else ""))
    return "".join(numerator) + ("/" + "".join(denominator) if denominator else "")


def resolve(unit: str | None) -> Unit:
    given = (unit or "").strip()
    key = canonical(given)
    # Case is significant: MeV/meV, Mbar/mbar and MPa/mPa differ by 1e6-1e9. Known variants are listed in _TABLE.
    found = _LOOKUP.get(key)
    if found is None:
        return Unit(given, key)
    dimension, base, factor, offset = found
    return Unit(given, key, dimension, base, factor, offset)


def dimension_of(unit: str | None) -> str | None:
    return resolve(unit).dimension


def finite(value: float | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:  # an int beyond float range
        return False


def base_units() -> dict[str, str]:
    """Dimension -> base unit, for documentation and error messages."""
    return {dimension: base for dimension, (base, _) in _TABLE.items()}


# Properties whose unit dimension is well known; a mismatch is reported as a warning, not refused.
EXPECTED = {
    "band_gap": "energy", "optical_band_gap": "energy", "work_function": "energy", "activation_energy": None,
    "formation_energy": None, "specific_capacity": "specific_capacity", "discharge_capacity": "specific_capacity",
    "charge_capacity": "specific_capacity", "reversible_capacity": "specific_capacity",
    "ionic_conductivity": "conductivity", "electronic_conductivity": "conductivity", "electrical_conductivity": "conductivity",
    "lattice_a": "length", "lattice_b": "length", "lattice_c": "length", "unit_cell_volume": "volume",
    "bulk_modulus": "pressure", "shear_modulus": "pressure", "youngs_modulus": "pressure", "hardness": "pressure",
    "density": "density", "average_voltage": "voltage", "operating_voltage": "voltage", "redox_potential": "voltage",
    "melting_point": "temperature", "curie_temperature": "temperature", "glass_transition_temperature": "temperature",
    "debye_temperature": "temperature", "critical_temperature": "temperature", "thermal_conductivity": "thermal_conductivity",
    "diffusion_coefficient": "diffusivity", "seebeck_coefficient": "seebeck", "bet_surface_area": "surface_area",
    "capacity_retention": "percent", "coulombic_efficiency": "percent",
}
