"""Agent access annotations on the existing Pydantic configuration schema.

Unannotated fields (including extra fields) are private to the host. These
annotations restrict automated access; they do not change desktop validation.
"""
from copy import deepcopy
import re

from backend.errors import PermissionDeniedError


def _secret_name(name):
    normalized = re.sub(r'[^a-z0-9]', '', name.lower())
    return normalized.endswith(('password', 'passwd', 'apikey', 'accesstoken', 'refreshtoken', 'privatekey', 'clientsecret', 'secret', 'token'))


def agent_config_policy(model):
    result = {}
    schema = model.model_json_schema()

    def scalar(value):
        if value.get("writeOnly") is True or value.get("format") == "password":
            return False
        if "$ref" in value:
            return scalar(schema.get("$defs", {}).get(value["$ref"].rsplit("/", 1)[-1], {}))
        if "anyOf" in value:
            return all(scalar(part) for part in value["anyOf"])
        return value.get("type") in {"string", "number", "integer", "boolean", "null"}

    for name, field in model.model_fields.items():
        metadata = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        # V1 opts in scalar fields only. Nested documents retain their existing
        # capability/action contracts; a parent annotation cannot expose a secret child.
        field_schema = schema.get("properties", {}).get(name, {})
        protected = (_secret_name(name) or any(metadata.get(key) is True for key in ("secret", "privileged"))
                     or field_schema.get("writeOnly") is True or field_schema.get("format") == "password"
                     or not scalar(field_schema))
        result[name] = {
            "agentReadable": metadata.get("agentReadable") is True and not protected,
            "agentWritable": metadata.get("agentWritable") is True and not protected
                and metadata.get("immutable") is not True and not field.frozen,
        }
    return result


def readable_config(model, config):
    policy = agent_config_policy(model)
    return {key: deepcopy(value) for key, value in config.items()
            if policy.get(key, {}).get("agentReadable", False)}


def validate_agent_config(model, patch):
    policy = agent_config_policy(model)
    if any(not policy.get(key, {}).get("agentWritable", False) for key in patch):
        # Never echo submitted values, including rejected secret values.
        raise PermissionDeniedError("Configuration contains a field unavailable for automated writes")


def config_write_risk(model, patch):
    """Local administration may propose sensitive writes, never secrets/internal state.

    This does not broaden ordinary agent access. Only a host-reviewed canvas
    facade uses this policy. Unknown plugin fields remain confirmable, whereas
    undeclared extras and explicitly read-only fields remain unavailable.
    """
    schema = model.model_json_schema()
    policy = agent_config_policy(model)

    def protected(value, seen=frozenset()):
        if any(value.get(key) is True for key in ("secret", "writeOnly", "immutable", "readOnly")) or value.get("format") == "password":
            return True
        ref = value.get("$ref")
        if ref and ref not in seen:
            return protected(schema.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {}), seen | {ref})
        return any(protected(child, seen) for key in ("anyOf", "allOf", "oneOf") for child in value.get(key, [])) or any(
            _secret_name(key) or protected(child, seen) for key, child in value.get("properties", {}).items()) or (
            isinstance(value.get("items"), dict) and protected(value["items"], seen))

    risk = "ALLOW"
    for name in patch:
        field = model.model_fields.get(name)
        metadata = field.json_schema_extra if field and isinstance(field.json_schema_extra, dict) else {}
        definition = schema.get("properties", {}).get(name, {})
        if (field is None or field.frozen or _secret_name(name) or metadata.get("agentWritable") is False
                or protected(definition) or name in {"status", "minister_chat"}):
            raise PermissionDeniedError("Secrets, internal state and read-only configuration cannot be changed by Minister")
        # A free-form map can conceal secrets; it needs its own declared field
        # contract rather than a parent annotation that exposes arbitrary data.
        if definition.get("type") == "object" and not definition.get("properties"):
            raise PermissionDeniedError("This configuration document needs its dedicated settings operation")
        if not policy[name]["agentWritable"]:
            risk = "CONFIRM"
    return risk
