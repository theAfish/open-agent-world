from fastapi import APIRouter, Depends
from fastapi.responses import Response

from backend.api.dependencies import get_services
from backend.errors import ResourceValidationError
from backend.packs.content import export_archive
from backend.packs.factory import add_item, factory_node, linked_design, prepare_factory_export, print_card
from backend.packs.factory_models import BasketItem, FunctionDesign, Model, PrintedCard, Scalar
from pydantic import Field
from backend.sandbox.python_runtime import finish_thread

router = APIRouter(prefix="/factory", tags=["card-factory"])


@router.get("/{node_id}/context")
async def context(node_id: str, services=Depends(get_services)):
    node = services.world.get_card(node_id)
    kind = node.type.removeprefix("oaw.factory.")
    if kind not in {"printer", "packer", "function"}:
        raise ResourceValidationError("请选择印刷器、打包器或功能设计器")
    factory_node(services, node_id, kind)
    inputs, issues = {}, []
    for input_kind in {"printer": ["face", "function"], "packer": ["pack"], "function": ["face"]}[kind]:
        try:
            linked = linked_design(services, node_id, input_kind)
            inputs[input_kind] = {"id": linked.id, "name": linked.name, "config": linked.config}
        except ValueError as exc:
            issues.append(str(exc))
    return {"inputs": inputs, "issues": issues}


@router.post("/{node_id}/print", status_code=201)
async def print_(node_id: str, services=Depends(get_services)):
    try:
        return await print_card(services, node_id)
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.post("/{node_id}/items")
async def add(node_id: str, request: BasketItem, services=Depends(get_services)):
    try:
        return await add_item(services, node_id, request)
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.post("/{node_id}/inspect")
async def inspect(node_id: str, services=Depends(get_services)):
    try:
        result, _ = await prepare_factory_export(services, node_id)
        return result
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.post("/{node_id}/export")
async def export(node_id: str, services=Depends(get_services)):
    try:
        result, files = await prepare_factory_export(services, node_id)
        if not result["can_export"]:
            raise ValueError("请先解决发布检查中的错误")
        data = await finish_thread(export_archive, files)
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    manifest = result["manifest"]
    return Response(data, media_type="application/vnd.oaw.pack", headers={
        "Content-Disposition": f'attachment; filename="{manifest["id"]}-{manifest["version"]}.oawpack"',
        "Cache-Control": "no-store"})


class RunRequest(Model):
    values: dict[str, Scalar] = Field(default_factory=dict, max_length=20)


class PreviewRequest(RunRequest):
    design: FunctionDesign


@router.post("/{node_id}/try")
async def try_function(node_id: str, request: PreviewRequest, services=Depends(get_services)):
    factory_node(services, node_id, "function")
    try:
        return {"result": request.design.run(request.values)}
    except (ValueError, OverflowError) as exc:
        raise ResourceValidationError(str(exc)) from exc


@router.post("/{node_id}/run")
async def run(node_id: str, request: RunRequest, services=Depends(get_services)):
    node = services.world.get_card(node_id)
    definition = services.plugins.node_type(node.type)
    if "ui.factory-card.v1" not in definition.traits:
        raise ResourceValidationError("此卡牌不支持表单功能")
    try:
        return {"result": PrintedCard.model_validate(node.config).function.run(request.values)}
    except (ValueError, OverflowError) as exc:
        raise ResourceValidationError(str(exc)) from exc
