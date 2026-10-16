"""Offline Docker smoke test using disposable containers and a dedicated named volume."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import uuid


IMAGE = "oaw-agentcore-probe:mount-probe-20261016"


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=60)
    if check and result.returncode:
        raise RuntimeError(f"Docker operation {args[0]} failed: {result.stderr.strip()}")
    return result


CLIENT = r'''
import json,os,time,urllib.request,urllib.error
headers={'X-OAW-Probe-Token':os.environ['OAW_PROBE_TOKEN'],'X-OAW-Session':'local-check'}
def request(path,method='GET',auth=True):
    req=urllib.request.Request('http://127.0.0.1:8080'+path,method=method,headers=headers if auth else {})
    try: response=urllib.request.urlopen(req,timeout=3)
    except urllib.error.HTTPError as exc: response=exc
    with response:return {'status':response.status,'body':json.load(response)}
for attempt in range(20):
    try:
        assert request('/health/live',auth=False)['status']==200
        break
    except OSError:time.sleep(.2)
else:raise RuntimeError('probe did not start')
print(json.dumps({'ready':request('/health/ready',auth=False),
    'anonymous':request('/probe/status',auth=False),
    'status':request('/probe/status'),
    'before':request('/probe/marker'),
    'write':request('/probe/marker','POST'),
    'retry':request('/probe/marker','POST')}))
'''


def main():
    suffix = uuid.uuid4().hex
    volume = "oaw-probe-check-" + suffix
    containers = []
    result = None
    try:
        docker("volume", "create", volume)
        docker("run", "--rm", "--network", "none", "--user", "0:0", "--read-only",
               "--mount", f"type=volume,src={volume},dst=/mnt/agenticfs/probe,volume-nocopy",
               "--entrypoint", "python", IMAGE, "-c",
               "import os; os.chown('/mnt/agenticfs/probe',10001,10001)")
        with tempfile.TemporaryDirectory(prefix="oaw-agentcore-probe-") as folder:
            env = Path(folder) / "probe.env"
            env.write_text(f"OAW_PROBE_TOKEN={secrets.token_urlsafe(48)}\nOAW_PROBE_RUN_ID={uuid.uuid4()}\n", encoding="utf-8")
            env.chmod(0o600)
            replies = []
            for index in range(3):
                name = f"oaw-probe-check-{suffix}-{index}"
                containers.append(name)
                mount = ["--mount", f"type=volume,src={volume},dst=/mnt/agenticfs/probe,volume-nocopy"] if index < 2 else []
                docker("run", "-d", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp",
                       "--env-file", str(env), *mount, IMAGE)
                replies.append(json.loads(docker("exec", name, "python", "-c", CLIENT).stdout))
                docker("rm", "-f", name)
                containers.remove(name)
            first, second, absent = replies
            for reply in replies:
                assert reply["anonymous"]["status"] == 401
                assert reply["status"]["body"]["uid"] == 10001
            assert first["ready"]["status"] == second["ready"]["status"] == 200
            assert first["before"]["status"] == 404
            assert first["write"]["status"] == 200, first["write"]
            assert first["write"]["body"]["created"] is True
            assert first["retry"]["body"]["created"] is False
            assert second["before"]["body"]["from_previous_boot"] is True
            assert second["status"]["body"]["boot_id"] != first["status"]["body"]["boot_id"]
            assert second["write"]["body"]["created"] is False
            assert second["write"]["body"]["marker"] == first["write"]["body"]["marker"]
            assert absent["ready"]["status"] == absent["write"]["status"] == 503
            assert absent["status"]["body"]["mounted"] is False
            result = {"local_docker_only": True, "image": IMAGE,
                      "image_id": docker("image", "inspect", "--format", "{{.Id}}", IMAGE).stdout.strip(),
                      "containers_recreated": 2, "nonroot_uid": 10001, "network": "none",
                      "unauthorized_requests_rejected": True, "marker_retained": True,
                      "repeat_write_preserved_marker": True, "missing_mount_rejected": True}
    finally:
        errors = []
        for name in containers:
            if docker("rm", "-f", name, check=False).returncode:
                errors.append(name)
        if docker("volume", "rm", volume, check=False).returncode:
            errors.append(volume)
        if errors:
            raise RuntimeError("Reconcile remaining smoke-test resources: " + ", ".join(errors))
    result["cleanup_complete"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
