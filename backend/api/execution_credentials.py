"""Host UI only: Agents receive no credential-writing capability."""
from fastapi import APIRouter, Depends, Request
from backend.api.dependencies import get_services
from backend.errors import ResourceValidationError, RevisionConflictError
from backend.execution_config import EnvironmentProfile, SecretRequirement
from backend.node_documents import read_document

router = APIRouter(prefix="/nodes", tags=["execution-credentials"])


def requirements(services, node_id):
    node = services.world.get_card(node_id)
    if "core.environment" not in services.plugins.node_type(node.type).traits:
        raise ResourceValidationError("Credential bindings require an Environment Profile")
    snapshot = read_document(services, node_id)
    profile = EnvironmentProfile.model_validate(snapshot["value"])
    return snapshot, {value.secret_ref for value in profile.variables.values() if isinstance(value, SecretRequirement)}


@router.get("/{node_id}/credentials")
async def status(node_id: str, services=Depends(get_services)):
    async with services._node_mutation(read_only=True):
        _, references = requirements(services, node_id)
        return {reference: services.execution_credentials.configured(node_id, reference) for reference in sorted(references)}


@router.put("/{node_id}/credentials/{reference}")
async def bind(node_id: str, reference: str, request: Request, services=Depends(get_services)):
    # Parse manually so validation errors can never echo a secret request body.
    try:
        payload = await request.json()
    except ValueError:
        raise ResourceValidationError("Invalid credential request") from None
    if not isinstance(payload, dict) or set(payload) != {"value", "expected_revision"}:
        raise ResourceValidationError("Supply value and expected_revision")
    value = payload["value"]
    if value is not None and (not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > 16000):
        raise ResourceValidationError("Credential must be a nonempty NUL-free string up to 16 KB, or null to unbind")
    async with services._node_mutation():
        snapshot, references = requirements(services, node_id)
        if payload["expected_revision"] != snapshot["revision"]:
            raise RevisionConflictError("Profile changed; reload before binding")
        if reference not in references:
            raise ResourceValidationError("Save this credential requirement in the profile first")
        if value is None:
            services.execution_credentials.unbind(node_id, reference)
        else:
            services.execution_credentials.bind(node_id, reference, value)
        return {"configured": value is not None}
