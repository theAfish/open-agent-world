"""Open validated Sandbox directories in the local host's file manager."""
import os
import subprocess
import sys


def open_folder(path: str) -> None:
    if sys.platform == 'win32':
        os.startfile(path, 'explore')
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
