import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from plugins.matcreator.oaw_matcreator.compatibility import adapt_package


def test_shipped_cli_instructions_have_maintained_notes_and_separate_provenance():
    root = Path(__file__).parents[1] / "oaw_matcreator" / "packages"
    package = json.loads((root / "research.json").read_text(encoding="utf-8"))
    skill = next(s for s in package["skills"] if s["id"] == "bohrium")
    assert "bohrium.com/download/bohr | sh" not in skill["instructions"]
    assert "bohr project list --json" not in skill["instructions"]
    assert "npm install -g @dptech-corp/bohr-cli" not in skill["instructions"]
    assert 'scripts/oaw_bohr.py' in skill['files']
    assert {'owner': 'matcreator', 'environment': 'bohr'} in skill['runtime_requirements']
    assert "bohr project list -o json" in skill["files"]["references/bohrium-cli-ref.md"]
    assert skill["defaults"]["upstream"]["revision"] == "a1a57688cdb7fc476498476cc388f932b6e83d6a"
    notes = skill["defaults"]["compatibility"]
    assert "unverified" in notes["verification_scope"]
    assert notes["instruction_sha256"] == hashlib.sha256(skill["instructions"].encode()).hexdigest()
    assert adapt_package(package) == package


@pytest.mark.parametrize('entry_mode', ['script', 'code'])
def test_bohr_wrapper_launches_only_private_package_entrypoint(tmp_path, entry_mode):
    if not shutil.which('node'):
        pytest.skip('Node is needed for this local CLI fixture')
    from plugins.matcreator.oaw_matcreator.compatibility import BOHR_SCRIPT
    modules = tmp_path / 'private-node-modules'
    package = modules / '@dptech-corp/bohr-cli'
    package.mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({'bin': {'bohr': 'entry.js'}}), encoding='utf-8')
    (package / 'entry.js').write_text('process.stdout.write(JSON.stringify(process.argv.slice(2)))', encoding='utf-8')
    script = tmp_path / 'oaw_bohr.py'
    script.write_text(BOHR_SCRIPT, encoding='utf-8')
    entry = [str(script)] if entry_mode == 'script' else ['-c', BOHR_SCRIPT]
    arguments = ['doctor', '--offline', 'a b', '& echo unintended', '$(literal)']
    result = subprocess.run([sys.executable, '-I', *entry, *arguments],
        env={**os.environ, 'NODE_PATH': str(modules)}, capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(result.stdout) == arguments
    missing = subprocess.run([sys.executable, '-I', *entry, *arguments],
        env={**os.environ, 'NODE_PATH': str(tmp_path / 'missing-package')},
        capture_output=True, text=True, timeout=15)
    assert missing.returncode != 0 and 'declared bohr environment' in missing.stderr


def test_scientific_preparation_does_not_require_cloud_cli():
    root = Path(__file__).parents[1] / 'oaw_matcreator/packages'
    for path in root.glob('*.json'):
        package = json.loads(path.read_text(encoding='utf-8'))
        assert adapt_package(package) == package
        for skill in package['skills']:
            if skill['id'] in {'local-structure', 'structure-conversion', 'lammps', 'deepmd', 'vasp-pymatgen'}:
                assert skill['runtime_requirements']
                assert not any(ref['environment'] == 'bohr' for ref in skill['runtime_requirements'])
