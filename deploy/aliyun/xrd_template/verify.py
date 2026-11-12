"""Build-time XRD readiness; not a substitute for scientific cloud acceptance."""
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tomllib


def verify(prefix):
    pack = prefix / 'pack'
    project = tomllib.loads((pack / 'pyproject.toml').read_text())['project']
    result = subprocess.run([str(prefix / 'qualx/bin/qualx'), '--nogui', '--help'],
                            capture_output=True, text=True, check=True, timeout=20)
    if not all(flag in result.stdout + result.stderr for flag in ('--settings-dir', '--database', '--wavelength')):
        raise RuntimeError('QualX does not implement the reviewed isolation contract')
    from pymatgen.core import Lattice, Structure
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    crystal = Structure(Lattice.cubic(5.431), ['Si', 'Si'], [[0, 0, 0], [.25, .25, .25]])
    pattern = XRDCalculator(wavelength='CuKa').get_pattern(crystal, two_theta_range=(10, 80))
    if not len(pattern.x) or not all(float(value) >= 0 for value in pattern.y):
        raise RuntimeError('XRD reference simulation failed')
    sys.path.insert(0, str(pack / 'src/oaw_xrd'))
    from OAW_XRDfit.src.Extinction.m3gnet.models import M3GNet
    weights = pack / 'src/oaw_xrd/OAW_XRDfit/src/Extinction/m3gnet/models/MP-2021.2.8-EFS'
    model = M3GNet.from_dir(str(weights))
    import math
    energy = float(model.predict_structure(crystal).numpy().ravel()[0])
    if not math.isfinite(energy):
        raise RuntimeError('Bundled M3GNet inference produced a nonfinite energy')
    # Scientific imports can expose vendored metadata; report only this venv.
    versions = {distribution.metadata['Name'].lower().replace('_', '-').replace('.', '-'): distribution.version
                for distribution in metadata.distributions(path=[sysconfig.get_paths()['purelib']])}
    for line in (prefix / 'requirements.lock').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.split('==')
            if versions.get(name.lower().replace('_', '-').replace('.', '-')) != version:
                raise RuntimeError('Installed package differs from the image lock: ' + name)
    report = {'pack_id': 'research.xrd', 'pack_version': project['version'],
              'source_revision': os.environ['OAW_XRD_SOURCE_REVISION'],
              'python': sys.version.split()[0], 'packages': versions, 'qualx': '1.0.5-oaw',
              'm3gnet_fixture_energy': energy,
              'reference_peak_count': len(pattern.x), 'scientific_acceptance': False}
    (prefix / 'verified.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]))))
