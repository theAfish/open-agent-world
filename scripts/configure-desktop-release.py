"""Apply release-only signing configuration; never persist private keys in config."""
import json
import os
from pathlib import Path


def configure(root: Path, env=os.environ):
    strict = env.get('OAW_SIGNED_RELEASE') == 'true'
    public = env.get('OAW_UPDATER_PUBLIC_KEY', '').strip()
    private = env.get('TAURI_SIGNING_PRIVATE_KEY', '').strip()
    if bool(public) != bool(private) or (strict and not public):
        raise SystemExit('Configure both updater public and private keys before enabling signed releases.')
    config_path = root / 'desktop/src-tauri/tauri.conf.json'
    config = json.loads(config_path.read_text())
    if public:
        config['bundle']['createUpdaterArtifacts'] = True
        config.setdefault('plugins', {})['updater'] = {
            'pubkey': public,
            'endpoints': ['https://github.com/theAfish/open-agent-world/releases/latest/download/latest.json'],
            'windows': {'installMode': 'passive'},
        }
    if env.get('RUNNER_OS') == 'Windows':
        thumbprint = env.get('OAW_WINDOWS_CERTIFICATE_THUMBPRINT')
        if strict and not thumbprint:
            raise SystemExit('Signed releases require a Windows code-signing certificate.')
        if thumbprint:
            config['bundle']['windows'].update(certificateThumbprint=thumbprint, digestAlgorithm='sha256',
                timestampUrl=env.get('OAW_WINDOWS_TIMESTAMP_URL') or 'http://timestamp.digicert.com')
    if env.get('RUNNER_OS') == 'macOS':
        identity = env.get('APPLE_SIGNING_IDENTITY')
        if strict and not all(env.get(key) for key in ['APPLE_CERTIFICATE', 'APPLE_SIGNING_IDENTITY', 'APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID']):
            raise SystemExit('Signed releases require Developer ID signing and Apple notarization credentials.')
        if identity:
            mac_path = root / 'desktop/src-tauri/tauri.macos.conf.json'
            mac = json.loads(mac_path.read_text())
            mac['bundle']['macOS']['signingIdentity'] = identity
            mac_path.write_text(json.dumps(mac, indent=2) + '\n')
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    if env.get('GITHUB_ENV'):
        with open(env['GITHUB_ENV'], 'a') as stream:
            stream.write(f'OAW_UPDATER_ENABLED={"1" if public else "0"}\n')


if __name__ == '__main__':
    configure(Path(__file__).resolve().parents[1])
