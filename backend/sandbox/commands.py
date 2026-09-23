"""Recognize explicit terminal/input requests on the non-interactive command API."""
import re
from pathlib import PureWindowsPath
from .models import SandboxValidationError


def needs_managed_python(argv, mode="auto", *, skill=False):
    """Select Seatbelt's optional Python environment without parsing a shell.

    A shell can invoke anything, so callers must explicitly request ``managed``
    when a shell command needs OAW's Python. Skill runs retain the historical
    managed environment by default, including executable scripts with shebangs.
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
