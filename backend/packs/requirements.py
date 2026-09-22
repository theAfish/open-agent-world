"""Offline direct-conflict checks; uv remains the full dependency resolver."""
from __future__ import annotations

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from backend.sandbox.python_runtime import validate_requirements


def aggregate_requirements(declarations: dict[str, tuple[str, ...] | list[str]]) -> list[str]:
    groups: dict[str, list[tuple[str, Requirement]]] = {}
    for owner, requirements in declarations.items():
        for value in validate_requirements(requirements):
            requirement = Requirement(value)
            groups.setdefault(canonicalize_name(requirement.name), []).append((owner, requirement))
    for name, group in groups.items():
        specs = [s for _, requirement in group for s in requirement.specifier]
        pins = [s.version for s in specs if s.operator in {"==", "==="} and "*" not in s.version]
        lower, upper = None, None
        for spec in specs:
            bounds = []
            if spec.operator in {">", ">=", "<", "<="}:
                bounds = [(spec.operator, spec.version)]
            elif spec.operator == "~=":
                release = Version(spec.version).release
                ceiling = (*release[:-2], release[-2] + 1)
                bounds = [(">=", spec.version), ("<", ".".join(map(str, ceiling)))]
            elif spec.operator == "==" and spec.version.endswith(".*"):
                release = Version(spec.version[:-2]).release
                bounds = [(">=", spec.version[:-2]), ("<", ".".join(map(str, (*release[:-1], release[-1] + 1))))]
            for operator, version in bounds:
                bound = (Version(version), operator in {">=", "<="})
                if operator.startswith(">"):
                    if lower is None or bound[0] > lower[0] or (bound[0] == lower[0] and not bound[1]):
                        lower = bound
                elif upper is None or bound[0] < upper[0] or (bound[0] == upper[0] and not bound[1]):
                    upper = bound
        conflict = bool(pins and not any(all(s.contains(pin, prereleases=True) for s in specs) for pin in pins))
        conflict |= bool(lower and upper and (lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1]))))
        if conflict:
            detail = "; ".join(f"{owner}: {requirement}" for owner, requirement in group)
            raise ValueError(f"Shared Python dependency conflict for {name}: {detail}")
    result = sorted({str(requirement) for group in groups.values() for _, requirement in group})
    if len(result) > 100:
        raise ValueError("Enabled Packs exceed the shared runtime limit of 100 Python requirements")
    return result
