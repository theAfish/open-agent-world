import hashlib
import json
from pathlib import Path

from plugins.matcreator.oaw_matcreator.compatibility import adapt_package


def test_shipped_cli_instructions_have_maintained_notes_and_separate_provenance():
    root = Path(__file__).parents[1] / "oaw_matcreator" / "packages"
    package = json.loads((root / "research.json").read_text(encoding="utf-8"))
    skill = next(s for s in package["skills"] if s["id"] == "bohrium")
    assert "bohrium.com/download/bohr | sh" not in skill["instructions"]
    assert "bohr project list --json" not in skill["instructions"]
    assert "npm install -g @dptech-corp/bohr-cli" in skill["instructions"]
    assert "bohr project list -o json" in skill["files"]["references/bohrium-cli-ref.md"]
    assert skill["defaults"]["upstream"]["revision"] == "a1a57688cdb7fc476498476cc388f932b6e83d6a"
    notes = skill["defaults"]["compatibility"]
    assert "unverified" in notes["verification_scope"]
    assert notes["instruction_sha256"] == hashlib.sha256(skill["instructions"].encode()).hexdigest()
    assert adapt_package(package) == package
