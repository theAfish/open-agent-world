"""Apply release-only signing configuration; never persist private keys in config."""
import json
import os
import argparse
from pathlib import Path


def preflight(env=os.environ):
    """Validate inputs before certificate import or expensive build setup."""
    public = env.get('OAW_UPDATER_PUBLIC_KEY', '').strip()
    private = env.get('TAURI_SIGNING_PRIVATE_KEY', '').strip()
    if bool(public) != bool(private):
        raise SystemExit('Updater public/private key mismatch: configure both updater keys or neither.')
    if env.get('OAW_SIGNED_RELEASE') != 'true':
        return
    if env.get('RUNNER_OS') == 'Windows' and not env.get('WINDOWS_CERTIFICATE'):
        raise SystemExit('Signed release requested but Windows certificate missing (WINDOWS_CERTIFICATE).')
    if env.get('RUNNER_OS') == 'macOS':
        if not all(env.get(key) for key in ('APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID')):
            raise SystemExit('Signed release requested but Apple notarization credentials missing (APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID).')
        if not env.get('APPLE_CERTIFICATE') or env.get('APPLE_SIGNING_IDENTITY', '').strip() in ('', '-'):
            raise SystemExit('Signed release requested but Apple Developer ID certificate or signing identity missing.')


def configure(root: Path, env=os.environ):
    strict = env.get('OAW_SIGNED_RELEASE') == 'true'
    public = env.get('OAW_UPDATER_PUBLIC_KEY', '').strip()
    private = env.get('TAURI_SIGNING_PRIVATE_KEY', '').strip()
    if bool(public) != bool(private):
        raise SystemExit('Updater public/private key mismatch: configure both updater keys or neither.')
    config_path = root / 'desktop/src-tauri/tauri.conf.json'
    config = json.loads(config_path.read_text())
    if public:
        config['bundle']['createUpdaterArtifacts'] = True
        config.setdefault('plugins', {})['updater'] = {
            'pubkey': public,
            'endpoints': ['https://github.com/theAfish/open-agent-world/releases/latest/download/latest.json'],
            'windows': {'installMode': 'passive'},
        }
    else:
        # Reusing a configured checkout must not produce preview artifacts with
        # a stale update channel or require an old private key.
        config['bundle'].pop('createUpdaterArtifacts', None)
        config.get('plugins', {}).pop('updater', None)
    if env.get('RUNNER_OS') == 'Windows':
        thumbprint = env.get('OAW_WINDOWS_CERTIFICATE_THUMBPRINT')
        if strict and not thumbprint:
            raise SystemExit('Signed releases require a Windows code-signing certificate.')
        if thumbprint:
            config['bundle']['windows'].update(certificateThumbprint=thumbprint, digestAlgorithm='sha256',
                timestampUrl=env.get('OAW_WINDOWS_TIMESTAMP_URL') or 'http://timestamp.digicert.com')
        else:
            for key in ('certificateThumbprint', 'digestAlgorithm', 'timestampUrl'):
                config['bundle']['windows'].pop(key, None)
    if env.get('RUNNER_OS') == 'macOS':
        identity = env.get('APPLE_SIGNING_IDENTITY')
        if strict and (identity == '-' or not all(env.get(key) for key in ['APPLE_CERTIFICATE', 'APPLE_SIGNING_IDENTITY', 'APPLE_ID', 'APPLE_PASSWORD', 'APPLE_TEAM_ID'])):
            raise SystemExit('Signed releases require Developer ID signing and Apple notarization credentials.')
        mac_path = root / 'desktop/src-tauri/tauri.macos.conf.json'
        mac = json.loads(mac_path.read_text())
        mac['bundle']['macOS']['signingIdentity'] = identity or '-'
        mac_path.write_text(json.dumps(mac, indent=2) + '\n')
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    if env.get('GITHUB_ENV'):
        with open(env['GITHUB_ENV'], 'a') as stream:
            stream.write(f'OAW_UPDATER_ENABLED={"1" if public else "0"}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='Validate credentials without changing configuration')
    if parser.parse_args().preflight:
        preflight()
    else:
        configure(Path(__file__).resolve().parents[1])
