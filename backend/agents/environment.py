"""Environment policy for subprocesses launched by trusted runtime plugins."""
from collections.abc import Mapping
import os

# Deployment credentials, proxy/import overrides and future provider secrets are
# excluded by construction. This is not a sandbox for untrusted plugin code.
PROCESS_ESSENTIALS = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
    "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR",
})


def subprocess_environment(*, inherit: bool, source: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = os.environ if source is None else source
    return {key: value for key, value in environment.items()
            if inherit or key.upper() in PROCESS_ESSENTIALS}
