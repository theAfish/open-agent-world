"""Explicit maintainer import; never runs on plugin load or assimilation."""
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
import subprocess
import yaml

REVISION = "a1a57688cdb7fc476498476cc388f932b6e83d6a"
GROUPS = {
    "core": ("Materials Core", {"atomic-structure", "structure-conversion", "plot", "matcraft-kit", "concepts/structure-generation", "concepts/utility", "guides/materials-design", "guides/skill-creation"}),
    "simulation": ("Atomistic Simulation", {"ase", "abacus", "vasp-pymatgen", "lammps", "eos", "phonon", "concepts/dft-calculation", "concepts/molecular-dynamics"}),
    "ai": ("Materials AI", {"mattergen", "mattersim", "deepmd", "cgcnn-predictor", "quests", "concepts/machine-learning-force-field", "guides/guide-for-model-training", "guides/pfd-distillation", "guides/pfd-finetuning"}),
    "research": ("Research and Remote Compute", {"bohrium", "remote-job", "paramiko", "materials-project", "database", "tavily"}),
}
GUIDANCE = """OAW execution adaptation: list this Toolset, then selectively read a skill or resource.
Use OAW run_skill_script/copy_skill_resource with the Skill resource ID and a relative
scripts/... path. Legacy MatCreator tool names and workspace paths are reference
examples, not callable OAW tools. Choose an independently authorized Sandbox and,
when needed, an Environment or Compute Target. Importing knowledge does not install
packages or grant network, filesystem or credential access. Report missing runtime
requirements. Remote provider examples are optional; do not enforce MatCreator's
automatic cloud routing. Pure ASE structure manipulation needs no calculator or GPU.
For the first demo use the local-structure skill and structure-conversion.
"""

def build(root):
    actual = subprocess.check_output(['git', '-c', 'safe.directory=' + root.resolve().as_posix(), '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != REVISION:
        raise ValueError('Check out the pinned MatCreator revision before importing')
    output = Path(__file__).parent / "oaw_matcreator" / "packages"
    output.mkdir(parents=True, exist_ok=True)
    skills_root = root / "src/matcreator/skills"
    covered = set()
    for group, (name, families) in GROUPS.items():
        skills = []
        for folder in sorted(families):
            base = skills_root / folder
            for md in sorted(base.rglob("SKILL.md")):
                relative = md.parent.relative_to(skills_root).as_posix()
                if relative in covered:
                    continue
                covered.add(relative)
                raw = md.read_text(encoding="utf-8")
                front = yaml.safe_load(raw.split("---", 2)[1]) if raw.startswith("---") else {}
                files = {}
                hashes = {}
                for file in sorted(md.parent.rglob("*")):
                    if not file.is_file() or file == md:
                        continue
                    path = file.relative_to(md.parent).as_posix()
                    data = file.read_bytes()
                    hashes[path] = hashlib.sha256(data).hexdigest()
                    try:
                        files[path] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        files[path] = {"data_base64": base64.b64encode(data).decode(), "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream"}
                metadata = front.get("metadata") or {}
                if relative == "bohrium":
                    archive = (skills_root / "bohrium-jobs.zip").read_bytes()
                    files["assets/bohrium-jobs.zip"] = {"data_base64": base64.b64encode(archive).decode(), "media_type": "application/zip"}
                    hashes["assets/bohrium-jobs.zip"] = hashlib.sha256(archive).hexdigest()
                skills.append({"id": relative.replace("/", "--"), "name": str(front.get("name", md.parent.name)),
                    "description": str(front.get("description", ""))[:1000], "instructions": GUIDANCE + "\n" + raw,
                    "files": files, "defaults": {"upstream": {"repository": "theAfish/MatCreator", "revision": REVISION,
                    "path": relative, "instruction_sha256": hashlib.sha256(md.read_bytes()).hexdigest(), "files_sha256": hashes},
                    "metadata": metadata, "runtime": "See SKILL.md requirements; provision explicitly."}})
        package = {"package_id": "matcreator." + group, "version": "0.1.0", "name": name,
            "description": "Portable MatCreator scientific knowledge and resources, adapted for OAW.",
            "author": "MatCreator contributors; OAW adaptation", "instructions": GUIDANCE, "skills": skills}
        if group == "core":
            package["skills"].insert(0, {"id": "local-structure", "name": "Local structure demo", "description": "Build and verify copper supercells in CIF and extxyz with ASE, without GPU or remote services.",
                "instructions": GUIDANCE + "\nRun scripts/build_structure.py with interpreter python and argv [--repeat, 2, --output, copper]. Requires ASE installed in the selected Sandbox runtime. Check exit code and report, then publish copper.cif, copper.extxyz and copper.json through OAW artifact publishing. Related task: repeat 3 and compare atom count (108 instead of 32).",
                "files": {"scripts/build_structure.py": Path(__file__).with_name("demo_structure.py").read_text(encoding="utf-8")},
                "defaults": {"runtime": {"python": ">=3.11", "dependencies": ["ase>=3.23,<4"]}, "metadata": {"tags": ["ASE", "structure", "copper", "supercell"]}, "upstream": {"repository": "OAW adaptation", "path": "local-structure"}}})
        (output / (group + ".json")).write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    discovered = {p.parent.relative_to(skills_root).as_posix() for p in skills_root.rglob("SKILL.md")}
    assert covered == discovered, discovered - covered

if __name__ == "__main__":
    build(Path(sys.argv[1]))
