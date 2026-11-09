"""Relocate only the backend's Python runtime, excluding third-party packages."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import ssl


def source_identity():
    executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    stat = executable.stat()
    return {'format': 1, 'prefix': str(Path(sys.base_prefix).resolve()), 'python': str(executable),
            'version': list(sys.version_info[:3]), 'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}


def matches_source(root):
    try:
        return json.loads((root / 'backend-source.json').read_text()) == source_identity()
    except (OSError, ValueError):
        return False


def _command(arguments):
    return subprocess.run(list(map(str, arguments)), check=True, capture_output=True,
                          text=True, timeout=60, env={key: value for key, value in os.environ.items()
                          if not key.startswith(('PYTHON', 'DYLD_'))}).stdout


def _macho(path):
    with path.open('rb') as stream:
        return stream.read(4) in {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
            b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def copy_backend_python(root):
    """Copy stdlib and the Mach-O library closure; never copy site-packages.

    Library references are relative to each copied file. Both the global cache
    and a profile can therefore move the tree without granting host-env reads.
    """
    identity = source_identity()
    executable, prefix = Path(identity['python']), Path(identity['prefix'])
    version = '.'.join(map(str, sys.version_info[:2]))
    source_stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    if not (source_stdlib / 'os.py').is_file():
        raise RuntimeError(f'Backend Python standard library is unavailable: {source_stdlib}')
    destination = root / f'cpython-{version}-backend'
    binary = destination / 'bin' / f'python{version}'
    binary.parent.mkdir(parents=True)
    shutil.copy2(executable, binary)
    stdlib = destination / 'lib' / f'python{version}'
    shutil.copytree(source_stdlib, stdlib, symlinks=False,
        ignore=shutil.ignore_patterns('site-packages', '__pycache__', '*.pyc', '*.pth', '*.a',
                                      'sitecustomize.py', 'usercustomize.py'))
    # OpenSSL can embed the source environment's CA path. Preserve its public
    # certificate bundle and let the runtime point SSL_CERT_FILE at the copy.
    certificate = ssl.get_default_verify_paths().cafile
    if certificate and Path(certificate).is_file():
        target = destination / 'ssl' / 'cert.pem'
        target.parent.mkdir(parents=True)
        shutil.copy2(certificate, target)
    pairs = {executable: binary}
    for target in stdlib.rglob('*'):
        source = source_stdlib / target.relative_to(stdlib)
        if target.is_file() and source.suffix in {'.so', '.dylib'}:
            pairs[source.resolve()] = target
    pending = list(pairs)
    visited, changes = set(), {}
    while pending:
        source = pending.pop()
        if source in visited or not _macho(source):
            continue
        visited.add(source)
        target = pairs[source]
        load_commands = _command(['/usr/bin/otool', '-l', source])
        rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', load_commands)

        def expand(value):
            return value.replace('@loader_path', str(source.parent)).replace('@executable_path', str(executable.parent))

        for line in _command(['/usr/bin/otool', '-L', source]).splitlines()[1:]:
            if ' (compatibility version' not in line:
                continue
            reference = line.strip().split(' (compatibility version', 1)[0]
            if reference.startswith(('/usr/lib/', '/System/Library/')):
                continue
            if reference.startswith('@rpath/'):
                suffix = reference.removeprefix('@rpath/')
                candidates = [Path(expand(path)) / suffix for path in rpaths]
                candidates.extend([source.parent / suffix, prefix / 'lib' / suffix])
            else:
                candidates = [Path(expand(reference))]
            library = next((path.resolve() for path in candidates if path.is_file()), None)
            if library is None:
                raise RuntimeError(f'Cannot resolve backend Python library {reference} used by {source}')
            if library == source:  # LC_ID_DYLIB is not an imported dependency.
                continue
            if library not in pairs:
                relative = (library.relative_to(prefix) if library.is_relative_to(prefix)
                            else Path('native') / library.relative_to(library.anchor))
                copied = destination / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(library, copied)
                pairs[library] = copied
                pending.append(library)
            relocated = '@loader_path/' + os.path.relpath(pairs[library], target.parent)
            changes.setdefault(target, []).extend(['-change', reference, relocated])
    for target, arguments in changes.items():
        _command(['/usr/bin/install_name_tool', *arguments, target])
        _command(['/usr/bin/codesign', '--force', '--sign', '-', target])
    for alias in ('python', 'python3'):
        (binary.parent / alias).symlink_to(binary.name)
    # The copied interpreter must be functional before its source receipt is
    # published. -S prevents all package/startup hooks from running on the host.
    _command([binary, '-I', '-S', '-c', 'import encodings,ssl,sqlite3,ctypes,zlib,bz2,lzma,venv'])
    (root / 'backend-source.json').write_text(json.dumps(identity), encoding='utf-8')
