"""Explicit isolated browser-test app; production never loads this provider."""
from backend.config import Settings
from backend.main import create_app
from backend.tests.minister_runtime import runtime_services

settings = Settings.from_environment()
services = runtime_services(settings.data_root)
services.run_manager.default_provider().auto_resume = True
app = create_app(services.settings, services=services)
