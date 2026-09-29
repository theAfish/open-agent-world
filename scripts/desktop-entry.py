"""Installed entry point: run with the bundled interpreter's -I flag."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

if "--backup-for-update" in sys.argv:
    from backend.config import Settings
    from backend.desktop_backup import backup_for_update
    print(backup_for_update(Settings.from_environment()), flush=True)
elif "--self-test" in sys.argv:
    from contextlib import closing
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    import google.adk
    import litellm
    from backend.config import Settings
    from backend.persistence.database import Database
    from backend.security.llm_settings import LlmSettingsStore
    from backend.services import create_services
    from backend.sandbox.python_runtime import SharedPythonRuntime
    with tempfile.TemporaryDirectory(prefix="oaw-package-test-") as temporary:
        temporary_root = Path(temporary).resolve()
        settings = Settings.for_data_root(temporary_root / 'data')
        services = create_services(settings)
        try:
            catalog = services.plugins.catalog()
            assert len(catalog.plugins) > 1, "Bundled plugins are missing"
            assert (root / "frontend/dist/index.html").is_file()
            sandbox_status = "unsupported" if sys.platform == "darwin" else "ready"
            if sys.platform != "darwin":
                runtime = SharedPythonRuntime(Path(temporary))
                runtime.prepare_sync()
                assert runtime.python.is_file(), "Sandbox Python could not be prepared"
            services.llm_settings.save(base_url='https://example.invalid', api_key='package-test-only')
            (settings.data_root / 'backup-test.txt').write_text('saved document')
        finally:
            services.close()
        # Exercise the actual installed backup entry point with synthetic data.
        # This also checks native credential encryption (including Windows DPAPI).
        backup = Path(subprocess.check_output(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--backup-for-update'],
            env={**os.environ, 'OPEN_AGENT_WORLD_DATA_ROOT': str(settings.data_root)}, text=True,
        ).strip())
        assert backup.parent == temporary_root, 'Backup escaped the self-test directory'
        receipt = json.loads((backup / '.oaw-update-backup.json').read_text())
        assert receipt['source'] == str(settings.data_root)
        settings.data_root.rename(temporary_root / 'retained-original')
        shutil.copytree(backup, settings.data_root)
        with closing(Database(settings.database_path)) as database:
            assert LlmSettingsStore(database, settings.data_root).read().api_key == 'package-test-only'
        assert (settings.data_root / 'backup-test.txt').read_text() == 'saved document'
        print(json.dumps({"status": "ok", "plugins": [plugin.id for plugin in catalog.plugins],
                          "sandbox_python": sandbox_status, "backup_restore": "ok"}))
else:
    # Installed applications cannot opt into the development reset server.
    if "--mode" in sys.argv and sys.argv[sys.argv.index("--mode") + 1] != "production":
        raise SystemExit("The installed application only supports production mode")
    from backend.launcher import main
    main()
