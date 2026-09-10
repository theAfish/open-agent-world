"""Retarget installer-created launchers without importing installed packages."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import tempfile


def python_shebang(executable):
    path = str(executable)
    if len(os.fsencode(path)) + 3 <= 127 and not any(c.isspace() for c in path):
        return f'#!{path}\n'.encode()
    return f'#!/bin/sh\n\'\'\'exec\' {shlex.quote(path)} "$0" "$@"\n\' \'\'\'\n'.encode()


def _windows_python_path(path, replacements):
    # LOAD_LIBRARY_AS_DATAFILE reads resources only; never run package code/DllMain.
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'LoadLibraryExW': ([w.LPCWSTR, w.HANDLE, w.DWORD], w.HMODULE),
        'FindResourceW': ([w.HMODULE, w.LPCWSTR, w.LPCWSTR], w.HANDLE),
        'SizeofResource': ([w.HMODULE, w.HANDLE], w.DWORD),
        'LoadResource': ([w.HMODULE, w.HANDLE], w.HANDLE),
        'LockResource': ([w.HANDLE], ctypes.c_void_p),
        'FreeLibrary': ([w.HMODULE], w.BOOL),
        'BeginUpdateResourceW': ([w.LPCWSTR, w.BOOL], w.HANDLE),
        'UpdateResourceW': ([w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.WORD, ctypes.c_void_p, w.DWORD], w.BOOL),
        'EndUpdateResourceW': ([w.HANDLE, w.BOOL], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        getattr(api, name).argtypes = args
        getattr(api, name).restype = result
    module = api.LoadLibraryExW(str(path), None, 2)
    if not module:
        return False
    resource_type = ctypes.cast(ctypes.c_void_p(10), w.LPCWSTR)  # RT_RCDATA
    try:
        def read(name):
            resource = api.FindResourceW(module, name, resource_type)
            if not resource:
                return None
            size = api.SizeofResource(module, resource)
            address = api.LockResource(api.LoadResource(module, resource))
            return ctypes.string_at(address, size) if address else None
        if read('UV_TRAMPOLINE_KIND') != b'\x01':  # Script, never Python itself.
            return False
        replacement = replacements.get(read('UV_PYTHON_PATH'))
        if replacement is None:
            return False
    finally:
        api.FreeLibrary(module)
    handle = api.BeginUpdateResourceW(str(path), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if not api.UpdateResourceW(handle, resource_type, 'UV_PYTHON_PATH', 0,
                              replacement, len(replacement)):
        error = ctypes.WinError(ctypes.get_last_error())
        api.EndUpdateResourceW(handle, True)
        raise error
    if not api.EndUpdateResourceW(handle, False):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def repair_python_launchers(bin_path: Path, installer_python: Path, python: Path):
    """Only replace known installer interpreter references in managed bin files.

    Atomic copies avoid modifying uv cache hardlinks and preserve executable modes.
    No package modules, metadata entry points or launcher bodies are executed.
    """
    replacements = {str(installer_python).encode(): str(python).encode()}
    if os.name == 'nt':
        replacements[str(installer_python.with_name('pythonw.exe')).encode()] = str(
            python.with_name('pythonw.exe')).encode()
    for path in bin_path.iterdir():
        if path.is_symlink() or not path.is_file() or path == python:
            continue
        with path.open('rb') as stream:
            header = stream.read(4096)
        old_header = None
        if os.name != 'nt':
            for candidate in (b'#!' + os.fsencode(installer_python) + b'\n',
                              python_shebang(installer_python)):
                if header.startswith(candidate):
                    old_header = candidate
                    break
            if old_header is None:
                continue
        elif not (path.suffix.lower() == '.exe' and header.startswith(b'MZ')):
            continue
        descriptor, raw = tempfile.mkstemp(prefix='.oaw-launcher-', dir=bin_path)
        os.close(descriptor)
        temporary = Path(raw)
        try:
            if os.name == 'nt':
                shutil.copy2(path, temporary)
                if not _windows_python_path(temporary, replacements):
                    continue
            else:
                with path.open('rb') as source, temporary.open('wb') as target:
                    source.seek(len(old_header))
                    target.write(python_shebang(python))
                    shutil.copyfileobj(source, target)
                shutil.copymode(path, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
