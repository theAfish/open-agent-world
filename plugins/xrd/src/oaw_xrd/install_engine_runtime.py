"""Create an isolated scientific runtime from the bundled dependency specification.
Usage: python -m oaw_xrd.install_engine_runtime --data-root <OAW-profile>
The host selects a compatible private Python; scientific data is preserved.
"""
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args()
    from .runtime_environment import prepare
    handle = prepare(args.data_root)
    print(f'XRD scientific environment ready: {handle.root}')

if __name__ == '__main__':
    main()
