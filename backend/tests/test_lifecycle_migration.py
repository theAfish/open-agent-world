import asyncio
import sqlite3
from backend.config import Settings
from backend.services import create_services
from backend.world.models import CardCreate
from backend.runs import RunStatus


def test_old_run_and_text_data_remain_usable_after_additive_migration(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings)
    async def seed():
        text = await services.create_card(CardCreate(type='text', content='Keep old bytes'))
        record = services.run_manager.store.create(agent_id='deleted-producer', runtime_provider_id='core.mock', caller_kind='user')
        services.run_manager.store.update_status(record.run_id, RunStatus.SUCCEEDED)
        return text.id, record.run_id
    text_id, run_id = asyncio.run(seed())
    services.close()
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('ALTER TABLE runs DROP COLUMN lifecycle_json')
        connection.execute('DROP TABLE artifact_references')
        connection.execute('DROP TABLE artifact_versions')
    reopened = create_services(settings)
    try:
        assert reopened.resources.read_text(text_id).content == 'Keep old bytes'
        assert reopened.run_manager.get_run(run_id).status == RunStatus.SUCCEEDED
        assert reopened.run_manager.get_run(run_id).agent_id == 'deleted-producer'
        assert reopened.resources.artifacts.all() == []
    finally:
        reopened.close()
