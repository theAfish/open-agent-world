"""Trusted install-time probe; importing this file performs no installation."""
from pathlib import Path
import sys


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--check':
        raise SystemExit('Usage: verify_environment.py --check ENVIRONMENT')
    import site
    site.addsitedir(str(Path(sys.argv[2]) / 'packages'))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from OAW_XRDfit.src import WPEM
    from OAW_XRDfit.src.EMBraggOpt.EMBraggSolver import WPEMsolver
    from OAW_XRDfit.src.Extinction.m3gnet.models import M3GNet

    # Import checks alone miss incompatible Keras/checkpoint combinations.
    # Load the bundled weights directly; verification must never download them.
    weights = Path(__file__).resolve().parent / 'OAW_XRDfit/src/Extinction/m3gnet/models/MP-2021.2.8-EFS'
    M3GNet.from_dir(str(weights))
