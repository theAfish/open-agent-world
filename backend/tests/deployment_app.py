"""Isolated real runtime server for the deployment browser acceptance suite."""
from pathlib import Path
from dataclasses import replace
import tempfile

import uvicorn

from backend.deploy import create_deployment
from backend.main import create_app
from backend.tests.test_deployments import PASSWORD, prepare_source, settings


def main():
    repository = Path(__file__).resolve().parents[2]
    temporary = repository / ".tmp"
    temporary.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="deployment-browser-", dir=temporary))
    source, runtime = directory / "source", directory / "runtime"
    plugin_directories = (repository / "examples/deployed-workspace/plugins",)
    records = prepare_source(source, plugin_directories=plugin_directories)
    create_deployment(source, runtime, records["release"]["id"], password=PASSWORD)
    app = create_app(replace(settings(runtime), plugin_directories=plugin_directories), frontend_directory=repository / "frontend/dist")
    uvicorn.run(app, host="127.0.0.1", port=5183, proxy_headers=False)


if __name__ == "__main__":
    main()
