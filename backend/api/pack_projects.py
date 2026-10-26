"""Host-authorized editing of shared Pack source files, never code execution."""
from functools import partial

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services, get_request_context
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.packs.factory_models import FunctionDesign, Scalar
from backend.packs.projects import PackProjects, read_file, write_file
from backend.request_context import ActorKind
from backend.sandbox.python_runtime import finish_thread


def host_authoring(context=Depends(get_request_context)):
    # Agent tools retain their existing Sandbox/file grants. A prompt cannot turn
    # a project path into a host filesystem capability.
    if context.actor.kind not in {ActorKind.USER, ActorKind.HOST_CREDENTIAL}:
        raise PermissionDeniedError('Pack project roots are selected by the host user')


router = APIRouter(prefix='/projects', tags=['pack-projects'], dependencies=[Depends(host_authoring)])


def projects(services=Depends(get_services)):
    return PackProjects(services.settings.data_root, services.database)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OpenProject(Model):
    path: str = Field(min_length=1, max_length=4096)


class CreateProject(Model):
    name: str = Field(min_length=1, max_length=120)
    distribution_id: str | None = Field(default=None, max_length=128)
    directory: str | None = Field(default=None, max_length=4096)
    design: dict | None = None


class FileWrite(Model):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=2 * 1024 * 1024)
    expected_sha256: str | None = Field(pattern=r'^[a-f0-9]{64}$')


class CheckProject(Model):
    expected_revision: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class BuildProject(CheckProject):
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    backend_wheel: str | None = Field(default=None, max_length=240)
    frontend_dir: str | None = Field(default=None, max_length=240)
    dependency_wheels: str | None = Field(default=None, max_length=240)


class PreviewProject(Model):
    design: FunctionDesign
    values: dict[str, Scalar] = Field(default_factory=dict, max_length=20)


class SaveDesign(Model):
    card_id: str = Field(min_length=1, max_length=128)
    design: dict
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class AddCard(Model):
    name: str = Field(min_length=1, max_length=120)
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    design: dict | None = None


class SetVersion(Model):
    version: str = Field(min_length=1, max_length=80)
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


async def checked(function, *args, **kwargs):
    try:
        return await finish_thread(partial(function, *args, **kwargs))
    except (ValueError, OSError, KeyError) as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.get('')
async def list_projects(store=Depends(projects)):
    return await checked(store.list)


@router.post('/open')
async def open_project(body: OpenProject, store=Depends(projects)):
    return await checked(store.open, body.path)


@router.post('/create', status_code=201)
async def create_project(body: CreateProject, store=Depends(projects)):
    return await checked(store.create, **body.model_dump())


@router.get('/{project_id}')
async def inspect_project(project_id: str, store=Depends(projects)):
    return await checked(store.inspect, project_id)


@router.get('/{project_id}/file')
async def get_file(project_id: str, path: str, store=Depends(projects)):
    return await checked(lambda: read_file(store.root(project_id), path))


@router.put('/{project_id}/file')
async def save_file(project_id: str, body: FileWrite, store=Depends(projects)):
    return await checked(lambda: write_file(store.root(project_id), body.path, body.content, body.expected_sha256))


@router.post('/{project_id}/check')
async def check_project(project_id: str, body: CheckProject, store=Depends(projects)):
    return await checked(store.check, project_id, body.expected_revision)


@router.post('/{project_id}/design')
async def save_design(project_id: str, body: SaveDesign, store=Depends(projects)):
    return await checked(store.save_design, project_id, **body.model_dump())


@router.post('/{project_id}/cards', status_code=201)
async def add_card(project_id: str, body: AddCard, store=Depends(projects)):
    return await checked(store.add_card, project_id, **body.model_dump())


@router.post('/{project_id}/version')
async def set_version(project_id: str, body: SetVersion, store=Depends(projects)):
    return await checked(store.set_version, project_id, **body.model_dump())


@router.post('/{project_id}/preview')
async def preview_project(project_id: str, body: PreviewProject, store=Depends(projects)):
    await checked(store.root, project_id)
    try:
        return {'result': body.design.run(body.values), 'level': 'declarative',
                'note': 'Bounded function preview; build and installed runtime checks are separate.'}
    except (ValueError, OverflowError) as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.post('/{project_id}/build')
async def build_project(project_id: str, body: BuildProject, store=Depends(projects)):
    data, filename = await checked(store.build, project_id, **body.model_dump())
    return Response(data, media_type='application/vnd.oaw.pack', headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})
