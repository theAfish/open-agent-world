"""MatCreator verification and worker activation for OAW-managed environments.

Host setup delegates lifecycle to OAW. The isolated worker uses only the small
stdlib activation helpers below; it never imports host packages or installs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import sysconfig

PACKAGE = Path(__file__).resolve().parent
REQUIREMENTS = PACKAGE / "native-requirements.txt"
ENGINE = PACKAGE.parent / "oaw_matcreator_engine"
REPAIR = "MatCreator plugin setup is incomplete. Prepare its native environment using OAW Pack installation."


def environment_spec():
    # Lazy host-only imports: native_worker runs this module under -I -S.
    from open_agent_world.plugin_api import PythonEnvironmentSpec
    from packaging.requirements import Requirement
    requirements = tuple(str(Requirement(line.strip())) for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
                         if line.strip() and not line.lstrip().startswith("#"))
    return PythonEnvironmentSpec("matcreator", "native", requirements, ">=3.12",
        ("litellm", "google.adk.runners", "json_repair", "tiktoken"), Path(__file__).resolve())


def _manager(data_root=None):
    from open_agent_world.plugin_api import PackEnvironmentManager
    return PackEnvironmentManager(Path(data_root) if data_root is not None else None)


def environment_root(data_root: Path | None = None) -> Path:
    return _manager(data_root).environment_root(environment_spec())


def legacy_environment_root() -> Path:
    """Read-only compatibility with verified pre-migration desktop/source installs."""
    identity = b"oaw-matcreator-dependencies-v2\n" + REQUIREMENTS.read_bytes()
    identity += f"{sys.implementation.cache_tag}:{sysconfig.get_platform()}".encode()
    return Path(sys.prefix) / "oaw-runtimes" / "matcreator" / hashlib.sha256(identity).hexdigest()[:20]


def bundled_environment_root() -> Path:
    return _manager(Path(sys.prefix) / "oaw-runtime-bundle").environment_root(environment_spec())


def _read_legacy(root: Path) -> tuple[str, Path]:
    receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
    if receipt["format"] != 2:
        raise ValueError("Unknown legacy runtime receipt")
    relative = receipt["python"]
    executable = Path(sys.executable) if relative == "host" else root / relative
    if relative != "host" and (Path(relative).is_absolute() or not executable.resolve().is_relative_to(root.resolve())):
        raise ValueError("Runtime interpreter escapes installation")
    if not executable.is_file() or not all((root / "packages" / module / "__init__.py").is_file()
            for module in ("google/adk", "litellm", "json_repair")):
        raise ValueError("Incomplete legacy runtime")
    return str(executable), root


def read_environment(root: Path | None = None, *, data_root: Path | None = None) -> tuple[str, Path]:
    manager, spec = _manager(data_root), environment_spec()
    expected = root or manager.environment_root(spec)
    candidates = (root,) if root is not None else (expected, bundled_environment_root(), legacy_environment_root())
    for candidate in candidates:
        try:
            if candidate == legacy_environment_root():
                return _read_legacy(candidate)
            handle = manager.read_python(spec, root=candidate)
            return handle.python, handle.root
        except (RuntimeError, OSError, ValueError, KeyError, TypeError):
            continue
    raise RuntimeError(f"{REPAIR} Backend Python: {sys.executable}. Expected runtime: {expected}.")


def activate(root: Path) -> None:
    # Called only in the isolated worker or install-time verifier, never the host.
    import site
    root = root.resolve()
    # Deep dependency imports and tiktoken's atomic cache writes can exceed
    # MAX_PATH without Windows' system-wide long-path setting.
    if os.name == "nt" and not str(root).startswith("\\\\?\\"):
        path = str(root)
        root = Path("\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path)
    site.addsitedir(str(root / "packages"))
    # LiteLLM owns a bundled cache and otherwise overrides TIKTOKEN_CACHE_DIR.
    cache = str(root / "packages" / "litellm" / "litellm_core_utils" / "tokenizers")
    os.environ["TIKTOKEN_CACHE_DIR"] = cache
    os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = cache
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


def activate_engine() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("MatCreator requires Python 3.12+. " + REPAIR)
    if not (ENGINE / "agent.py").is_file():
        raise RuntimeError("The MatCreator Pack is missing its bundled engine.")
    sys.path.insert(0, str(PACKAGE.parent))
    import importlib.util
    spec = importlib.util.find_spec("oaw_matcreator_engine")
    if spec is None or Path(spec.origin).resolve() != (ENGINE / "__init__.py").resolve():
        raise RuntimeError("MatCreator must load the engine shipped in this Pack.")


def install(*, data_root: Path | None = None) -> None:
    # Build roots are explicit and carry the same relocatable receipt as user
    # profiles. Ordinary setup never writes under the backend interpreter.
    build_root = os.environ.get("OPEN_AGENT_WORLD_RUNTIME_BUILD_ROOT")
    manager = _manager(data_root if data_root is not None else build_root)
    manager.prepare_python(environment_spec(), seed_roots=(bundled_environment_root(), legacy_environment_root()))
    print("MatCreator native runtime is ready.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        activate(Path(sys.argv[2]))
        activate_engine()
        import litellm  # noqa: F401
        from google.adk.runners import Runner  # noqa: F401
        import tiktoken
        tiktoken.get_encoding("cl100k_base")
        tiktoken.get_encoding("o200k_base")
    else:
        install()
