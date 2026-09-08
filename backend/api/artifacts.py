"""Desktop control-plane endpoints; Agent callers use scoped capabilities."""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from backend.api.dependencies import get_services
from backend.resources.artifact_models import ArtifactPublish, ArtifactMaterialize

router = APIRouter(tags=['artifacts'])


@router.get('/artifact-collections/{collection_id}/versions')
async def listing(collection_id: str, services=Depends(get_services)):
    return services.resources.artifacts.listing(services, collection_id)


@router.post('/artifact-collections/{collection_id}/versions')
async def publish(collection_id: str, request: ArtifactPublish, services=Depends(get_services)):
    return await services.resources.artifacts.publish(services, collection_id, request)


@router.get('/artifact-collections/{collection_id}/versions/{version_id}/preview')
async def preview(collection_id: str, version_id: str, path: str, services=Depends(get_services)):
    return await services.resources.artifacts.preview(services, collection_id, version_id, path)


@router.get('/artifact-collections/{collection_id}/versions/{version_id}/content')
async def content(collection_id: str, version_id: str, path: str, services=Depends(get_services)):
    store = services.resources.artifacts
    stream = store.read(services, collection_id, version_id, path)
    # Admit before sending headers, including missing file and access errors.
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = b''
    async def body():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()
    return StreamingResponse(body(), media_type='application/octet-stream',
        headers={'Content-Disposition': 'attachment', 'X-Content-Type-Options': 'nosniff'})


@router.post('/artifact-collections/{collection_id}/versions/{version_id}/materialize')
async def materialize(collection_id: str, version_id: str, request: ArtifactMaterialize, services=Depends(get_services)):
    return await services.resources.artifacts.materialize(services, collection_id, version_id, request)


@router.delete('/artifact-collections/{collection_id}/versions/{version_id}')
async def release(collection_id: str, version_id: str, services=Depends(get_services)):
    return await services.resources.artifacts.release(services, collection_id, version_id)


@router.delete('/artifact-collections/{collection_id}/references/{version_id}')
async def remove_reference(collection_id: str, version_id: str, services=Depends(get_services)):
    store = services.resources.artifacts
    store.authorize(services, collection_id, None, 'artifact.manage', version_id)
    with services.database.transaction(immediate=True) as db:
        db.execute('DELETE FROM artifact_references WHERE collection_id=? AND version_id=?', (collection_id, version_id))
    return {'removed': True, 'retained': store.get(version_id)['retention']['retained']}


@router.get('/artifacts/retained')
async def retained(services=Depends(get_services)):
    # The local user owns retention even if every display reference is removed.
    return services.resources.artifacts.all()


@router.put('/artifact-collections/{collection_id}/references/{version_id}')
async def add_reference(collection_id: str, version_id: str, services=Depends(get_services)):
    store = services.resources.artifacts
    store.authorize(services, collection_id, None, 'artifact.manage')
    record = store.get(version_id)
    with services.database.transaction(immediate=True) as db:
        db.execute('INSERT OR IGNORE INTO artifact_references VALUES (?,?)', (collection_id, version_id))
    return record
