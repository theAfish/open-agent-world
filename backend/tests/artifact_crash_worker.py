"""Child backend for destructive process-restart tests; no production failpoints."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

from backend.config import Settings
from backend.plugins.loader import load_plugin_registry
from backend.resources.artifact_models import ArtifactPublish
from backend.sandbox.windows import WindowsSandboxBackend
from backend.services import create_services
from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
from backend.world.models import CardCreate, CardPatch
from backend.plugins.summoning import SummoningAction


def services_for(root):
    settings = replace(Settings.for_data_root(root), agent_runtime='core.mock')
    return create_services(settings, plugins=load_plugin_registry(),
        sandbox_backend=WindowsSandboxBackend(root, native_api=FakeWindowsNativeApi()))


async def main(root, phase):
    services = services_for(root)
    await services.startup()
    collection = await services.create_card(CardCreate(type='core.artifact-collection'))
    sandbox = await services.create_card(CardCreate(type='sandbox'))
    info = await services.sandbox_backend.get(sandbox.id)
    (info.workspace / 'output.bin').write_bytes(b'\0durable\xff' * 10000)
    store = services.resources.artifacts
    request = ArtifactPublish(sandbox_id=sandbox.id, paths=['output.bin'], finalized=True, name='Result', request_key='original')
    def checkpoint():
        (root / 'checkpoint.json').write_text(json.dumps({'collection': collection.id, 'sandbox': sandbox.id,
            'versions': store.all(), 'instances': services.summoning.records()}), encoding='utf-8')
    if phase in {'publication', 'publication_rename'}:
        original_save = store.save
        def save(record):
            if phase == 'publication_rename' and record['state'] == 'ready':
                checkpoint()
                import threading
                threading.Event().wait(30)
            original_save(record)
            if phase == 'publication' and record['state'] == 'staging' and record['manifest']:
                checkpoint()
                # Process is killed by the parent while the backend is inside publication.
                import threading
                threading.Event().wait(30)
        store.save = save
    version = await store.publish(services, collection.id, request)
    if phase == 'deletion':
        async def pause_remove(record):
            checkpoint()
            await asyncio.Event().wait()
        store.remove_bytes = pause_remove
        await store.release(services, collection.id, version['version_id'])
    if phase == 'reclamation':
        box = await services.create_card(CardCreate(type='oaw.barracks'))
        agent = await services.create_card(CardCreate(type='agent', parent_id=box.id))
        await services.update_card(sandbox.id, CardPatch(equipment={'owner_id': agent.id, 'relationship': None}))
        instance = await services.summoning.action(box.id, SummoningAction(action='summon', agent_id=agent.id, prompt='task'))
        await services.run_manager.wait_execution(instance['run_id'])
        original_destroy = services.sandbox_backend.destroy
        async def pause_destroy(node_id):
            checkpoint()
            await asyncio.Event().wait()
            await original_destroy(node_id)
        services.sandbox_backend.destroy = pause_destroy
        await services.summoning.teardown(services.summoning.records()[0], 'reclaim')
    if phase == 'ready':
        checkpoint()
        await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
