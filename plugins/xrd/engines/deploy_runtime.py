"""Complete a QualX portable Windows install with toolchain DLL dependencies.

Only copies libraries supplied by the explicit UCRT64 toolchain. Windows system
DLLs are never copied. Existing toolchain DLLs are refreshed if they differ.
"""
from __future__ import annotations

import argparse
import filecmp
import json
from pathlib import Path
import re
import shutil
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', required=True, type=Path,
                        help='MSYS2 UCRT64 prefix containing bin/objdump.exe')
    parser.add_argument('--install', required=True, type=Path,
                        help='Portable CMake install directory containing bin/qualx.exe')
    args = parser.parse_args()
    toolbin = args.toolchain.resolve() / 'bin'
    installed = args.install.resolve()
    destination = installed / 'bin'
    objdump = toolbin / 'objdump.exe'
    executable = destination / 'qualx.exe'
    if not objdump.is_file() or not executable.is_file():
        parser.error('Both TOOLCHAIN/bin/objdump.exe and INSTALL/bin/qualx.exe must exist.')

    available = {path.name.casefold(): path for path in toolbin.glob('*.dll')}
    queue = [*installed.rglob('*.dll'), executable]
    seen: set[Path] = set()
    copied: set[str] = set()
    external_imports: dict[str, set[str]] = {}
    while queue:
        binary = queue.pop().resolve()
        if binary in seen:
            continue
        seen.add(binary)
        output = subprocess.check_output(
            [str(objdump), '-p', str(binary)], text=True, encoding='utf-8', errors='replace',
        )
        for name in re.findall(r'DLL Name:\s*(\S+)', output):
            source = available.get(name.casefold())
            if source is None:
                external_imports.setdefault(name, set()).add(str(binary.relative_to(installed)))
                continue
            target = destination / source.name
            if not target.exists() or not filecmp.cmp(source, target, shallow=False):
                shutil.copy2(source, target)
                copied.add(source.name)
                seen.discard(target.resolve())
            queue.append(target)

    report = {
        'toolchain': str(args.toolchain.resolve()),
        'install': str(installed),
        'copied_libraries': sorted(copied),
        # These include normal Windows API dependencies and optional Qt drivers.
        # A missing third-party dependency is not silently presented as packaged.
        'imports_not_in_toolchain': {name: sorted(users) for name, users in sorted(external_imports.items())},
    }
    (installed / 'runtime-deployment.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Copied or refreshed {len(copied)} toolchain DLLs; see runtime-deployment.json.')


if __name__ == '__main__':
    main()
