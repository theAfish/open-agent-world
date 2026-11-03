"""Launch the existing reconciler from an independently reviewed, sealed installation.

The release transaction never upgrades this installation. Run with system Python
-I -S: site startup, editable installs and the application's venv are excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys

OPERATOR = Path('/opt/oaw-release-operator')
POLICY = OPERATOR / 'provisioner.json'
IMPORT_ROOT = Path('/opt/oaw-provisioner')
TARGET_KEYS = {'app', 'hosting', 'releases', 'service'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trusted(path, *, directory=False, private=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'Provisioner paths cannot traverse links')
    for entry in (path, *path.parents):
        info = entry.lstat()
        folder = entry != path or directory
        require(stat.S_ISDIR(info.st_mode) if folder else stat.S_ISREG(info.st_mode),
                'Provisioner paths must be real directories or regular files')
        require(info.st_uid == 0 and not info.st_mode & 0o022,
                'The complete provisioner import closure must be root-owned and not writable by other users')
        if not folder:
            require(info.st_nlink == 1, 'Provisioner files cannot be hard links')
            if private:
                require(not info.st_mode & 0o077, 'Provisioner registration must be private to root')
    return path


def inventory(root, expected):
    require(isinstance(expected, dict) and expected, 'Provisioner requires the complete reviewed file inventory')
    found = {}
    trusted(root, directory=True)
    for parent, folders, files in os.walk(root, followlinks=False):
        for name in folders:
            trusted(Path(parent) / name, directory=True)
        for name in files:
            path = trusted(Path(parent) / name)
            require(not name.endswith('.pth') and not name.startswith('__editable__'),
                    'Provisioner dependencies cannot use site startup or editable import hooks')
            found[path.relative_to(root).as_posix()] = path
    require(set(found) == set(expected), 'Provisioner complete import closure inventory changed')
    for name, path in found.items():
        require(isinstance(expected[name], str) and re.fullmatch(r'[a-f0-9]{64}', expected[name]),
                'Provisioner inventory requires exact file digests')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        require(actual == expected[name], 'Reviewed provisioner file changed: ' + name)


def configuration(expected_target=None, *, excluded=(), verify_user=None):
    trusted(POLICY, private=True)
    config = json.loads(POLICY.read_text(encoding='utf-8'))
    require(isinstance(config, dict) and set(config) ==
            {'version', 'target', 'source', 'dependencies', 'source_revision', 'files', 'verify_user'}
            and type(config.get('version')) is int and config['version'] == 1,
            'Invalid independent provisioner registration')
    target = config['target']
    require(isinstance(target, dict) and set(target) == TARGET_KEYS,
            'Invalid provisioner target')
    require(expected_target is None or target == expected_target, 'Provisioner belongs to another installation')
    require(isinstance(config['verify_user'], str) and re.fullmatch(r'oaw-[a-z0-9-]{1,27}', config['verify_user'])
            and (verify_user is None or config['verify_user'] == verify_user),
            'Provisioner sockets must use the independently registered nonroot verification group')
    require(isinstance(target['service'], str) and re.fullmatch(r'oaw-[a-z0-9-]{1,27}', target['service']),
            'Invalid provisioner service name')
    for key in ('app', 'hosting', 'releases'):
        require(isinstance(target[key], str) and re.fullmatch(r'/(?:opt|srv|var)/[A-Za-z0-9_./-]+', target[key])
                and '..' not in Path(target[key]).parts, 'Invalid provisioner target path')
    require(isinstance(config['source_revision'], str) and re.fullmatch(r'[a-f0-9]{40}', config['source_revision']),
            'Provisioner source must identify an independently reviewed revision')
    require(isinstance(config['files'], dict) and set(config['files']) == {'source', 'dependencies'},
            'Provisioner requires complete source and dependency inventories')
    roots = []
    for key in ('source', 'dependencies'):
        value = config[key]
        require(isinstance(value, str) and re.fullmatch(r'/opt/[A-Za-z0-9_./-]+', value)
                and '..' not in Path(value).parts, 'Provisioner imports must use independent /opt directories')
        require(Path(value).is_relative_to(IMPORT_ROOT) and Path(value) != IMPORT_ROOT,
                'Provisioner imports require the dedicated independent /opt/oaw-provisioner installation')
        roots.append(Path(value))
    forbidden = [OPERATOR, *[Path(target[key]) for key in ('app', 'hosting', 'releases')],
                 *[Path(path) for path in excluded]]
    require(all(not a.is_relative_to(b) and not b.is_relative_to(a)
                for a in roots for b in forbidden),
            'Provisioner imports must be independent of application, data, releases, operator and execution paths')
    require(not roots[0].is_relative_to(roots[1]) and not roots[1].is_relative_to(roots[0]),
            'Provisioner source and dependencies must be separate')
    for key, root in zip(('source', 'dependencies'), roots):
        inventory(root, config['files'][key])
    for package in ('backend', 'scripts', 'open_agent_world'):
        require((roots[0] / package / '__init__.py').is_file(), 'Provisioner source requires ordinary sealed packages')
    return config


def units(config):
    name = config['target']['service']
    return {
        name + '-provisioner.service': f'''[Unit]
Description=OAW queued workspace deployment (independent reviewed provisioner)
After=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/usr/bin:/bin HOME=/root
UMask=0077
TimeoutStartSec=300
ExecStart=/usr/bin/python3 -I -S {OPERATOR.as_posix()}/provisioner_guard.py tick
''',
        name + '-provisioner.timer': f'''[Unit]
Description=Check OAW workspace deployment queue

[Timer]
OnBootSec=10
OnUnitInactiveSec=5
Unit={name}-provisioner.service

[Install]
WantedBy=timers.target
''',
    }


def verification_sockets(rendered, config):
    """Adapt only the reviewed native socket policy; never alter worker services.

    The existing reconciler calls this renderer when adding a profile. Keep the
    application generator unchanged so release compatibility checks remain valid.
    """
    name = config['target']['service']
    gateway = 'oaw-' + hashlib.sha256((name + ':gateway').encode()).hexdigest()[:24]
    result = dict(rendered)
    for filename, content in rendered.items():
        if not filename.startswith(name + '-worker-') or not filename.endswith('.socket'):
            continue
        lines = content.splitlines()
        for key, expected in (('SocketUser', gateway), ('SocketGroup', gateway), ('SocketMode', '0600')):
            require([line for line in lines if line.startswith(key + '=')] == [key + '=' + expected],
                    'Reviewed native worker socket policy differs: ' + filename)
        result[filename] = content.replace('SocketGroup=' + gateway + '\n',
                                           'SocketGroup=' + config['verify_user'] + '\n').replace(
                                               'SocketMode=0600\n', 'SocketMode=0660\n')
    return result


def execute(config, command, *, username=None):
    require(command in {'tick', 'engineering', 'socket-units'}, 'Unsupported privileged provisioner operation')
    require(sys.flags.isolated and sys.flags.no_site and os.geteuid() == 0,
            'Use Linux root and independent system Python -I -S')
    trusted(Path(sys.executable).resolve())
    for entry in sys.path:
        require(entry and 'site-packages' not in Path(entry).parts,
                'Root provisioner cannot inherit application or site startup paths')
        if Path(entry).exists():
            trusted(Path(entry).resolve(), directory=Path(entry).is_dir())
    target = config['target']
    # No environment-driven service factory or Python/plugin path enters root.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin', HOME='/root', PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode = True
    sys.path.extend([config['source'], config['dependencies']])
    provisioner = importlib.import_module('backend.hosting.provisioner')
    renderer = provisioner.render_units
    # The frozen reconciler's existing install_profile uses this module global.
    # Scope the policy adapter to this short-lived, independently reviewed process.
    provisioner.render_units = lambda *args, **kwargs: verification_sockets(renderer(*args, **kwargs), config)
    if command == 'socket-units':
        settings = provisioner.read_gateway_settings(Path(target['hosting']) / 'control/hosting.json')
        rendered = provisioner.render_units(settings, directory=target['hosting'], application=target['app'],
                                            name=target['service'])
        return {name: text for name, text in rendered.items()
                if name.startswith(target['service'] + '-worker-') and name.endswith('.socket')}
    hook = Path(target['hosting']) / 'resource-provisioner.json'
    if hook.exists():
        trusted(hook, private=True)
        value = json.loads(provisioner.read_regular(hook, 1024 * 1024))
        require(value.get('handler') == 'backend.cloud.aliyun.provisioning:prepare_profile',
                'Independent Alibaba provisioner requires the reviewed fixed resource handler')
    arguments = [Path(target['hosting']), Path(target['app']), target['service']]
    if command == 'engineering':
        require(isinstance(username, str) and username, 'Engineering initialization requires an explicit administrator')
        return provisioner.engineering(*arguments, username)
    return provisioner.tick(*arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'units', 'socket-units', 'tick', 'engineering'))
    parser.add_argument('--user')
    args = parser.parse_args()
    require(sys.flags.isolated and sys.flags.no_site and os.geteuid() == 0,
            'Use Linux root and independent system Python -I -S')
    require(Path(__file__).absolute() == OPERATOR / 'provisioner_guard.py',
            'Use the independently installed provisioner guard')
    trusted(Path(__file__).absolute())
    config = configuration()
    if args.command == 'check':
        print(json.dumps({'verified': True, 'source_revision': config['source_revision'], 'target': config['target']}))
    elif args.command == 'units':
        print(json.dumps(units(config)))
    else:
        result = execute(config, args.command, username=args.user)
        if result is not None:
            print(json.dumps(result))


if __name__ == '__main__':
    main()
