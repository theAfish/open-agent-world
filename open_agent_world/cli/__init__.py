"""A lightweight command-line client for the OAW management API."""


def main(argv=None):
    from .main import main as run
    return run(argv)
