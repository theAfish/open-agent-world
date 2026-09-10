"""Host UI only: Agents receive no credential-writing capability."""
from fastapi import APIRouter, Depends, Request
from backend.api.dependencies import get_services
from backend.errors import ResourceValidationError, RevisionConflictError
from backend.execution_config import EnvironmentProfile, SecretRequirement
from backend.node_documents import read_document

router = APIRouter(prefix="/nodes", tags=["execution-credentials"])


@router.put("/{node_id}/environment")
async def save_environment(node_id: str, request: Request, services=Depends(get_services)):
    """Save portable configuration and private bindings in one host-only operation."""
    from backend.node_documents import write_document
    try:
        payload = await request.json()
    except ValueError:
        raise ResourceValidationError("Invalid environment request") from None
    if not isinstance(payload, dict) or set(payload) != {"value", "secrets", "expected_revision"}:
        raise ResourceValidationError("Supply value, secrets and expected_revision")
    secrets = payload["secrets"]
    if not isinstance(secrets, dict) or any(
        not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > 16000
        for value in secrets.values()
    ):
        raise ResourceValidationError("Secrets must be nonempty NUL-free strings up to 16 KB")
    if type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0:
        raise ResourceValidationError("Supply expected_revision")
    try:
        profile = EnvironmentProfile.model_validate(payload["value"])
    except ValueError:
        raise ResourceValidationError("Invalid environment configuration") from None
    references = {v.secret_ref for v in profile.variables.values() if isinstance(v, SecretRequirement)}
    if set(secrets) - references:
        raise ResourceValidationError("Secret does not belong to this environment")
    async with services._node_mutation():
        snapshot, _ = requirements(services, node_id)
        services.node_execution.assert_editable(node_id)
        if snapshot["revision"] != payload["expected_revision"]:
            raise RevisionConflictError("Environment changed; reload before saving")
        for name, value in profile.variables.items():
            if (isinstance(value, SecretRequirement) and value.secret_ref not in secrets
                    and not services.execution_credentials.configured(node_id, value.secret_ref)):
                raise ResourceValidationError(f"Enter a secret for {name}, or remove the unused variable")
        with services.execution_credentials.database.transaction(immediate=True):
            for reference, value in secrets.items():
                services.execution_credentials.bind(node_id, reference, value)
            return write_document(services, node_id, profile.model_dump(mode="json"), snapshot["revision"])


def requirements(services, node_id):
    node = services.world.get_card(node_id)
    if not {"core.environment", "core.sandbox"} & services.plugins.node_type(node.type).traits:
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
