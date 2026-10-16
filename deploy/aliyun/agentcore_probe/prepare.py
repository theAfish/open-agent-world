"""Prepare a private AgentCore request; never create cloud resources or push images."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import uuid


def build_request(config: dict) -> dict:
    required = ("acr_instance_id", "acr_namespace", "acr_repository", "image_tag", "role_arn",
                "access_point_domain", "mount_subdirectory", "probe_token", "probe_run_id")
    for key in required:
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"Missing deployment setting: {key}")
    path = config["mount_subdirectory"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path):
        raise ValueError("mount_subdirectory must be an existing relative child directory")
    if config.get("mount_subdirectory_verified") is not True:
        raise ValueError("Verify the directory exists and UID 10001 can write before deployment")
    if not re.fullmatch(r"ap-[a-z0-9]+\.[a-z0-9-]+\.cn-hangzhou\.nas\.aliyuncs\.com", config["access_point_domain"]):
        raise ValueError("Expected a Hangzhou Access Point DomainName without a protocol or path")
    if not config["acr_instance_id"].startswith("cri-"):
        raise ValueError("Expected an enterprise ACR instance ID")
    if not re.fullmatch(r"acs:ram::[0-9]+:role/[A-Za-z0-9_-]+", config["role_arn"]):
        raise ValueError("Invalid execution role ARN")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", config["probe_token"]):
        raise ValueError("Expected a random URL-safe probe token of at least 32 characters")
    run_id = str(uuid.UUID(config["probe_run_id"]))
    return {
        "name": "oaw-agentcore-mount-probe",
        "description": "OAW HTTP and AgenticFS mount probe; no user data or model credentials",
        "artifact": {"port": 8080, "container": {
            "type": "acr", "instanceId": config["acr_instance_id"],
            "namespace": config["acr_namespace"], "repo": config["acr_repository"],
            "tag": config["image_tag"], "entrypoint": "python /app/probe.py"}},
        "roleArn": config["role_arn"],
        "environment": {"variables": [
            {"name": "OAW_PROBE_TOKEN", "value": config["probe_token"]},
            {"name": "OAW_PROBE_RUN_ID", "value": run_id},
            {"name": "OAW_PROBE_MOUNT", "value": "/mnt/agenticfs/probe"}]},
        "agenticFsMounts": [{"server": config["access_point_domain"], "path": path,
                             "mountPath": "/mnt/agenticfs/probe", "readOnly": False}],
        "network": {"accessInternet": {"enabled": False}, "accessVpc": {"enabled": True}},
        "runtime": {"compute": {"computeClass": "INTERACTIVE"},
                    "hpa": {"enabled": False, "minSandboxCount": 1, "maxSandboxCount": 1,
                            "maxConcurrentSessionsPerSandbox": 1, "sessionTtlSeconds": 300},
                    "sessionPolicy": {"type": "ISOLATED_HEADER_FIELD", "headerName": "X-OAW-Session"}},
        "protocol": [{"type": "custom", "paths": [{"name": "/probe/status"}, {"name": "/probe/marker"}]}],
        "healthCheck": {"path": "/health/ready", "port": 8080, "intervalSeconds": 10,
                        "timeoutSeconds": 3, "healthyThreshold": 1, "unhealthyThreshold": 3},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Private JSON settings outside the checkout")
    parser.add_argument("output", type=Path, help="New private request JSON; contains the probe token")
    args = parser.parse_args()
    request = build_request(json.loads(args.config.read_text(encoding="utf-8")))
    # Do not overwrite a previously prepared deployment or print its credentials.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(request, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print("Request prepared; no cloud deployment submitted.")


if __name__ == "__main__":
    main()
