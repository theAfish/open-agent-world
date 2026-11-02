"""Plan or explicitly install an independently reviewed ECS deployment operator.

Run a reviewed, root-owned copy with the system Python using -I -S. This
module uses only the standard library and never imports application code. A plan
has no side effects; --apply is a separate, offline administrator action.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from uuid import UUID


TARGET_KEYS = ('app', 'hosting', 'releases', 'service')
OPERATOR_ROOTS = {'production': '/opt/oaw-release-operator', 'preview': '/opt/oaw-release-operator-preview'}
CORE_FILES = {'bootstrap.py', 'release_remote.py', 'release_worker.py', 'release_verify.py'}
UNITS = Path('/etc/systemd/system')
SYSTEMCTL = '/usr/bin/systemctl'
SYSUSERS = '/usr/bin/systemd-sysusers'
ANALYZE = '/usr/bin/systemd-analyze'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def linux_path(value):
    require(isinstance(value, str), 'Expected a Linux installation path')
    path = PurePosixPath(value)
    require(re.fullmatch(r'/(?:opt|srv|var)/[A-Za-z0-9_./-]+', value)
            and str(path) == value and '..' not in path.parts,
            'Use separate canonical Linux directories under /opt, /srv or /var')
    return path


def service(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,23}', value), 'Invalid service name')
    return value


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r'oaw-[a-z0-9-]{1,27}', value),
            'Use a dedicated oaw- nonroot system identity')
    return value


def service_user(name, profile='gateway'):
    return 'oaw-' + hashlib.sha256(f'{service(name)}:{profile}'.encode()).hexdigest()[:24]


def target(value):
    require(isinstance(value, dict) and set(value) == set(TARGET_KEYS), 'Invalid registered target')
    return {**{key: str(linux_path(value[key])) for key in TARGET_KEYS[:-1]}, 'service': service(value['service'])}


def https_origin(value):
    from urllib.parse import urlsplit
    require(isinstance(value, str), 'Use an exact approved HTTPS origin')
    origin = urlsplit(value)
    require(origin.scheme == 'https' and origin.netloc and origin.hostname
            and not any((origin.path, origin.query, origin.fragment, origin.username, origin.password)),
            'Use an exact approved HTTPS origin')
    return origin


def separate(paths):
    roots = [linux_path(value) for value in paths]
    require(all(not a.is_relative_to(b) for index, a in enumerate(roots)
                for other, b in enumerate(roots) if index != other), 'Installation directories must not overlap')


def inventory(source, files, expected):
    require(isinstance(files, dict) and set(files) == set(expected), 'Incomplete or unexpected reviewed file inventory')
    for name, expected_digest in files.items():
        require(isinstance(expected_digest, str) and re.fullmatch(r'[a-f0-9]{64}', expected_digest),
                'Every reviewed file requires its exact SHA-256 digest')
        path = source / name
        metadata = path.lstat()
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                'Reviewed files must be regular files without links')
        require(digest(path) == expected_digest, f'Reviewed file changed: {name}')


def validate(config):
    require(isinstance(config, dict) and type(config.get('version')) is int and config['version'] == 1, 'Unsupported bootstrap configuration')
    execution = config.get('execution')
    require(isinstance(execution, dict) and type(execution.get('version')) is int and execution['version'] == 1
            and execution.get('environment') in OPERATOR_ROOTS, 'Invalid execution policy')
    require(set(execution) == {'version', 'environment', 'target', 'build_user', 'verify_user', 'python', 'sandbox_root'},
            'Unexpected execution policy fields')
    environment = execution['environment']
    registered = target(execution['target'])
    operator = OPERATOR_ROOTS[environment]
    require(config.get('operator') == operator, 'Operator directory does not match the registered environment')
    build, verify = (identity(execution[key]) for key in ('build_user', 'verify_user'))
    require(build != verify and service_user(registered['service']) not in {build, verify},
            'Build, verification and gateway identities must be independent')
    sandbox = str(linux_path(execution['sandbox_root']))
    require(PurePosixPath(sandbox).parts[1] in {'opt', 'srv'}, 'The operator sandbox must be under /opt or /srv')
    python = execution['python']
    require(isinstance(python, str) and re.fullmatch(r'/(?:usr/)?bin/python3(?:\.\d+)?', python),
            'Use the reviewed independent Python inside sandbox_root')
    production = target(config.get('production_target'))
    production_origin = https_origin(config.get('production_origin'))
    if environment == 'production':
        require(registered == production, 'Production registration must match the protected production target')
        roots = [registered[key] for key in TARGET_KEYS[:-1]]
    else:
        require(registered['service'] != production['service'], 'Preview requires a separate service namespace')
        roots = [value[key] for value in (registered, production) for key in TARGET_KEYS[:-1]]
    separate([*roots, operator, sandbox])
    source = Path(config['source_directory']).absolute()
    # Source staging is outside every mutable installation and the installed operator.
    if os.name != 'nt':
        source_root = PurePosixPath(str(source))
        require(all(not source_root.is_relative_to(PurePosixPath(root))
                    and not PurePosixPath(root).is_relative_to(source_root) for root in [*roots, operator, sandbox]),
                'Reviewed source staging must be outside every installation directory')
    inventory(source, config.get('operator_files'), CORE_FILES)
    approved = config.get('approved_artifacts', [])
    require(isinstance(approved, list) and all(isinstance(item, dict)
            and set(item) == {'archive_sha256', 'source_commit'}
            and isinstance(item['archive_sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', item['archive_sha256'])
            and isinstance(item['source_commit'], str) and re.fullmatch(r'[a-f0-9]{40}', item['source_commit'])
            for item in approved), 'Invalid explicit artifact approvals')
    preview = config.get('preview')
    if environment == 'preview':
        require(isinstance(preview, dict), 'Preview requires its independent hosting setup')
        origin = https_origin(preview.get('public_origin'))
        try:
            ipaddress.ip_address(origin.hostname)
        except ValueError:
            pass
        else:
            raise ValueError('Preview requires a separate HTTPS hostname, not another port on the production IP')
        require(origin.hostname.lower() != production_origin.hostname.lower(),
                'Preview requires a distinct hostname so production cookies cannot enter Preview')
        require('port' not in preview, 'Preview ingress uses its private Unix socket, without a TCP port')
        setup = identity(preview.get('setup_user'))
        require(setup not in {build, verify, service_user(registered['service'])},
                'Preview setup requires its own nonroot identity')
    else:
        require(preview is None, 'Production bootstrap cannot contain Preview setup')
    return source, operator, execution, registered, production, preview


def protected_paths(production):
    return [production[key] for key in TARGET_KEYS[:-1]] + [OPERATOR_ROOTS['production']]


def plan(config, *, step='operator', upgrade_from=None):
    source, operator, execution, registered, production, preview = validate(config)
    result = {'version': 1, 'step': step, 'apply': False, 'operator': operator, 'execution': execution,
              'operator_files': config['operator_files'], 'upgrade_from': upgrade_from,
              'root_application_imports': False, 'enable_services': False, 'start_services': False,
              'routing_changes': False, 'production_mutations': False}
    if step == 'preview-units':
        require(preview is not None, 'Preview units are only supported for the Preview environment')
        reviewed_units(config)
        result['unit_files'] = preview['unit_files']
    if preview:
        app, hosting, name = (registered[key] for key in ('app', 'hosting', 'service'))
        restrictions = ['--property=RootDirectory=' + execution['sandbox_root'],
                        '--property=MountAPIVFS=yes', '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
                        '--property=NoNewPrivileges=yes', '--property=PrivateNetwork=yes',
                        '--property=RestrictAddressFamilies=AF_UNIX',
                        '--property=PrivateTmp=yes', '--property=PrivateDevices=yes',
                        '--property=PrivateIPC=yes', '--property=ProtectProc=invisible', '--property=ProcSubset=pid',
                        '--property=ProtectKernelTunables=yes', '--property=ProtectKernelModules=yes',
                        '--property=ProtectControlGroups=yes', '--property=RestrictSUIDSGID=yes',
                        '--property=MemoryMax=768M', '--property=TasksMax=128', '--property=KillMode=control-group',
                        '--property=RuntimeMaxSec=1200',
                        '--property=CapabilityBoundingSet=', '--property=IPAddressDeny=any',
                        '--property=BindReadOnlyPaths=' + app + ':' + app,
                        '--property=BindReadOnlyPaths=' + hosting + '.passwords.json:' + hosting + '.passwords.json',
                        '--property=BindPaths=' + hosting + ':' + hosting,
                        '--property=ReadWritePaths=' + hosting]
        runner = ['/usr/bin/systemd-run', '--wait', '--pipe', '--collect',
                  '--property=User=' + preview['setup_user'], '--property=Group=' + preview['setup_user'],
                  '--property=WorkingDirectory=' + app, *restrictions,
                  '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'HOME=/tmp',
                  app + '/backend/.venv/bin/python', '-m', 'backend.hosting.preview']
        result['preview_preparation'] = {
            'prerequisites': ['A separately reviewed, root-owned application and runtime bundle',
                              'A new empty hosting directory owned by setup_user, mode 0700',
                              'An independent setup-user-owned 0600 JSON file at hosting + .passwords.json (outside the empty hosting directory)',
                              'Empty canonical app/hosting/password bind placeholders in the independently prepared sandbox root',
                              'The approved HTTPS route must be reviewed separately; no DNS or nginx changes are applied'],
            'initialize': [*runner, 'initialize', '--directory', hosting, '--origin', preview['public_origin'],
                           '--service', name, '--password-file', hosting + '.passwords.json'],
            'generate_units': [*runner, 'units', '--directory', hosting, '--application-directory', app,
                               '--service', name, '--verify-user', execution['verify_user'],
                               '--execution-root', execution['sandbox_root'],
                               *[part for root in protected_paths(production) for part in ('--production-root', root)]],
            'review': 'Review every generated unit and sysusers.conf, stage root-owned copies outside the app/hosting/releases, '
                      'record unit_files digests, then plan/apply --step preview-units. Keep services stopped and disabled.',
        }
    return result


def trusted(path, *, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'Trusted paths cannot use symlinks')
    for item in (path, *path.parents):
        metadata = item.lstat()
        require(metadata.st_uid == 0 and not metadata.st_mode & 0o022,
                'Reviewed control files and every ancestor must be root-owned and not writable by other users')
        require(stat.S_ISDIR(metadata.st_mode) if item != path or directory else stat.S_ISREG(metadata.st_mode),
                'Reviewed control files must be real directories or regular files')
        if item == path and not directory:
            require(metadata.st_nlink == 1, 'Reviewed control files cannot be hard links')


def plain_tree(root):
    root = Path(root)
    require(root.absolute() == root.resolve(strict=True), 'Preview storage cannot use symlink ancestors')
    paths = [root]
    for current, dirs, files in os.walk(root, followlinks=False):
        paths.extend(Path(current) / name for name in dirs + files)
    for path in paths:
        metadata = path.lstat()
        require(stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                'Preview storage cannot contain links or special files')
    return paths


def trusted_runtime(root, python, *, preview_target=None):
    """Check an independently prepared chroot; never run its interpreter as root."""
    root = Path(root)
    trusted(root, directory=True)
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            item = Path(current) / name
            metadata = item.lstat()
            require(metadata.st_uid == 0 and not metadata.st_mode & 0o022,
                    'The complete sandbox runtime must be root-owned and not writable by other users')
            require(stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                    'Use a self-contained runtime with real files and directories, without links or special files')
    interpreter = root / python.lstrip('/')
    require(interpreter.is_file() and interpreter.stat().st_mode & 0o111,
            'The independently prepared sandbox must contain its executable Python and reviewed dependencies')
    for placeholder in ('source', 'previous', 'scratch', 'sockets'):
        path = root / 'workspace' / placeholder
        require(path.is_dir() and not any(path.iterdir()), 'Sandbox bind placeholders must be empty real directories')
    for placeholder in ('release_worker.py', 'release_verify.py', 'verification.json'):
        helper = root / 'workspace' / placeholder
        require(helper.is_file() and helper.stat().st_size == 0, 'Sandbox requires empty helper and verification bind placeholders')
    if preview_target:
        for key in ('app', 'hosting'):
            path = root / preview_target[key].lstrip('/')
            require(path.is_dir() and not any(path.iterdir()), 'Preview requires empty canonical app and hosting sandbox bind placeholders')
        password = root / (preview_target['hosting'].lstrip('/') + '.passwords.json')
        require(password.is_file() and password.stat().st_size == 0, 'Preview requires an empty password-file sandbox bind placeholder')
        for location in ('/var/lib/' + preview_target['service'] + '-control',
                         '/var/lib/' + preview_target['service'] + '-profile', '/run/' + preview_target['service']):
            path = root / location.lstrip('/')
            require(path.is_dir() and not any(path.iterdir()), 'Preview requires empty control/profile/socket sandbox bind placeholders')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def identity_record(name, marker):
    import grp
    import pwd
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        return None
    require(account.pw_uid > 0 and account.pw_gid > 0 and account.pw_shell == '/usr/sbin/nologin'
            and account.pw_gecos == marker and grp.getgrnam(name).gr_gid == account.pw_gid,
            f'Refusing to reuse an unrelated system identity: {name}')
    return account


def require_idle(account):
    """A stopped, dedicated setup identity cannot race the ownership transition."""
    require(account is not None, 'Install the registered operator identities before Preview units')
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            try:
                require(path.stat().st_uid != account.pw_uid, 'Stop every Preview setup process before installing units')
            except FileNotFoundError:
                pass


def check_admin(config):
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.no_site,
            'Apply requires Linux root and an independently reviewed copy invoked with system Python -I -S')
    trusted(Path(__file__).absolute())
    trusted(Path(sys.executable).resolve())
    for entry in sys.path:
        require(entry and not any(Path(entry).is_relative_to(Path(config['execution']['target'][key]))
                                for key in TARGET_KEYS[:-1]), 'Root Python cannot import from application/data/release paths')
        if Path(entry).exists():
            trusted(Path(entry).resolve(), directory=Path(entry).is_dir())
    source = Path(config['source_directory']).absolute()
    trusted(source, directory=True)
    for name in config['operator_files']:
        trusted(source / name)
    trusted_runtime(config['execution']['sandbox_root'], config['execution']['python'],
                    preview_target=config['execution']['target'] if config['execution']['environment'] == 'preview' else None)


def write_json(path, value, mode=0o600):
    Path(path).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    Path(path).chmod(mode)


def install_operator(config, *, upgrade_from=None):
    plan(config, upgrade_from=upgrade_from)
    check_admin(config)
    source, destination, execution, registered, production, preview = validate(config)
    destination = Path(destination)
    trusted(destination.parent, directory=True)
    previous = destination.with_name(destination.name + '.previous')
    require(not previous.exists() and not previous.is_symlink(), 'Archive or restore the previous reviewed operator before upgrading')
    if destination.exists() or destination.is_symlink():
        trusted(destination, directory=True)
        for name in [*CORE_FILES, 'execution.json', 'trusted-artifacts.json', 'installation.json']:
            trusted(destination / name)
        require(isinstance(upgrade_from, str) and re.fullmatch(r'[a-f0-9]{64}', upgrade_from)
                and digest(destination / 'installation.json') == upgrade_from,
                'Upgrades require the exact current installation.json digest')
        installed = read(destination / 'execution.json')
        require(installed['target'] == registered and installed['environment'] == execution['environment'],
                'An operator upgrade cannot change its registered target')
    else:
        require(upgrade_from is None, 'A fresh installation cannot claim an existing operator version')
    identities = [(execution[key], 'OAW release ' + execution['environment'] + ' ' + key)
                  for key in ('build_user', 'verify_user')]
    if preview:
        identities.append((preview['setup_user'], 'OAW preview setup'))
    for name, marker in identities:
        identity_record(name, marker)
    with tempfile.TemporaryDirectory(prefix='.oaw-operator-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        stage.chmod(0o700)
        for name in CORE_FILES:
            shutil.copyfile(source / name, stage / name)
            (stage / name).chmod(0o644)
        # Recheck copied bytes before installing any authority or running sysusers.
        inventory(stage, config['operator_files'], CORE_FILES)
        write_json(stage / 'execution.json', execution)
        write_json(stage / 'trusted-artifacts.json', {'version': 1, 'purpose': 'trusted_manual',
                   'target': registered, 'approved_artifacts': config.get('approved_artifacts', [])})
        write_json(stage / 'installation.json', {'version': 1, 'operator_files': config['operator_files'],
                   'execution': execution}, 0o644)
        accounts = stage / 'sysusers.conf'
        accounts.write_text(''.join(f'u {name} - "{marker}" /nonexistent /usr/sbin/nologin\n'
                                   for name, marker in identities), encoding='utf-8')
        accounts.chmod(0o600)
        run(SYSUSERS, str(accounts))
        accounts.unlink()
        if destination.exists():
            destination.rename(previous)
        try:
            stage.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
    return {'installed': str(destination), 'installation_sha256': digest(destination / 'installation.json'),
            'services_started': False, 'routing_changed': False}


def directives(content, section):
    result, current = {}, None
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
        elif current == section:
            key, separator, value = line.partition('=')
            require(separator and key not in result and not line.endswith('\\'), 'Duplicate or continued unit directives are not supported')
            result[key] = value
    return result


def service_policy(registered, execution, profile=None):
    """Fixed root-side policy, independent of the candidate application's renderer."""
    app, hosting, name = (registered[key] for key in ('app', 'hosting', 'service'))
    user = service_user(name, profile or 'gateway')
    policy = {'User': user, 'Group': user, 'WorkingDirectory': app,
        'Environment': 'PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp', 'UMask': '0077',
        'Restart': 'on-failure', 'RestartSec': '3', 'TimeoutStopSec': '45', 'KillMode': 'control-group',
        'NoNewPrivileges': 'yes', 'CapabilityBoundingSet': '', 'AmbientCapabilities': '',
        'ProtectSystem': 'strict', 'ProtectHome': 'yes', 'PrivateTmp': 'yes', 'PrivateDevices': 'yes',
        'RootDirectory': execution['sandbox_root'], 'MountAPIVFS': 'yes', 'PrivateIPC': 'yes', 'ProcSubset': 'pid',
        'ProtectProc': 'invisible', 'ProtectKernelTunables': 'yes', 'ProtectKernelModules': 'yes',
        'ProtectControlGroups': 'yes', 'RestrictSUIDSGID': 'yes', 'LockPersonality': 'yes',
        'RestrictAddressFamilies': 'AF_UNIX', 'PrivateNetwork': 'yes',
        'IPAddressDeny': 'any', 'MemoryMax': '384M' if profile else '128M', 'TasksMax': '128'}
    if profile:
        instance, view = hosting + '/instances/' + profile, '/var/lib/' + name + '-profile'
        policy.update({'Sockets': name + '-worker-' + profile + '.socket',
            'BindReadOnlyPaths': app + ':' + app + ' ' + instance + ':' + view, 'BindPaths': instance + '/data:' + view + '/data',
            'ExecStart': app + '/backend/.venv/bin/python -m backend.hosting.preview worker --config ' + view + '/worker.json --fd 3'})
    else:
        view = '/var/lib/' + name + '-control'
        policy.update({'Sockets': name + '-gateway.socket', 'BindPaths': hosting + '/control:' + view,
            'BindReadOnlyPaths': app + ':' + app + ' /run/' + name + ':/run/' + name,
            'ExecStart': app + '/backend/.venv/bin/python ' + app + '/scripts/hosted.py gateway --config '
                         + view + '/hosting.json --fd 3'})
    return policy


def reviewed_units(config):
    _, _, execution, registered, production, preview = validate(config)
    require(preview is not None, 'Only Preview has a separate unit installer')
    root = Path(preview['unit_source_directory']).absolute()
    if os.name != 'nt':
        reviewed_root = PurePosixPath(str(root))
        mutable = [item[key] for item in (registered, production) for key in TARGET_KEYS[:-1]]
        require(all(not reviewed_root.is_relative_to(PurePosixPath(path))
                    and not PurePosixPath(path).is_relative_to(reviewed_root)
                    for path in [*mutable, *OPERATOR_ROOTS.values(), execution['sandbox_root']]),
                'Reviewed units must be staged independently outside every installation and execution directory')
    files = preview.get('unit_files')
    name = registered['service']
    require(isinstance(files, dict), 'Review and pin the generated Preview unit inventory first')
    expected = {'sysusers.conf', name + '.target', name + '-gateway.service', name + '-gateway.socket'}
    profile_ids = set()
    for filename in files:
        match = re.fullmatch(re.escape(name) + r'-worker-([a-f0-9-]+)\.(service|socket)', filename)
        if match:
            require(str(UUID(match[1])) == match[1], 'Invalid Preview worker identity')
            profile_ids.add(match[1])
    require(len(profile_ids) == 2, 'Basic Preview requires exactly two separate test workspace identities')
    expected.update(f'{name}-worker-{profile}.{suffix}' for profile in profile_ids for suffix in ('service', 'socket'))
    inventory(root, files, expected)
    sysusers = [f'u {service_user(name)} - "OAW {name} gateway" /nonexistent /usr/sbin/nologin']
    sysusers.extend(f'u {service_user(name, profile)} - "OAW {name} {profile}" /nonexistent /usr/sbin/nologin'
                    for profile in profile_ids)
    require(set((root / 'sysusers.conf').read_text(encoding='utf-8').splitlines()) == set(sysusers),
            'Reviewed sysusers must contain only the dedicated Preview gateway and two workers')
    for filename in expected:
        profile = None
        content = (root / filename).read_text(encoding='utf-8')
        if filename == 'sysusers.conf':
            continue
        require(content.startswith('# OAW hosting directory: ' + registered['hosting'] + '\n'),
                'Reviewed units belong to a different hosting directory')
        if filename.endswith('.service'):
            policy = directives(content, 'Service')
            profile = None if filename == name + '-gateway.service' else filename[len(name) + 8:-8]
            require(policy == service_policy(registered, execution, profile),
                    'Reviewed Preview service differs from the fixed root-side security policy')
        elif filename.endswith('.socket'):
            policy = directives(content, 'Socket')
            profile = None if filename == name + '-gateway.socket' else filename[len(name) + 8:-7]
            require(policy == {'ListenStream': f'/run/{name}/{profile or "gateway"}.sock', 'SocketUser': service_user(name),
                    'SocketGroup': execution['verify_user'] if profile else 'www-data', 'SocketMode': '0660', 'DirectoryMode': '0755',
                    'RemoveOnStop': 'yes'}, 'Preview sockets must permit only gateway and dedicated verifier')
        unit = directives(content, 'Unit')
        if filename == name + '.target':
            members = {name + '-gateway.service', name + '-gateway.socket', *(f'{name}-worker-{profile}.{suffix}'
                       for profile in profile_ids for suffix in ('service', 'socket'))}
            require(set(unit) == {'Description', 'After', 'Wants'}
                    and unit['Description'] == f'OAW hosted users ({name})'
                    and set(unit['After'].split()) == members and set(unit['Wants'].split()) == members,
                    'Preview target can only activate its dedicated gateway and workers')
        else:
            expected_unit = {'PartOf': name + '.target'}
            if filename == name + '-gateway.service':
                expected_unit.update(Description=f'OAW authenticated gateway ({name})',
                    Requires=name + '-gateway.socket', After=name + '-gateway.socket network-online.target',
                    Wants='network-online.target')
            elif filename == name + '-gateway.socket':
                expected_unit['Description'] = f'OAW private Preview ingress ({name})'
            elif filename.endswith('.socket'):
                expected_unit['Description'] = f'Private OAW socket ({profile})'
            else:
                expected_unit.update(Description=f'OAW private workspace ({profile})',
                    Requires=f'{name}-worker-{profile}.socket',
                    After=f'{name}-worker-{profile}.socket network-online.target', Wants='network-online.target')
            require(unit == expected_unit, 'Preview unit dependencies cannot control unrelated services')
        require(not directives(content, 'Install') or directives(content, 'Install') == {'WantedBy': 'multi-user.target'},
                'Preview installation cannot add unrelated systemd activation hooks')
    return root, expected, profile_ids


def install_preview_units(config):
    plan(config, step='preview-units')
    check_admin(config)
    root, expected, profiles = reviewed_units(config)
    _, _, execution, registered, production, preview = validate(config)
    registration = Path(config['operator']) / 'execution.json'
    trusted(registration)
    require(read(registration) == execution, 'Preview units must use the independently installed execution registration')
    require_idle(identity_record(preview['setup_user'], 'OAW preview setup'))
    trusted(root, directory=True)
    for filename in expected:
        trusted(root / filename)
    target_root = Path(registered['hosting'])
    tree = plain_tree(target_root)
    require(not any((target_root / name).exists() for name in ('resource-provisioner.json', 'worker-common.env')),
            'Preview cannot inherit a cloud resource provisioner or shared model environment')
    hosting = read(target_root / 'control/hosting.json')
    require(hosting.get('public_origin') == preview['public_origin']
            and set(item['profile_id'] for item in hosting.get('profiles', [])) == profiles
            and not hosting.get('shared_models_origin') and not hosting.get('shared_environment_origin'),
            'Reviewed units must match the independent Preview configuration')
    app = Path(registered['app'])
    trusted(app, directory=True)
    for required in ('scripts/hosted.py', 'backend/hosting/preview.py', 'frontend/dist/index.html'):
        trusted(app / required)
    identities = [(service_user(registered['service']), f'OAW {registered["service"]} gateway')]
    for profile in profiles:
        instance = target_root / 'instances' / profile
        worker = read(instance / 'worker.json')
        require(worker.get('profile_id') == profile and worker.get('agent_runtime') == 'core.mock'
                and worker.get('data_root') == 'data', 'Preview requires independent mock-runtime workspace configuration')
        require((instance / 'worker.env').read_text(encoding='utf-8').strip() in
                {'', '# Operator-owned per-instance model/runtime environment. Never put login passwords here.'},
                'Preview cannot contain model or cloud credentials in worker.env')
        identities.append((service_user(registered['service'], profile), f'OAW {registered["service"]} {profile}'))
    for name, marker in identities:
        identity_record(name, marker)
    trusted(UNITS, directory=True)
    for filename in expected - {'sysusers.conf'}:
        path = UNITS / filename
        require(not path.exists() and not path.is_symlink(), 'Initial Preview install never replaces existing systemd units')
        state = run(SYSTEMCTL, 'show', filename, '--property=ActiveState', '--value').stdout.strip()
        require(state in {'inactive', 'failed', ''}, 'Preview services must be stopped before unit installation')
    # Validate the entire reviewed bundle before the first system mutation.
    run(ANALYZE, 'verify', *(str(root / filename) for filename in sorted(expected - {'sysusers.conf'})))
    run(SYSUSERS, str(root / 'sysusers.conf'))
    import pwd
    for path in tree:
        metadata = path.lstat()
        os.chown(path, 0, 0, follow_symlinks=False)
        path.chmod(0o700 if path.is_dir() else 0o600 | metadata.st_mode & 0o100)
    gateway = pwd.getpwnam(service_user(registered['service']))
    for path in plain_tree(target_root / 'control'):
        os.chown(path, gateway.pw_uid, gateway.pw_gid, follow_symlinks=False)
    for profile in profiles:
        instance = target_root / 'instances' / profile
        account = pwd.getpwnam(service_user(registered['service'], profile))
        os.chown(instance, 0, account.pw_gid)
        instance.chmod(0o750)
        os.chown(instance / 'worker.json', 0, account.pw_gid)
        (instance / 'worker.json').chmod(0o640)
        for path in plain_tree(instance / 'data'):
            os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
    for filename in expected - {'sysusers.conf'}:
        shutil.copyfile(root / filename, UNITS / filename)
        (UNITS / filename).chmod(0o644)
    run(SYSTEMCTL, 'daemon-reload')
    return {'installed_units': sorted(expected - {'sysusers.conf'}), 'services_started': False,
            'routing_changed': False, 'production_mutations': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--step', choices=('operator', 'preview-units'), default='operator')
    parser.add_argument('--upgrade-from', help='Exact installed installation.json digest; required for operator upgrades')
    parser.add_argument('--apply', action='store_true', help='Explicit offline root install; never starts/enables services or edits routing')
    args = parser.parse_args(argv)
    config = read(args.config)
    result = plan(config, step=args.step, upgrade_from=args.upgrade_from)
    if args.apply:
        trusted(args.config.absolute())
        result = install_operator(config, upgrade_from=args.upgrade_from) if args.step == 'operator' else install_preview_units(config)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    main()
