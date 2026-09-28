"""Source-bound method specifications and portable, verifiable OAW Skill packages.

No code is run by these helpers. Execution receipts and bytes must come from a
host-owned completed run, never an agent-authored success flag. Hash comparison
proves artifact consistency; the host still authenticates who actually ran it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Literal, Mapping

from pydantic import Field, JsonValue, field_validator, model_validator
from open_agent_world.skill_packages import Skill, SkillAsset, SkillPackage
from oaw_library.contracts import Sha256, normalize_source_url
from .evidence import EvidenceSource, Model, SourceValidator, aware_timestamp, canonical_sha256, validate_sources


def relative_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 240 or any(char in value for char in "\\:\x00"):
        raise ValueError("Use a bounded portable relative artifact path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part) for part in parts):
        raise ValueError("Use a safe portable relative artifact path")
    return value


class MethodParameter(Model):
    name: str = Field(min_length=1, max_length=128)
    value: JsonValue = None
    units: str | None = Field(default=None, min_length=1, max_length=100)
    source_anchor_ids: list[str] = Field(default_factory=list, max_length=100)
    origin: Literal["source", "engineering_adaptation", "missing"] = "source"
    rationale: str = Field(default="", max_length=5000)
    missing_reason: str | None = Field(default=None, min_length=1, max_length=5000)

    @model_validator(mode="after")
    def no_invented_parameter(self):
        if self.origin == "missing":
            if self.value is not None or not self.missing_reason or not self.missing_reason.strip():
                raise ValueError("A missing parameter must have null value and an explicit reason")
        elif self.value is None or self.missing_reason:
            raise ValueError("A known parameter needs a value; mark absent information as missing")
        elif self.origin == "source" and not self.source_anchor_ids:
            raise ValueError("Source parameters require their source anchor IDs")
        elif self.origin == "engineering_adaptation" and not self.rationale.strip():
            raise ValueError("Engineering adaptations require a separate rationale")
        canonical_sha256(self.value)  # Reject NaN/Infinity in arbitrary JSON parameters.
        return self


class MethodIO(Model):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=5000)
    data_type: str = Field(min_length=1, max_length=100)
    units: str | None = Field(default=None, min_length=1, max_length=100)


class MethodStep(Model):
    instruction: str = Field(min_length=1, max_length=10_000)
    source_anchor_ids: list[str] = Field(default_factory=list, max_length=100)
    origin: Literal["source", "engineering_adaptation"] = "source"
    rationale: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def step_provenance(self):
        if self.origin == "source" and not self.source_anchor_ids:
            raise ValueError("A source-derived step requires its source anchor IDs")
        if self.origin == "engineering_adaptation" and not self.rationale.strip():
            raise ValueError("An adapted step requires an engineering rationale")
        return self


class PrincipleReference(Model):
    title: str = Field(min_length=1, max_length=500)
    url: str
    notes: str = Field(default="", max_length=5000)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        result = normalize_source_url(value)
        if result is None:
            raise ValueError("A principle reference requires a source URL")
        return result


class ExpectedMetric(Model):
    value: float = Field(allow_inf_nan=False, strict=True)
    absolute_tolerance: float = Field(ge=0, allow_inf_nan=False, strict=True)
    units: str = Field(min_length=1, max_length=100)


class MethodBaseline(Model):
    id: str = Field(min_length=1, max_length=128)
    input_sha256: Sha256
    description: str = Field(min_length=1, max_length=5000)
    independence: str = Field(min_length=1, max_length=5000)
    metrics: dict[str, ExpectedMetric] = Field(min_length=1, max_length=100)


class MethodRuntime(Model):
    language: Literal["python"] = "python"
    version: str = Field(min_length=1, max_length=100)
    entrypoint: str
    dependencies: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("entrypoint")
    @classmethod
    def portable_entrypoint(cls, value):
        return relative_path(value)


class MethodSpec(Model):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    revision: int = Field(default=1, ge=1, strict=True)
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=1000)
    sources: list[EvidenceSource] = Field(min_length=1, max_length=100)
    inputs: list[MethodIO] = Field(default_factory=list, max_length=100)
    outputs: list[MethodIO] = Field(default_factory=list, max_length=100)
    parameters: list[MethodParameter] = Field(default_factory=list, max_length=200)
    steps: list[MethodStep] = Field(default_factory=list, max_length=200)
    preconditions: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    acceptance: list[str] = Field(default_factory=list, max_length=100)
    missing: list[str] = Field(default_factory=list, max_length=100)
    license: str | None = Field(default=None, min_length=1, max_length=100)
    license_notes: str = Field(default="", max_length=5000)
    principle_references: list[PrincipleReference] = Field(default_factory=list, max_length=100)
    runtime: MethodRuntime | None = None
    implementation_files: dict[str, str] = Field(default_factory=dict, max_length=100)
    baseline: MethodBaseline | None = None

    @model_validator(mode="after")
    def connected_specification(self):
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("Duplicate method source anchor IDs")
        for values in (self.inputs, self.outputs, self.parameters):
            if len({value.name for value in values}) != len(values):
                raise ValueError("Duplicate method input, output or parameter names")
        for item in [*self.parameters, *self.steps]:
            if set(item.source_anchor_ids) - source_ids:
                raise ValueError("Method parameter or step refers to an unknown source anchor")
        for values in (self.preconditions, self.constraints, self.acceptance, self.missing):
            if any(not item.strip() or len(item) > 5000 for item in values):
                raise ValueError("Method conditions and missing items must be explicit bounded text")
        reserved = {"SKILL.md", "method.json", "sources.json", "validation/report.json", "validation/receipt.json"}
        for path, content in self.implementation_files.items():
            relative_path(path)
            if path in reserved or path.startswith("validation/") or len(content.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("Method files must not replace provenance/validation files or exceed 2 MiB")
        # The standard Skill contract also guards file/folder collisions.
        Skill(id=self.id, files=self.implementation_files)
        if self.runtime and self.runtime.entrypoint not in self.implementation_files:
            raise ValueError("Method entrypoint is missing from its implementation files")
        if self.baseline:
            outputs = {output.name: output for output in self.outputs}
            for name, expected in self.baseline.metrics.items():
                if name not in outputs or outputs[name].units != expected.units:
                    raise ValueError("Baseline metric names and units must match method outputs")
        return self


def method_sha256(method: MethodSpec | dict) -> str:
    value = MethodSpec.model_validate(method).model_dump(mode="json")
    for source in value["sources"]:
        source.pop("status", None)
    return canonical_sha256(value)


def method_gaps(method: MethodSpec | dict) -> list[str]:
    method = MethodSpec.model_validate(method)
    gaps = list(method.missing)
    gaps.extend(f"Parameter {item.name}: {item.missing_reason}" for item in method.parameters if item.origin == "missing")
    for field in ("inputs", "outputs", "steps", "preconditions", "constraints", "acceptance", "runtime", "license"):
        if not getattr(method, field):
            gaps.append(f"Missing {field}")
    for item in [*method.inputs, *method.outputs, *method.parameters]:
        if item.units is None:
            gaps.append(f"Missing units for {item.name}; use 'dimensionless' when appropriate")
    return gaps


class ExecutionReceipt(Model):
    run_id: str = Field(min_length=1, max_length=200)
    executor: Literal["oaw_sandbox", "controlled_python_fixture"]
    method_sha256: Sha256
    started_at: datetime
    finished_at: datetime
    command: list[str] = Field(min_length=1, max_length=100)
    exit_code: int = Field(strict=True)
    input_path: str
    input_sha256: Sha256
    output_sha256: dict[str, Sha256] = Field(min_length=1, max_length=100)
    metrics_path: str
    validation_scope: str = Field(min_length=1, max_length=5000)

    @field_validator("started_at", "finished_at")
    @classmethod
    def timezone(cls, value):
        return aware_timestamp(value)

    @field_validator("input_path", "metrics_path")
    @classmethod
    def portable_path(cls, value):
        return relative_path(value)

    @model_validator(mode="after")
    def run_artifacts(self):
        if self.finished_at < self.started_at or not self.validation_scope.strip():
            raise ValueError("A completed run needs ordered timestamps and an explicit validation scope")
        for path in self.output_sha256:
            relative_path(path)
        if self.input_path in self.output_sha256:
            raise ValueError("Validation input must be immutable and separate from output artifacts")
        if self.metrics_path not in self.output_sha256:
            raise ValueError("Metrics must be an actual hashed output artifact")
        return self


def verify_execution(method: MethodSpec | dict, receipt: ExecutionReceipt | dict,
    artifacts: Mapping[str, bytes]) -> dict:
    """Verify a collected run against immutable method/input/output identities."""
    method, receipt = MethodSpec.model_validate(method), ExecutionReceipt.model_validate(receipt)
    if receipt.method_sha256 != method_sha256(method):
        raise ValueError("Execution receipt belongs to a different method revision or implementation")
    expected = {receipt.input_path: receipt.input_sha256, **receipt.output_sha256}
    for path, digest in expected.items():
        data = artifacts.get(path)
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Missing or changed execution artifact: {path}")
    gaps = method_gaps(method)
    if receipt.exit_code != 0:
        return {"status": "draft", "run_id": receipt.run_id, "scope": receipt.validation_scope,
            "gaps": [*gaps, f"Execution failed with exit code {receipt.exit_code}"], "checks": []}
    try:
        metrics = json.loads(artifacts[receipt.metrics_path].decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("Metrics output must be UTF-8 JSON") from error
    if not isinstance(metrics, dict) or any(type(value) not in {int, float} or not math.isfinite(value) for value in metrics.values()):
        raise ValueError("Metrics output must be a finite numeric object")
    checks = []
    if method.baseline:
        if method.baseline.input_sha256 != receipt.input_sha256:
            raise ValueError("Run input does not match the independent baseline fixture")
        for name, expected_metric in method.baseline.metrics.items():
            observed = metrics.get(name)
            checks.append({"metric": name, "observed": observed, "expected": expected_metric.value,
                "absolute_tolerance": expected_metric.absolute_tolerance, "units": expected_metric.units,
                "passed": observed is not None and abs(observed - expected_metric.value) <= expected_metric.absolute_tolerance})
    status = "draft" if gaps else "validated" if checks and all(check["passed"] for check in checks) else "executable"
    return {"status": status, "run_id": receipt.run_id, "scope": receipt.validation_scope,
        "gaps": gaps, "checks": checks, "baseline_id": method.baseline.id if method.baseline else None,
        "scientific_claim": "Only the recorded input, outputs and baseline conditions were checked."}


def method_to_skill_package(method: MethodSpec | dict, *, source_validator: SourceValidator,
    receipt: ExecutionReceipt | dict | None = None, artifacts: Mapping[str, bytes] | None = None,
    allow_synthetic: bool = False) -> SkillPackage:
    """Build an exportable standard package; no persistence, network or execution."""
    import base64
    method = MethodSpec.model_validate(method)
    checked = validate_sources(method.sources, source_validator, allow_synthetic=allow_synthetic)
    method = method.model_copy(update={"sources": checked}, deep=True)
    report = {"status": "draft", "gaps": [*method_gaps(method), "No completed execution receipt"], "checks": []}
    if receipt is not None:
        receipt = ExecutionReceipt.model_validate(receipt)
        if receipt.executor == "controlled_python_fixture" and not allow_synthetic:
            raise ValueError("Controlled fixture receipts are not production method validation")
        report = verify_execution(method, receipt, artifacts or {})
    digest = method_sha256(method)
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    files = {**method.implementation_files,
        "method.json": dump({"spec": method.model_dump(mode="json"), "spec_sha256": digest, "status": report["status"]}),
        "sources.json": dump({"sources": [source.model_dump(mode="json") for source in method.sources],
            "principle_references": [item.model_dump(mode="json") for item in method.principle_references],
            "synthetic_demo": any(source.source_kind == "synthetic_fixture" for source in method.sources)}),
        "validation/report.json": dump(report)}
    if receipt is not None:
        files["validation/receipt.json"] = dump(receipt.model_dump(mode="json"))
        for path in [receipt.input_path, *receipt.output_sha256]:
            files["validation/artifacts/" + path] = SkillAsset(data_base64=base64.b64encode(artifacts[path]).decode())
    instructions = "\n".join([
        f"# {method.name}", "", method.purpose, "", f"Method status: {report['status']}. Method revision: {method.revision}.",
        "Read method.json for exact parameters, units, missing items and constraints; read sources.json for source identities.",
        "Read validation/report.json before relying on a reported validation state. A successful example applies only to its recorded conditions.",
        "Paper statements, engineering adaptations and missing values are distinct. Do not invent absent parameters.",
        "This Skill grants no Paper, network, filesystem or execution permissions. Run scripts only with separately authorized Skill and Sandbox tools.",
        "", "## Preconditions", *[f"- {item}" for item in method.preconditions],
        "", "## Procedure", *[f"{index}. {step.instruction}" for index, step in enumerate(method.steps, 1)],
        "", "## Constraints", *[f"- {item}" for item in method.constraints],
        "", "## Acceptance", *[f"- {item}" for item in method.acceptance],
        "", "## Missing information", *[f"- {item}" for item in report["gaps"]],
        "", f"Implementation license: {method.license or 'unknown; resolve before reuse'}. {method.license_notes}",
        "Source publication rights remain separate from the generated implementation license.",
    ]) + "\n"
    skill = Skill(id=method.id, name=method.name, description=method.purpose,
        instructions=instructions, files=files, defaults={
            "metadata": {"entry_type": "procedure", "tags": ["literature", "source-bound-method"], "content_sha256": digest},
            "upstream": {"method_id": method.id, "method_revision": method.revision, "method_sha256": digest,
                "source_anchor_ids": [source.id for source in method.sources], "source_manifest": "sources.json"},
            "runtime": method.runtime.model_dump(mode="json") if method.runtime else None,
            "method_status": report["status"], "validation_report": "validation/report.json"})
    return SkillPackage(package_id="research.method." + method.id, version=f"0.{method.revision}.0", name=method.name,
        description=method.purpose[:500], author="OAW method extraction", skills=[skill],
        instructions="Read the method's source and validation manifests. A Skill's availability never grants execution or source access.")
