"""Build a declarative Pack outside OAW using the distributed frontend SDK."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--offline', action='store_true', help='Use an already populated npm cache')
args = parser.parse_args()
external = Path(tempfile.mkdtemp(prefix='oaw-external-notes-'))
shutil.copytree(root / 'examples/packs/notes', external, dirs_exist_ok=True)
output = root / '.outputs/declarative-acceptance'
output.mkdir(parents=True, exist_ok=True)
sdk = root / '.outputs/oaw-plugin-api-1.0.0.tgz'
if not sdk.is_file():
    raise SystemExit('Build and npm pack frontend/pack-sdk into .outputs first')
npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
subprocess.run([npm, 'install', str(sdk), '--cache', str(root / '.tmp/npm-cache'),
                '--no-audit', '--no-fund', *(['--offline'] if args.offline else [])], cwd=external, check=True)
subprocess.run([npm, 'run', 'build'], cwd=external, check=True)
artifact = output / (external.name + '.oawpack')
cli = [sys.executable, str(root / 'scripts/oaw-pack.py')]
subprocess.run([*cli, 'doctor', '.'], cwd=external, check=True, stdout=subprocess.DEVNULL)
subprocess.run([*cli, 'build-project', '.', str(artifact), '--frontend-dir', 'dist/frontend'], cwd=external, check=True)
subprocess.run([*cli, 'inspect', str(artifact)], cwd=external, check=True, stdout=subprocess.DEVNULL)
result = {'external_repository': str(external), 'artifact': str(artifact), 'sdk': str(sdk), 'host_only': True}
(output / 'build.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
