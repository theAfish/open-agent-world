"""Full published MatCreator UI; test Sandbox OS adapter, no model/cloud calls."""
from pathlib import Path
import tempfile

import uvicorn

from backend.deploy import create_deployment
from backend.main import create_app
from backend.sandbox.windows import WindowsSandboxBackend
from backend.services import create_services
from backend.tests.test_deployments import PASSWORD, settings
from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
from backend.tests.test_workspace_publication import STRUCTURE, prepare_research_source


def main():
    repository = Path(__file__).resolve().parents[2]
    temporary = repository / ".tmp"
    temporary.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="research-deployment-browser-", dir=temporary))
    source, target = directory / "source", directory / "runtime"
    records = prepare_research_source(source, offline_sandbox=True)
    create_deployment(source, target, records["release"]["id"], password=PASSWORD)
    class PreviewSandbox(WindowsSandboxBackend):
        async def create(self, sandbox_id):
            result = await super().create(sandbox_id)
            (target / "sandboxes" / sandbox_id / "workspace" / "crystal.xyz").write_bytes(STRUCTURE)
            return result

    def services_factory(config):
        return create_services(config, sandbox_backend=PreviewSandbox(
            config.data_root, native_api=FakeWindowsNativeApi()))

    app = create_app(settings(target), services_factory=services_factory, frontend_directory=repository / "frontend/dist")
    uvicorn.run(app, host="127.0.0.1", port=5184, proxy_headers=False)


if __name__ == "__main__":
    main()
