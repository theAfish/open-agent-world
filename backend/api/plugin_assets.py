from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from backend.api.dependencies import get_services
from backend.services import ApplicationServices

router = APIRouter(tags=["plugins"])


@router.get("/plugins/{plugin_id}/assets/{asset_id}")
def get_plugin_asset(plugin_id: str, asset_id: str, services: ApplicationServices = Depends(get_services)) -> Response:
    try:
        asset = services.plugins.asset(plugin_id, asset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plugin asset not found") from None
    return Response(asset.content, media_type=asset.media_type, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "no-cache",
    })
