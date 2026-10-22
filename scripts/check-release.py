"""Check committed app versions and exact-commit CI before desktop packaging."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("ci.yml", "docs.yml", "desktop-check.yml")
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.(0|[1-9]\d*))?")


def check_versions(root: Path = ROOT, tag: str = "") -> str:
    versions = {}
    for folder in ("frontend", "desktop"):
        for filename in ("package.json", "package-lock.json"):
            name = f"{folder}/{filename}"
            value = json.loads((root / name).read_text(encoding="utf-8"))
            versions[name] = value["version"]
            if filename == "package-lock.json":
                versions[name + ":root"] = value["packages"][""]["version"]
    for name, table in (("backend/pyproject.toml", "project"),
                        ("desktop/src-tauri/Cargo.toml", "package")):
        versions[name] = tomllib.loads((root / name).read_text(encoding="utf-8"))[table]["version"]
    backend_lock = tomllib.loads((root / "backend/uv.lock").read_text(encoding="utf-8"))
    versions["backend/uv.lock"] = next(item["version"] for item in backend_lock["package"]
                                      if item["name"] == "open-agent-world-backend")
    cargo = tomllib.loads((root / "desktop/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    package = tomllib.loads((root / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8"))["package"]["name"]
    versions["Cargo.lock:app"] = next(item["version"] for item in cargo["package"] if item["name"] == package)
    versions["tauri"] = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    module = ast.parse((root / "backend/__init__.py").read_text(encoding="utf-8"))
    versions["backend runtime"] = next(ast.literal_eval(node.value) for node in module.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets))
    version = versions["tauri"]
    if not SEMVER.fullmatch(version):
        raise ValueError("Use X.Y.Z or X.Y.Z-{alpha,beta,rc}.N for the application version")
    # Python's equivalent spelling is required for Pack compatibility and metadata.
    python_version = re.sub(r"-(alpha|beta|rc)\.", lambda m: {"alpha": "a", "beta": "b", "rc": "rc"}[m[1]], version)
    for name, value in versions.items():
        expected = python_version if name.startswith("backend") else version
        if value != expected:
            raise ValueError(f"{name}: {value!r} does not match application version {expected!r}")
    if tag and tag != "v" + version:
        raise ValueError(f"Tag {tag!r} must match committed application version v{version}")
    return version


def verified_run(runs: list[dict], workflow: str, sha: str) -> dict:
    # PR runs validate synthetic merge commits. Releases require a push or an
    # explicit validation of the actual source commit; never use an older green run.
    candidates = [r for r in runs if r["head_sha"] == sha
                  and r["event"] in ("push", "workflow_dispatch")
                  and r["path"] == f".github/workflows/{workflow}"]
    if not candidates:
        raise ValueError(f"{workflow}: no push/manual validation for {sha}")
    # A rerun keeps its original run ID. Compare attempt start times first so
    # rerunning an older record cannot hide a newly observed failure.
    latest = max(candidates, key=lambda r: (r.get("run_started_at", r.get("created_at", "")),
                                           r["id"], r.get("run_attempt", 1)))
    if latest["status"] != "completed" or latest["conclusion"] != "success":
        raise ValueError(f"{workflow}: latest run is not successful: {latest['html_url']}")
    return {key: latest[key] for key in ("id", "head_sha", "html_url", "run_attempt")}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_ci(version: str, tag: str) -> dict:
    sha = git("rev-parse", "HEAD")
    if tag:
        if git("rev-parse", f"refs/tags/{tag}^{{commit}}") != sha:
            raise ValueError("Checkout does not match the release tag")
        branch = "dev" if "-" in version else "main"
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"origin/{branch}"], cwd=ROOT, check=True)
    repository = os.environ["GITHUB_REPOSITORY"]
    checks = {}
    for workflow in WORKFLOWS:
        url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100"
        request = Request(url, headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                          "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=30) as response:
            checks[workflow] = verified_run(json.load(response)["workflow_runs"], workflow, sha)
    return {"version": version, "commit": sha, "tag": tag or None, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-ci", action="store_true")
    args = parser.parse_args()
    tag = os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
    version = check_versions(tag=tag)
    print(f"Committed application version: {version}")
    if args.verify_ci:
        evidence = verify_ci(version, tag)
        Path("release-validation.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as stream:
                stream.write(f"sha={evidence['commit']}\nversion={version}\nprerelease={'true' if '-' in version else 'false'}\n")


if __name__ == "__main__":
    main()
