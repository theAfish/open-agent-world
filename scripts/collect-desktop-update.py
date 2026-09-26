"""Collect signed updater artifacts, then assemble the three-platform release manifest."""
import argparse
import json
from pathlib import Path
import shutil

TARGETS = {'windows-x64': 'windows-x86_64', 'macos-arm64': 'darwin-aarch64', 'macos-x86_64': 'darwin-x86_64'}


def collect(root: Path, platform: str):
    version = json.loads((root / 'desktop/src-tauri/tauri.conf.json').read_text())['version']
    suffix = '.exe' if platform == 'windows-x64' else '.app.tar.gz'
    artifacts = list((root / 'desktop/src-tauri/target/release/bundle').rglob('*' + suffix + '.sig'))
    if len(artifacts) != 1:
        raise ValueError(f'Expected one signed updater artifact for {platform}, found {len(artifacts)}')
    signature = artifacts[0].read_text().strip()
    if not signature:
        raise ValueError('Empty updater signature')
    artifact = artifacts[0].with_suffix('')
    output = root / 'release-assets'
    output.mkdir(exist_ok=True)
    name = f'Open-Agent-World-{version}-{platform}{suffix}'
    shutil.copy2(artifact, output / name)
    shutil.copy2(artifacts[0], output / (name + '.sig'))
    (output / f'updater-{platform}.json').write_text(json.dumps({'version': version, 'platforms': {
        TARGETS[platform]: {'signature': signature, 'url': f'https://github.com/theAfish/open-agent-world/releases/download/v{version}/{name}'},
    }}, indent=2) + '\n')


def manifest(output: Path):
    items = [json.loads((output / f'updater-{platform}.json').read_text()) for platform in TARGETS]
    if len({item['version'] for item in items}) != 1:
        raise ValueError('Updater artifacts must all have the same version')
    value = {'version': items[0]['version'], 'platforms': {key: entry for item in items for key, entry in item['platforms'].items()}}
    if set(value['platforms']) != set(TARGETS.values()):
        raise ValueError('Updater manifest is missing a platform')
    (output / 'latest.json').write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=TARGETS)
    parser.add_argument('--manifest', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.manifest:
        manifest(root / 'release-assets')
    else:
        collect(root, args.platform)
