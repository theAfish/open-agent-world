"""Stdlib bootstrap shared by scientific workers and their nested children."""
from pathlib import Path
import runpy
import sys


def main():
    environment, name, *arguments = sys.argv[1:]
    package = Path(__file__).resolve().parent
    if name not in {'worker.py', 'multiphase_worker.py', 'multiphase_wpem.py'}:
        raise ValueError('Unknown XRD worker')
    if environment != '-':
        import site
        site.addsitedir(str(Path(environment) / 'packages'))
    sys.path.insert(0, str(package))
    script = package / name
    sys.argv = [str(script), *arguments]
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()
