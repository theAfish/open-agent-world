"""Non-root, offline scientific worker fixture for native/cloud comparison.

Uses synthetic diffraction, not a claim of real-sample phase identification.
No cloud allocation, model calls, downloads or external reference database.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def run(prefix, directory):
    if os.name != 'posix' or os.geteuid() == 0:
        raise RuntimeError('Run acceptance as the non-root scientific worker identity')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    package = prefix / 'pack/src/oaw_xrd'
    python = str(prefix / 'venv/bin/python')
    launcher = str(package / 'worker_launcher.py')
    env = {**os.environ, 'MPLBACKEND': 'Agg', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
           'TF_NUM_INTRAOP_THREADS': '1', 'TF_NUM_INTEROP_THREADS': '1', 'OAW_XRD_ENVIRONMENT': '-'}
    # Independently specified peaks exercise the batch matching worker.
    import math
    points = [[10+i*.02, 100+1000*math.exp(-((10+i*.02-20.04)/.07)**2)
               +500*math.exp(-((10+i*.02-30)/.07)**2)] for i in range(1501)]
    inputs = [{'node_id': 'p', 'revision': 1, 'value': {'kind': 'pattern', 'filename': 'synthetic',
               'sha256': 'fixture-p', 'metadata': {}, 'points': points}},
              {'node_id': 'r', 'revision': 1, 'value': {'kind': 'reference', 'filename': 'reference',
               'sha256': 'fixture-r', 'metadata': {}, 'peaks': [{'two_theta': 20,
               'd': 1.540593/(2*math.sin(math.radians(10))), 'intensity': 100, 'hkl': [1, 0, 0]}]}}]
    options = {'mode': 'match', 'wavelength': 1.540593, 'tolerance_deg': .15,
               'prominence_fraction': .03, 'smoothing_deg': .03,
               'min_peak_distance': .1, 'reference_min_intensity': 5}
    (directory/'input.json').write_text(json.dumps(options))
    (directory/'input-snapshot.json').write_text(json.dumps(inputs))
    with (directory/'batch.log').open('w') as log:
        subprocess.run([python, '-I', '-u', launcher, '-', 'worker.py', str(directory.parent), str(directory)],
                       env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=True, timeout=120)
    result = json.loads((directory/'result.json').read_text())
    assert len(result['observed_peaks']) == 2 and len(result['unexplained_peaks']) == 1
    delta = result['candidates'][0]['matches'][0]['delta']
    assert abs(delta-.04) < .021

    # Real CIF reflection generation through the JSON-lines worker, without mocks.
    from pymatgen.core import Lattice, Structure
    from pymatgen.io.cif import CifWriter
    crystal = Structure.from_spacegroup('Fm-3m', Lattice.cubic(5.64), ['Na', 'Cl'], [[0, 0, 0], [.5, .5, .5]])
    payload = {'pattern': {'points': points}, 'candidates': [{'candidate_id': 'nacl',
               'source_cif': {'text': str(CifWriter(crystal))}}], 'options': {'wavelength': 1.540593}}
    (directory/'multiphase-input.json').write_text(json.dumps(payload))
    with (directory/'interactive.log').open('w') as log:
        response = subprocess.run([python, '-I', '-u', launcher, '-', 'multiphase_worker.py', str(directory)],
                    env=env, input=json.dumps({'command': 'decision_evidence'})+'\n', text=True,
                    stdout=subprocess.PIPE, stderr=log, check=True, timeout=120)
    reply = json.loads(response.stdout)
    assert reply['ok'], reply
    peaks = reply['value']['candidates'][0]['full_reference_peaks']
    assert peaks and all(0 < angle < 180 and intensity >= 0 for angle, intensity in peaks)
    fit = directory / 'fit'
    fit.mkdir()
    (fit/'input.json').write_text(json.dumps({'demo': True, 'preoptimize': False,
        'wavelength': 1.540593, 'low_angle': 10, 'high_angle': 80, 'iterations': 1}))
    with (fit/'console.log').open('w') as log:
        subprocess.run([python, '-I', '-u', launcher, '-', 'worker.py', str(directory), str(fit)],
                       env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, check=True, timeout=240)
    fitted = json.loads((fit/'result.json').read_text())
    assert fitted['status'] == 'completed' and fitted['files']
    rwp = float(fitted['solver']['Rwp_percent'])
    assert math.isfinite(rwp) and rwp >= 0
    report = {'batch_match': True, 'interactive_cif': True, 'match_delta': delta,
              'demo_fit_rwp_percent': rwp, 'demo_fit_converged': fitted['converged'],
              'reference_peaks': peaks, 'full_library_search': False, 'full_refinement': False}
    (directory/'acceptance.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path('/opt/oaw-xrd'))
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.prefix, args.directory.resolve())))
