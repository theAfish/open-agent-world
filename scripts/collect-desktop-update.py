"""Collect signed updater artifacts, then assemble the three-platform release manifest."""
import argparse
import json
from pathlib import Path
import shutil

TARGETS = {'windows-x64': 'windows-x86_64', 'macos-arm64': 'darwin-aarch64', 'macos-x86_64': 'darwin-x86_64'}


def artifact_name(version: str, platform: str) -> str:
    suffix = '.exe' if platform == 'windows-x64' else '.app.tar.gz'
    return f'Open-Agent-World-{version}-{platform}{suffix}'


def artifact_url(version: str, platform: str) -> str:
    return f'https://github.com/theAfish/open-agent-world/releases/download/v{version}/{artifact_name(version, platform)}'


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
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise ValueError('Signed updater payload is missing or empty')
    output = root / 'release-assets'
    output.mkdir(exist_ok=True)
    name = artifact_name(version, platform)
    shutil.copy2(artifact, output / name)
    shutil.copy2(artifacts[0], output / (name + '.sig'))
    (output / f'updater-{platform}.json').write_text(json.dumps({'version': version, 'platforms': {
        TARGETS[platform]: {'signature': signature, 'url': artifact_url(version, platform)},
    }}, indent=2) + '\n')


def manifest(output: Path):
    items = [json.loads((output / f'updater-{platform}.json').read_text()) for platform in TARGETS]
    if len({item['version'] for item in items}) != 1:
        raise ValueError('Updater artifacts must all have the same version')
    version = items[0]['version']
    platforms = {}
    for (platform, target), item in zip(TARGETS.items(), items):
        if set(item['platforms']) != {target}:
            raise ValueError(f'Updater metadata must contain only {target}')
        entry = item['platforms'][target]
        artifact = output / artifact_name(version, platform)
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f'Updater payload is missing or empty for {platform}')
        signature = artifact.with_name(artifact.name + '.sig').read_text().strip()
        if not signature or entry.get('signature') != signature:
            raise ValueError(f'Updater signature does not match the asset for {platform}')
        if entry.get('url') != artifact_url(version, platform):
            raise ValueError(f'Updater URL does not match the release asset for {platform}')
        platforms[target] = entry
    value = {'version': version, 'platforms': platforms}
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
