"""Installed entry point: run with the bundled interpreter's -I flag."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

if "--self-test" in sys.argv:
    import json
    import tempfile
    import google.adk
    import litellm
    from backend.config import Settings
    from backend.services import create_services
    from backend.sandbox.python_runtime import SharedPythonRuntime
    with tempfile.TemporaryDirectory(prefix="oaw-package-test-") as temporary:
        settings = Settings.for_data_root(temporary)
        services = create_services(settings)
        try:
            catalog = services.plugins.catalog()
            assert len(catalog.plugins) > 1, "Bundled plugins are missing"
            assert (root / "frontend/dist/index.html").is_file()
            runtime = SharedPythonRuntime(Path(temporary))
            runtime.prepare_sync()
            assert runtime.python.is_file(), "Sandbox Python could not be prepared"
            print(json.dumps({"status": "ok", "plugins": [plugin.id for plugin in catalog.plugins], "sandbox_python": "ready"}))
        finally:
            services.close()
else:
    # Installed applications cannot opt into the development reset server.
    if "--mode" in sys.argv and sys.argv[sys.argv.index("--mode") + 1] != "production":
        raise SystemExit("The installed application only supports production mode")
    from backend.launcher import main
    main()
