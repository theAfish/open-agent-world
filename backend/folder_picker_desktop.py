"""Isolated Tk main thread for Linux/macOS desktop folder dialogs."""
import json
import sys
import tkinter
from tkinter import filedialog


def main() -> None:
    request = json.load(sys.stdin)
    root = tkinter.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(
            parent=root, title="Select a Workspace folder for Open Agent World",
            initialdir=request["initial_path"], mustexist=True,
        )
        # ASCII JSON also works when the desktop's locale is not UTF-8.
        print(json.dumps(selected or None))
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
