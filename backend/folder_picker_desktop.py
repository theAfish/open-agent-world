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
        if request.get("kind") == "file":
            selected = filedialog.askopenfilename(parent=root, title="Select a file for Open Agent World", initialdir=request["initial_path"])
        else:
            selected = filedialog.askdirectory(
                parent=root, title="Select a folder for Open Agent World",
                initialdir=request["initial_path"], mustexist=True,
            )
        # ASCII JSON also works when the desktop's locale is not UTF-8.
        print(json.dumps(selected or None))
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
