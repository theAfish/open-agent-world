"""Install the lightweight oaw command for the current user."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shlex
import sys


ROOT = Path(__file__).resolve().parents[1]
MARKER = "OAW CLI launcher"


class InstallError(Exception):
    pass


def current_windows_user():
    buffer = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(buffer))
    if not ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        raise InstallError("Cannot identify the Windows user.")
    return buffer.value


def check_windows_user():
    actual = current_windows_user()
    expected = os.environ.get("USERNAME")
    if expected and actual.casefold() != expected.casefold():
        raise InstallError(f"This process runs as {actual}, not {expected}. Run the installer in your own terminal.")


def default_bin_dir():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "OpenAgentWorld" / "bin"
    return Path.home() / ".local" / "bin"


def launchers(root: Path, *, windows: bool):
    if windows:
        quoted = str(root).replace("'", "''")
        powershell = (root / "oaw.ps1").read_text(encoding="utf-8")
        powershell = powershell.replace("$oawRoot = $PSScriptRoot", f"$oawRoot = '{quoted}'", 1)
        batch = ('@echo off\nsetlocal DisableDelayedExpansion\n'
                 'powershell.exe -NoProfile -File "%~dp0oaw.ps1" %*\nexit /b %errorlevel%\n')
        return {"oaw.ps1": f"# {MARKER}: {root}\n" + powershell,
                "oaw.cmd": f":: {MARKER}: {root}\n" + batch}
    return {"oaw": f"#!/usr/bin/env sh\n# {MARKER}: {root}\nexec {shlex.quote(sys.executable)} -I -B {shlex.quote(str(root / 'scripts/oaw.py'))} \"$@\"\n"}


def _path_key(value):
    return os.path.normcase(os.path.normpath(os.path.expandvars(value.strip().strip('"'))))


def update_windows_path(directory: Path, *, remove=False):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in value.split(";") if entry]
        matched = [entry for entry in entries if _path_key(entry) == _path_key(str(directory))]
        if remove:
            updated = [entry for entry in entries if entry not in matched]
        elif matched:
            return False
        else:
            updated = [*entries, str(directory)]
        if updated == entries:
            return False
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(updated))

    # Let Explorer propagate the user PATH to subsequently opened terminals.
    result = ctypes.c_size_t()
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p,
                     ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_size_t
    send(0xFFFF, 0x1A, 0, "Environment", 2, 2000, ctypes.byref(result))
    return True


def install(root: Path, directory: Path, *, windows: bool):
    content = launchers(root, windows=windows)
    for name in content:
        target = directory / name
        owned = False
        if target.is_file() and not target.is_symlink():
            lines = target.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:2]
            owned = any(line.removeprefix("# ").removeprefix(":: ").startswith(MARKER + ": ") for line in lines)
        if target.is_symlink() or target.exists() and not owned:
            raise InstallError(f"{target} already exists and does not belong to OAW. Choose another --bin-dir.")
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in content.items():
        target = directory / name
        target.write_text(text, encoding="utf-8-sig" if windows and name.endswith(".ps1") else "utf-8", newline="\n")
        if not windows:
            target.chmod(0o755)
    return list(content)


def uninstall(root: Path, directory: Path, *, windows: bool):
    removed = []
    for name in launchers(root, windows=windows):
        target = directory / name
        if not target.is_file() or target.is_symlink():
            continue
        content = target.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        expected = f"{MARKER}: {root}"
        if any(expected == line.removeprefix("# ").removeprefix(":: ") for line in content[:2]):
            target.unlink()
            removed.append(name)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, help="Install into a chosen command directory")
    parser.add_argument("--no-path", action="store_true", help="Leave persistent PATH unchanged")
    parser.add_argument("--uninstall", action="store_true", help="Remove only launchers belonging to this OAW location")
    parser.add_argument("--json", action="store_true", help="Print installation details as JSON")
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 11):
            raise InstallError("OAW needs Python 3.11 or newer. No command was installed.")
        if os.name == "nt" and (args.bin_dir is None or not args.no_path):
            check_windows_user()
        directory = (args.bin_dir or default_bin_dir()).expanduser().resolve()
        files = uninstall(ROOT, directory, windows=os.name == "nt") if args.uninstall else install(ROOT, directory, windows=os.name == "nt")
        path_updated = False
        if os.name == "nt" and not args.no_path:
            # A different checkout may now own this command directory.
            if not args.uninstall or not directory.exists() or not any(directory.iterdir()):
                path_updated = update_windows_path(directory, remove=args.uninstall)
        on_path = any(_path_key(entry) == _path_key(str(directory)) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)
        path_retained = args.uninstall and directory.exists() and any(directory.iterdir())
        result = {"bin_dir": str(directory), "files": files, "path_updated": path_updated,
                  "on_path": on_path, "uninstalled": args.uninstall, "path_retained": path_retained}
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif args.uninstall:
            print("Removed this OAW command." if files else "No command belonging to this OAW location was installed.")
        else:
            print(f"Installed oaw in {directory}")
            if not on_path:
                if os.name == "nt":
                    print("Open a new terminal, then run: oaw --help")
                else:
                    print(f"Run once in this shell: export PATH={shlex.quote(str(directory))}:\"$PATH\"")
                    print("Add that line to your shell profile to keep it for future terminals.")
            else:
                print("Try: oaw --help")
        return 0
    except (InstallError, OSError, KeyError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
