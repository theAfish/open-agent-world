"""Explicit offline copy of installed scientific packages into a workspace."""
import argparse
import importlib.util
from pathlib import Path
import shutil

def provision(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Choose a new dependency directory')
    destination.mkdir(parents=True)
    for name in ('ase', 'numpy', 'scipy', 'matplotlib', 'contourpy', 'cycler', 'fontTools', 'kiwisolver', 'packaging', 'PIL', 'pyparsing', 'dateutil', 'six'):
        spec = importlib.util.find_spec(name)
        if spec is None:
            if name in ('ase', 'numpy', 'scipy'):
                raise ValueError('Missing installed dependency: ' + name)
            continue
        path = Path(spec.origin)
        source = path.parent if spec.submodule_search_locations else path
        if source.is_dir():
            shutil.copytree(source, destination / source.name, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
            libraries = source.with_name(source.name + '.libs')
            if libraries.is_dir():
                shutil.copytree(libraries, destination / libraries.name)
        else:
            shutil.copy2(source, destination / source.name)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True)
    provision(parser.parse_args().destination)
