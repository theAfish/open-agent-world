from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.sandbox import darwin_python_copy as copier


@pytest.fixture
def backend(tmp_path, monkeypatch):
    prefix = tmp_path / 'backend'
    binary = prefix / 'bin/python'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'\xcf\xfa\xed\xfe' + b'fake-python')
    stdlib = prefix / 'lib' / f'python{copier.sys.version_info.major}.{copier.sys.version_info.minor}'
    stdlib.mkdir(parents=True)
    (stdlib / 'os.py').write_text('# standard library')
    (stdlib / 'site-packages').mkdir()
    (stdlib / 'site-packages/host_only.py').write_text('raise AssertionError("not copied")')
    (stdlib / 'sitecustomize.py').write_text('raise AssertionError("not run")')
    (stdlib / 'host.pth').write_text('/private/host')
    library = prefix / 'lib/libpython.dylib'
    library.write_bytes(b'\xcf\xfa\xed\xfe' + b'fake-library')
    monkeypatch.setattr(copier.sys, 'base_prefix', str(prefix))
    monkeypatch.setattr(copier.sys, '_base_executable', str(binary))
    monkeypatch.setattr(copier.sysconfig, 'get_path', lambda _: str(stdlib))
    monkeypatch.setattr(copier.ssl, 'get_default_verify_paths', lambda: SimpleNamespace(cafile=None))
    return prefix, binary, library


def test_backend_runtime_copy_excludes_packages_and_relocates_and_signs_libraries(tmp_path, monkeypatch, backend):
    prefix, binary, library = backend
    calls = []
    def command(arguments):
        calls.append(list(map(str, arguments)))
        if arguments[:2] == ['/usr/bin/otool', '-l']:
            return 'cmd LC_RPATH\ncmdsize 48\npath @loader_path/../lib (offset 12)\n'
        if arguments[:2] == ['/usr/bin/otool', '-L']:
            name = '@rpath/libpython.dylib' if Path(arguments[-1]) == binary else str(library)
            return f'file:\n\t{name} (compatibility version 1.0.0, current version 1.0.0)\n'
        return ''
    monkeypatch.setattr(copier, '_command', command)
    destination = tmp_path / 'managed'
    copier.copy_backend_python(destination)
    copied = next(destination.glob('cpython-*/bin/python'))
    assert copied.resolve().is_relative_to(destination)
    assert not list(destination.rglob('site-packages'))
    assert not list(destination.rglob('sitecustomize.py'))
    assert not list(destination.rglob('*.pth'))
    assert next(destination.glob('cpython-*/lib/libpython.dylib')).is_file()
    assert any('-change' in args and '@loader_path/../lib/libpython.dylib' in args for args in calls)
    assert any(args[0] == '/usr/bin/codesign' for args in calls)
    assert calls[-1][1:3] == ['-I', '-S']
    assert copier.matches_source(destination)
    assert all(str(prefix / 'site-packages') not in ' '.join(args) for args in calls)


def test_unresolved_native_library_never_publishes_source_receipt(tmp_path, monkeypatch, backend):
    def command(arguments):
        if arguments[1] == '-l':
            return ''
        return 'file:\n\t@rpath/not-present.dylib (compatibility version 1.0.0, current version 1.0.0)\n'
    monkeypatch.setattr(copier, '_command', command)
    destination = tmp_path / 'managed'
    with pytest.raises(RuntimeError, match='Cannot resolve backend Python library'):
        copier.copy_backend_python(destination)
    assert not (destination / 'backend-source.json').exists()
