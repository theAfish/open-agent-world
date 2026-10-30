"""Small shared CLI helpers, copied into each independently runnable Skill."""
import argparse
import io
import json
from pathlib import Path
import runpy
import sys
import zipfile


def utf8_output():
    # Windows redirected Python output otherwise uses the host ANSI code page.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def workspace_path(value, suffix=None, *, existing=True):
    root = Path.cwd().resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("Use a workspace-relative path")
    path = (root / candidate).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Path must stay inside the Sandbox workspace")
    if suffix and path.suffix.lower() != suffix:
        raise ValueError(f"This helper supports {suffix}; convert older or macro-enabled formats explicitly")
    if existing and not path.is_file():
        raise ValueError("Input file does not exist")
    return path


def office_input(value, suffix):
    path = workspace_path(value, suffix)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Helper input exceeds 64 MiB; use a targeted custom workflow")
    with zipfile.ZipFile(path) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 256 * 1024 * 1024:
            raise ValueError("Expanded Office document exceeds 256 MiB")
    return path


def spec(value):
    return json.loads(workspace_path(value, ".json").read_text(encoding="utf-8-sig"))


def save_new(document, destination, suffix, reopen):
    path = workspace_path(destination, suffix, existing=False)
    if path.exists():
        raise ValueError("Output already exists; choose a new filename")
    buffer = io.BytesIO()
    document.save(buffer)
    checked = reopen(io.BytesIO(buffer.getvalue()))
    if hasattr(checked, "close"):
        checked.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(buffer.getvalue())
    return {"output": path.relative_to(Path.cwd().resolve()).as_posix(),
            "size_bytes": len(buffer.getvalue()), "structurally_verified": True,
            "visually_verified": False}


def clipped(value, limit=2000):
    value = str(value)
    return value[:limit], len(value) > limit


def window(parser, *, default=50, maximum=200):
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=default)
    parser.set_defaults(window_max=maximum)


def emit_main(function):
    utf8_output()
    try:
        result = function()
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ValueError("Inspection result exceeds 256 KiB; request a smaller window")
        print(encoded)
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None


def run_workspace_script():
    utf8_output()
    parser = argparse.ArgumentParser(description="Run workspace Python with this Skill's prepared dependencies")
    parser.add_argument("script")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    path = workspace_path(args.script, ".py")
    sys.argv = [str(path), *args.arguments]
    sys.path.insert(0, str(path.parent))
    runpy.run_path(str(path), run_name="__main__")
