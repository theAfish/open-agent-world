"""Agent access annotations on the existing Pydantic configuration schema.

Unannotated fields (including extra fields) are private to the host. These
annotations restrict automated access; they do not change desktop validation.
"""
from copy import deepcopy

from backend.errors import PermissionDeniedError


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
        protected = (any(metadata.get(key) is True for key in ("secret", "privileged"))
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
