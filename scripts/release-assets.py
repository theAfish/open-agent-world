"""Record and verify the three existing desktop build outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"windows-x64": "exe", "macos-arm64": "dmg", "macos-x86_64": "dmg"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(root: Path, platform: str, sha: str, version: str) -> None:
    assets = root / "release-assets"
    installer = assets / f"Open-Agent-World-{version}-{platform}.{PLATFORMS[platform]}"
    if not installer.is_file() or not installer.stat().st_size:
        raise ValueError(f"Missing or empty installer: {installer.name}")
    expected = f"{digest(installer)}  {installer.name}\n"
    if installer.with_suffix(installer.suffix + ".sha256").read_text() != expected:
        raise ValueError("Installer checksum does not match")
    files = {p.name: digest(p) for p in sorted(assets.iterdir())
             if p.is_file() and not p.name.startswith("provenance-")}
    (assets / f"provenance-{platform}.json").write_text(json.dumps(
        {"platform": platform, "commit": sha, "version": version, "files": files}, indent=2) + "\n", encoding="utf-8")


def assemble(root: Path, sha: str, version: str) -> None:
    assets = root / "release-assets"
    validation = json.loads((assets / "release-validation.json").read_text(encoding="utf-8"))
    if validation["commit"] != sha or validation["version"] != version or validation["tag"] not in (None, "v" + version):
        raise ValueError("Validation evidence does not match release checkout")
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(assets.glob("provenance-*.json"))]
    if len(records) != len(PLATFORMS) or {r["platform"] for r in records} != set(PLATFORMS):
        raise ValueError("Expected exactly one build from each desktop platform")
    for item in records:
        if item["commit"] != sha or item["version"] != version:
            raise ValueError("Desktop builds have different sources or versions")
        installer = f"Open-Agent-World-{version}-{item['platform']}.{PLATFORMS[item['platform']]}"
        if not {installer, installer + ".sha256"} <= item["files"].keys():
            raise ValueError("Build record has no installer or checksum")
        for name, checksum in item["files"].items():
            if Path(name).name != name or not (assets / name).is_file() or digest(assets / name) != checksum:
                raise ValueError(f"Missing or changed release asset: {name}")
    files = {p.name: digest(p) for p in sorted(assets.iterdir()) if p.is_file() and p.name != "release-manifest.json"}
    (assets / "release-manifest.json").write_text(json.dumps(
        {**validation, "builds": records, "sha256": files}, indent=2) + "\n", encoding="utf-8")
    notes = (root / "docs/release-notes.md").read_text(encoding="utf-8")
    evidence = "\n".join(f"- [{name}]({run['html_url']})" for name, run in validation["checks"].items())
    channel = "manual preview" if validation["tag"] is None else "prerelease" if "-" in version else "stable"
    header = (f"# Open Agent World v{version}\n\n"
              f"Source commit: `{sha}`. Channel: {channel}.\n\n"
              f"Verified workflows for this commit:\n\n{evidence}\n\n"
              "All three installers use this source. `release-manifest.json` records their SHA-256 checksums "
              "and validation runs. Signing and installed-app acceptance remain separate release requirements.\n\n")
    (root / "release-notes.generated.md").write_text(header + notes, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--platform", choices=PLATFORMS)
    mode.add_argument("--assemble", action="store_true")
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    version = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]
    if args.assemble:
        assemble(ROOT, sha, version)
    else:
        record(ROOT, args.platform, sha, version)


if __name__ == "__main__":
    main()
