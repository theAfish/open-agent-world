"""Read AtomSculptor's bundled skills as an OAW SkillPackage.

The package is assembled only from files shipped with this plugin.  It never
reads the legacy AtomSculptor checkout at OAW startup and it never installs a
runtime dependency; OAW's separately authorized Skill/Sandbox path owns that.
"""

from __future__ import annotations

import base64
import re
from importlib.resources import files
from typing import Iterable

from open_agent_world.skill_packages import Skill, SkillAsset, SkillPackage


def _metadata(instructions: str, fallback: str) -> tuple[str, str]:
    """Extract the small useful subset of existing SKILL.md frontmatter."""

    header = instructions.split("---", 2)[1] if instructions.startswith("---") and instructions.count("---") >= 2 else ""
    name = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", header, re.MULTILINE)
    description = re.search(r"^description:\s*['\"]?(.+?)['\"]?\s*$", header, re.MULTILINE)
    return (
        name.group(1) if name else fallback.replace("-", " ").title(),
        description.group(1) if description else "Atomistic modelling skill.",
    )


def _files(root, current=None) -> Iterable[tuple[str, object]]:
    current = root if current is None else current
    for entry in current.iterdir():
        relative = str(entry.relative_to(root)).replace("\\", "/")
        if entry.is_dir():
            yield from _files(root, entry)
        elif relative != "SKILL.md":
            yield relative, entry


def _skill(folder) -> Skill:
    instructions = folder.joinpath("SKILL.md").read_text(encoding="utf-8")
    name, description = _metadata(instructions, folder.name)
    contents: dict[str, str | SkillAsset] = {}
    for relative, entry in _files(folder):
        raw = entry.read_bytes()
        try:
            contents[relative] = raw.decode("utf-8")
        except UnicodeDecodeError:
            contents[relative] = SkillAsset(data_base64=base64.b64encode(raw).decode("ascii"))
    return Skill(
        id=folder.name,
        name=name,
        description=description,
        instructions=instructions,
        files=contents,
    )


def atomsculptor_skills() -> SkillPackage:
    root = files(__package__).joinpath("skills")
    return SkillPackage(
        package_id="atomsculptor.skills",
        version="0.1.0",
        name="AtomSculptor Skills",
        description="Portable atomistic modelling, inspection, conversion, and Materials Project skills.",
        author="AtomSculptor",
        instructions=(
            "Use the connected Sandbox explicitly for every runnable skill. "
            "Provision scientific libraries in the selected OAW environment; "
            "this package never installs dependencies by itself."
        ),
        skills=[_skill(folder) for folder in sorted(root.iterdir(), key=lambda item: item.name) if folder.is_dir()],
    )
