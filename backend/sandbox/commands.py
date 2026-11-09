"""Recognize explicit terminal/input requests on the non-interactive command API."""
import re
from pathlib import PureWindowsPath
from .models import SandboxValidationError


def python_entrypoint(argv):
    """Recognize a direct Python entrypoint, never commands embedded in a shell.

    Returns (script/code/module, value, first application argument index).
    Shared by execution tracking and collection; not permission to execute.
    """
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(a, str) for a in argv):
        return None
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", PureWindowsPath(argv[0]).name.lower()):
        return None
    index = 1
    while index < len(argv):
        option = argv[index]
        if option in {"-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-b", "-bb", "-q"}:
            index += 1
        elif option in {"-W", "-X", "--check-hash-based-pycs"} and index + 1 < len(argv):
            index += 2
        elif option.startswith(("-W", "-X")) and len(option) > 2:
            index += 1
        else:
            break
    if index >= len(argv):
        return None
    if argv[index] in {"-c", "-m"}:
        return ("code" if argv[index] == "-c" else "module", argv[index+1], index+2) if index+1 < len(argv) else None
    if argv[index] == "--":
        index += 1
    if index >= len(argv) or argv[index].startswith("-"):
        return None
    return "script", argv[index], index+1


def needs_managed_python(argv, mode="auto", *, skill=False):
    """Classify Python requests for host preparation tracking.

    Native backends can select managed Python for additional command types.
    Their capability flags decide whether omitting that runtime is supported.
    """
    if not isinstance(mode, str) or mode not in {"auto", "managed", "none"}:
        raise SandboxValidationError("python_environment must be auto, managed, or none")
    if mode != "auto":
        return mode == "managed"
    if skill:
        return True
    if not argv:
        return False
    # Keep this list in sync with SharedPythonRuntime.command. An absolute
    # interpreter outside this set is an explicit host-path request, not an
    # alias that the runtime will replace with its managed interpreter.
    return str(argv[0]).lower() in {
        "python", "python3", "python.exe", "python3.exe", "/usr/bin/python3",
    }


def require_noninteractive(argv=None, command=None):
    if command is not None:
        # Only recognize unambiguous simple requests. Arbitrary shell programs
        # still receive closed stdin and the configured deadline.
        words = command.strip().split()
        if re.search(r"(^|[;&\n])\s*(?:read\b|set\s+/p\b|pause\s*(?:$|[;&\n]))", command, re.I):
            raise SandboxValidationError("Interactive input is unsupported; use environment settings or explicit arguments")
    else:
        words = list(argv or [])
    if not words:
        return
    executable = PureWindowsPath(words[0].strip('"')).name.lower().removesuffix(".exe")
    interactive = executable in {"vim", "vi", "nano", "less", "top", "htop"}
    interactive |= command is not None and executable in {"python", "python3", "node", "bash", "sh", "cmd", "powershell", "pwsh"} and len(words) == 1
    interactive |= executable in {"python", "python3", "node"} and any(p in {"-i", "-"} for p in words[1:])
    interactive |= executable == "ssh" and any(p in {"-t", "-tt"} for p in words[1:])
    interactive |= executable == "sudo" and "-n" not in words[1:]
    if interactive:
        raise SandboxValidationError("This command requests unsupported interactive input or a terminal. Use a non-interactive command with explicit arguments.")
