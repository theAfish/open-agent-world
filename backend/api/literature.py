from fastapi import APIRouter, Depends
from backend.api.dependencies import get_services
from backend.node_documents import DocumentActionRequest
from backend.services import ApplicationServices
from backend.literature_service import service
from fastapi.responses import Response
from urllib.parse import quote

router = APIRouter(prefix="/literature", tags=["literature"])


@router.get("/papers/intake-options")
async def paper_intake_options(doi: str | None = None, services: ApplicationServices = Depends(get_services)):
    from backend.library_intake import intake_options
    async with services._node_mutation(read_only=True):
        return intake_options(services, doi)


@router.post("/papers/{paper_id}/attach_pdf")
async def paper_attach_pdf(paper_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.library_intake import attach_pdf
    async with services._node_mutation():
        return await attach_pdf(services, paper_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.post("/papers/{paper_id}/model_figure")
async def paper_model_figure(paper_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.literature_modeling import model_figure
    async with services._node_mutation():
        return await model_figure(services, paper_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.post("/scopes/{scope_id}/collect_reference")
async def collect_paper_reference(scope_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.library_intake import collect_reference
    async with services._node_mutation():
        service(services).authorize(scope_id, "collect_reference", None)
        return await collect_reference(services, scope_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.post("/scopes/{scope_id}/link_paper")
async def link_existing_paper(scope_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.library_intake import link_paper
    async with services._node_mutation():
        service(services).authorize(scope_id, "link_paper", None)
        return await link_paper(services, scope_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.get("/scopes/{scope_id}/intake")
async def paper_intake(scope_id: str, services: ApplicationServices = Depends(get_services)):
    from backend.literature_intake import intake_view
    async with services._node_mutation(read_only=True):
        return intake_view(service(services), scope_id)


@router.post("/scopes/{scope_id}/review_paper")
async def review_paper_intake(scope_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.literature_intake import review_paper
    return await review_paper(service(services), scope_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.post("/scopes/{scope_id}/repair_preview")
async def preview_paper_repair(scope_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.literature_intake import repair_preview
    return await repair_preview(service(services), scope_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.post("/scopes/{scope_id}/repair_apply")
async def apply_paper_repair(scope_id: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    from backend.literature_intake import repair_apply
    return await repair_apply(service(services), scope_id, {**request.arguments, "expected_revision": request.expected_revision})


@router.get("/scopes/{scope_id}/methods/{method_id}/export")
async def export_method(scope_id: str,method_id: str,services:ApplicationServices=Depends(get_services)):
    from backend.literature_records import method_package
    from open_agent_world.skill_packages import export_plugin
    async with services._node_mutation(read_only=True):
        service(services).authorize(scope_id,"scope",None)
        result=export_plugin(method_package(services,scope_id,method_id).model_dump(mode="json"))
    return Response(result.content,media_type=result.media_type,headers={"Content-Disposition":f"attachment; filename*=UTF-8''{quote(result.filename,safe='')}"})


@router.get("/scopes/{scope_id}")
async def scope(scope_id: str, services: ApplicationServices = Depends(get_services)):
    return await service(services).invoke(scope_id,"scope",{})


@router.post("/scopes/{scope_id}/{operation}")
async def action(scope_id: str, operation: str, request: DocumentActionRequest,
                 services: ApplicationServices = Depends(get_services)):
    return await service(services).invoke(scope_id,operation,{**request.arguments,"expected_revision":request.expected_revision})
