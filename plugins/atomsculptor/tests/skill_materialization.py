"""Exercise the shipped SkillPackage, not a host-import-dependent source tree."""
import base64
import tempfile
from functools import lru_cache
from pathlib import Path
from oaw_atomsculptor.skill_package import atomsculptor_skills

_ROOT = tempfile.TemporaryDirectory(prefix="atomsculptor-skills-")


@lru_cache
def skill_scripts(skill_id):
    skill = next(s for s in atomsculptor_skills().skills if s.id == skill_id)
    root = Path(_ROOT.name) / skill_id
    for name, asset in skill.files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(asset.encode() if isinstance(asset, str) else base64.b64decode(asset.data_base64))
    return root / "scripts"
