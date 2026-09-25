"""Build the repository fixture from a real directory outside the host checkout."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
external = Path(tempfile.mkdtemp(prefix='oaw-external-greeter-'))
shutil.copytree(root / 'examples/packs/greeter', external, dirs_exist_ok=True)
artifact_directory = root / '.outputs/pack-acceptance' / external.name
artifact_directory.mkdir(parents=True)
sdk = root / '.outputs/oaw-plugin-api-1.0.0.tgz'
if not sdk.is_file():
    raise SystemExit('Build and npm pack frontend/pack-sdk first (see docs/pack-distribution.md)')


def run(args):
    subprocess.run(list(map(str, args)), cwd=external, check=True,
                   **({'creationflags': 0x08000000} if os.name == 'nt' else {}))


run([shutil.which('npm.cmd' if os.name == 'nt' else 'npm'), 'install', sdk, '--no-audit', '--no-fund', '--fetch-retries=0', '--fetch-timeout=15000', '--cache', root / '.tmp/npm-cache'])
run([shutil.which('npm.cmd' if os.name == 'nt' else 'npm'), 'run', 'build'])
run([shutil.which('uv'), '--cache-dir', root / '.tmp/uv-cache', 'build', '--wheel', '--out-dir', 'dist/backend'])
from open_agent_world.pack import build
artifact = build(external / 'dist', artifact_directory / 'greeter-0.1.0.oawpack')
result = {'external_repository': str(external), 'artifact': str(artifact), 'sdk': str(sdk)}
(root / '.outputs/pack-acceptance/build.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
