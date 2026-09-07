"""A local-only consumer of explicitly selected execution configuration."""
import json
import os

region = os.environ.get("DEMO_REGION", "unset")
target = json.loads(os.environ.get("OAW_TARGET_CONFIG_JSON", "{}"))
print(json.dumps({
    "region": region,
    "provider_id": target.get("provider_id", "unset"),
    "configuration_field_count": len(target.get("config", {})),
}, sort_keys=True))
