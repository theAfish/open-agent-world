"""One dependent delivery pipeline composed from authorized OAW operations.

The model supplies the artifacts and acceptance parameters; the host sequences
validate -> convert -> import -> read-back, checking each native receipt before
the next step, without a model request between dependent operations. No host
process executes code and no new store is introduced: native Sandbox and graph
capabilities remain the only executors and the only evidence. A failure stops
the pipeline at its exact step; later steps are never dispatched, and nothing
is replayed automatically.
"""
from __future__ import annotations

import asyncio
import ast
import math
import posixpath
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import SimpleNamespace
from typing import Any

from open_agent_world.plugin_api.tool_execution import (
    execute_tool, tool_payload as _payload, collect_operation_result, OperationReceiptMismatch,
)


def _report_json(result: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import json
        report = json.loads(result.get("stdout", "") or "")
    except (ValueError, TypeError):
        return {}
    return report if isinstance(report, dict) else {}


class DeliveryArguments(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Correct the listed delivery parameter fields; no operation ran.")


@lru_cache(maxsize=2)
def _cli_parameters(command):
    """Read signatures of trusted bundled functions without loading ASE or code.

    The existing Skill CLI derives its options from these same signatures.
    No model source, external Skill content or function body is executed here.
    """
    scripts = files(__package__).joinpath("skills/structure-inspect/scripts")
    source = scripts.joinpath("structure_inspect.py").read_text(encoding="utf-8")
    # Share the validator's Literal aliases without importing scientific code.
    aliases = {node.targets[0].id: node.value for node in ast.parse(
        scripts.joinpath("structure_validation.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Subscript) and ast.unparse(node.value.value) == "Literal"}
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == command)
    count = len(function.args.args) - len(function.args.defaults)
    parameters = {}
    for i, arg in enumerate(function.args.args):
        annotation = ast.unparse(arg.annotation)
        literal = aliases.get(annotation, arg.annotation)
        choices = (ast.literal_eval(literal.slice) if isinstance(literal, ast.Subscript)
                   and ast.unparse(literal.value) == "Literal" else None)
        if choices is not None and not isinstance(choices, tuple):
            choices = (choices,)
        parameters[arg.arg] = {"type": type(choices[0]).__name__ if choices else annotation.split(" | ")[0],
            "nullable": "None" in annotation, "required": i < count,
            **({"enum": list(choices)} if choices else {}),
            **({"default": ast.literal_eval(function.args.defaults[i - count])} if i >= count else {})}
    return parameters


def validation_schema():
    kinds = {"int": "integer", "float": "number", "str": "string", "bool": "boolean"}
    return {"type": "object", "additionalProperties": False,
            "properties": {key: {"type": kinds[meta["type"]], **({"enum": list(meta["enum"])} if "enum" in meta else {})}
                           for key, meta in _cli_parameters("validate_structure").items()
                           if key not in {"folder", "file_name"}},
            "description": "Optional acceptance parameters. Omit or repeat identical parameters to reuse the current build validation receipt. Changed parameters require validation. Use snake_case names; folder and file_name are supplied by the host from structure_path."}


def _skill_argv(command, values):
    parameters = _cli_parameters(command)
    issues = []
    argv = [command]
    for key in values:
        if key not in parameters:
            issues.append({"field": key, "message": "Unknown parameter. Use a declared snake_case name, without -- prefixes."})
    for key, meta in parameters.items():
        value = values.get(key)
        if value is None and (meta["nullable"] or not meta["required"]):
            continue
        kind = meta["type"]
        if kind == "bool" and isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"  # Compatibility with prior JSON-to-CLI callers.
        try:
            finite = type(value) in {int, float} and math.isfinite(value)
        except OverflowError:
            finite = False
        valid = (type(value) is bool if kind == "bool" else type(value) is int if kind == "int" else
                 finite if kind == "float" else
                 isinstance(value, str) and bool(value.strip()))
        if not valid:
            issues.append({"field": key, "message": f"Supply a finite {kind} value of the declared type."})
            continue
        if "enum" in meta and value not in meta["enum"]:
            issues.append({"field": key, "message": "Choose one of the declared values.", "allowed_values": list(meta["enum"])})
            continue
        argv.extend(("--" + key.replace("_", "-"), str(value).lower() if kind == "bool" else str(value)))
    if issues:
        raise DeliveryArguments(issues)
    return argv


def _validate_argv(structure_path: str, validate: Mapping[str, Any] | None) -> list[str]:
    if validate is not None and not isinstance(validate, Mapping):
        raise DeliveryArguments([{"field": "validate", "message": "Use an object of acceptance parameters or omit it."}])
    values = dict(validate or {})
    issues = []
    if "folder" in values and (not isinstance(values["folder"], str) or values["folder"] not in {"", "."}):
        issues.append({"field": "folder", "message": "Use structure_path for the complete workspace-relative path; folder is supplied as ."})
    if "file_name" in values and (not isinstance(values["file_name"], str)
            or posixpath.normpath(values["file_name"]) != structure_path):
        issues.append({"field": "file_name", "message": "The input file must match structure_path."})
    if issues:
        raise DeliveryArguments(issues)
    return _skill_argv("validate_structure", {**values, "folder": ".", "file_name": structure_path})


def validation_identity(args, environment_revision):
    """Normalize the existing validator invocation, without executing Skill code."""
    argv = args.get("argv")
    if args.get("script") != "scripts/structure_inspect.py" or not isinstance(argv, list) or not argv or argv[0] != "validate_structure":
        return None
    parameters = _cli_parameters("validate_structure")
    if len(argv) % 2 != 1 or any(not isinstance(a, str) for a in argv):
        return None
    pairs = list(zip(argv[1::2], argv[2::2]))
    if len({key for key, _ in pairs}) != len(pairs):
        return None
    values = {key: meta.get("default") for key, meta in parameters.items()}
    for key, value in pairs:
        key = key.removeprefix("--").replace("-", "_")
        if key not in parameters:
            return None
        kind = parameters[key]["type"]
        try:
            if kind == "bool":
                if value.lower() not in {"true", "false"}:
                    return None
                value = value.lower() == "true"
            elif kind == "int":
                value = int(value)
            elif kind == "float":
                value = float(value)
                if not math.isfinite(value):
                    return None
        except (ValueError, OverflowError):
            return None
        values[key] = value
    values.pop("folder", None)
    values.pop("file_name", None)
    # This extra file has mutable contents outside this candidate's receipt.
    # Keep running validation until its own dependency identity is available.
    if values.get("region_checks_file") is not None:
        return None
    return {"skill": args.get("skill"), "script": args["script"], "parameters": values,
            "environment_revision": environment_revision}


def reusable_validation(evidence, prior, sandbox, skill, argv, validate):
    if not prior or prior.get("phase") != "validated":
        return False
    artifact = evidence.artifact_handoffs.get((sandbox, prior["path"]))
    if artifact and prior.get("execution_sequence", -1) < artifact.get("execution_sequence", 0):
        return False
    known = prior.get("validation")
    if known is None:
        return validate is None and "validation" not in prior  # Legacy receipts lack invocation metadata.
    requested = validation_identity({"skill": skill, "script": "scripts/structure_inspect.py", "argv": argv},
                                    evidence.environment_revisions.get(sandbox, 0))
    if requested is None or known.get("skill") != skill or known.get("environment_revision") != requested["environment_revision"]:
        return False
    return validate is None or known == requested


def is_independent_validation(args, evidence):
    """Recognize only the read-only inspector for an exact host-owned artifact."""
    argv = args.get("argv")
    if (args.get("script") != "scripts/structure_inspect.py" or not isinstance(argv, list)
            or not isinstance(args.get("sandbox"), str) or not argv or argv[0] != "validate_structure"
            or len(argv) % 2 != 1 or any(not isinstance(v, str) for v in argv)):
        return False
    pairs = list(zip(argv[1::2], argv[2::2]))
    if len({k for k, _ in pairs}) != len(pairs):
        return False
    values = dict(pairs)
    path = values.get("--file-name")
    receipt = evidence.artifact_handoffs.get((args.get("sandbox"), path))
    return bool(receipt and values.get("--folder") == ".")


def validation_step_function(evidence, definitions, invoke, stopped):
    async def validate_modelling_artifact(sandbox: str, skill: str, structure_path: str,
                                         checks: dict | None = None) -> dict:
        """Independently validate a saved HOST artifact. Call alone: starts Sandbox work and records a validation receipt. Preserves the artifact; never imports or advances a stage."""
        if stopped.is_set():
            raise asyncio.CancelledError()
        def error(code, message, **details):
            return {"ok": False, "error": {"code": code, "message": message,
                "kind": "step_contract", "command_dispatched": False, **details}}
        if (not evidence.workflow.active or evidence.workflow.current not in {"probe", "build"}
                or evidence.workflow.review_request is not None or evidence.workflow.blocked):
            return error("validation_phase", "Complete the current plan review before independent validation.")
        receipt = evidence.artifact_handoffs.get((sandbox, structure_path))
        if not receipt:
            return error("validation_artifact", "Use a produced path from HOST artifact_handoffs.")
        if checks is not None and (not isinstance(checks, Mapping) or
                                  set(checks) & {"folder", "file_name"}):
            return error("validation_arguments", "checks holds acceptance parameters only; the host supplies the file path.")
        try:
            argv = _validate_argv(structure_path, checks)
        except DeliveryArguments as exc:
            return error("validation_arguments", str(exc), issues=exc.issues)
        operations = {evidence.semantic_tool(d.name): d for d in definitions}
        if "run_skill_script" not in operations:
            return error("validation_capability", "The native Skill executor must be authorized.")
        if evidence.steps.active or evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations:
            return error("validation_pending", "Collect pending native operations before another validation.")
        hooks = evidence.tool_callbacks("structure_builder")
        import uuid
        call_id = "validation:" + uuid.uuid4().hex
        async def operation(name, args):
            if stopped.is_set():
                raise asyncio.CancelledError()
            definition = operations.get(name)
            if definition is None:
                return error("validation_wait", "Collect the pending Skill using its authorized native waiter.")
            context = SimpleNamespace(function_call_id=call_id + ":" + name)
            tool = SimpleNamespace(name=definition.name)
            denied = await hooks["before_tool_callback"](tool, args, context)
            if denied is not None:
                return denied
            response = (await execute_tool(lambda: invoke(definition.capability_id, args))).response
            await hooks["after_tool_callback"](tool, args, context, response)
            return response
        result = await operation("run_skill_script", {"sandbox": sandbox, "skill": skill,
            "script": "scripts/structure_inspect.py", "argv": argv})
        try:
            result = await collect_operation_result(result, lambda op, seconds: operation(
                "wait_sandbox_operation", {"sandbox": sandbox, "operation_id": op, "wait_seconds": seconds}), stopped)
        except OperationReceiptMismatch as exc:
            return error("validation_wait", str(exc))
        payload = _payload(result)
        report = _report_json(payload)
        if payload.get("exit_code") == 2:
            return {"ok": False, "error": {"code": "validation_cli_error", "kind": "step_contract",
                "command_dispatched": True, "message": "The Skill CLI rejected arguments before validation. Check the equipped/linked Skill and its declared parameters. Keep the structure unchanged."},
                "validation": result}
        if report.get("error") and "violations" not in report:
            return {"ok": False, "error": {"code": "validation_unavailable", "kind": "native_dependency",
                "command_dispatched": True, "message": "The validator did not produce a scientific verdict. Inspect its parameter, input or dependency error and correct that cause or report the blocker. Do not redesign the scientific method from this error alone."},
                "validation": result, "report": report}
        bound = report.get("file") == structure_path
        passed = bool(bound and payload.get("exit_code") == 0 and report.get("passed") is True)
        next_action = (evidence.actions.next_action(evidence, structure_path=structure_path)[0] if passed else
            "Use the independent measurements to assess the current execution or patch one failed check. Stage acceptance is unchanged.")
        return {"ok": passed, "validation": result, "report": report, "artifact_bound": bound,
                "next_action": next_action,
                **({"checkpoint_action": {"tool": "advance_modelling_stage", "arguments": completion}}
                   if passed and (completion := evidence.workflow.checkpoint_arguments(evidence, structure_path)) else {})}
    return validate_modelling_artifact


async def run_delivery_step(evidence: Any, definitions: Any, invoke: Any, stopped: Any, *, call_id: str,
                            sandbox: str, skill: str, structure_path: str, target: str,
                            output_name: str = "", validate: Mapping[str, Any] | None = None) -> dict:
    def error(code: str, message: str, **details: Any):
        return {"ok": False, "error": {"code": code, "message": message, **details}}

    if stopped.is_set():
        raise asyncio.CancelledError()
    structure_path = posixpath.normpath(structure_path)
    if target in evidence.delivery_uncertainty:
        return error("delivery_import_unknown", "A previous import has no confirmed outcome; inspect native receipts and the live document before another import.",
                     pending=evidence.delivery_uncertainty[target])
    if target in evidence.imports and evidence.confirmed.get(target) != evidence.imports[target]:
        return error("delivery_read_back_required", "An import already succeeded; inspect its revision instead of replaying the delivery pipeline.", revision=evidence.imports[target])
    workflow = evidence.workflow
    if not workflow.active or workflow.current != "deliver":
        return error("delivery_phase", "Use this pipeline in the deliver stage; probe/build experiments use run_modelling_step.")
    if workflow.delivery != "document":
        return error("delivery_kind", f"Requested delivery is '{workflow.delivery}'; document import is not required.")
    if workflow.review_request is not None:
        return error("plan_review_required", "Complete the Planner review before delivery.")
    if target in evidence.imports and evidence.confirmed.get(target) == evidence.imports[target]:
        # A manual delivery already succeeded. Advancing its native checkpoint
        # is the remaining action; never import again to repair a stage field.
        build = next((c for c in reversed(workflow.checkpoints) if c.get("stage") == "build"), {})
        if build.get("structure_path") != structure_path or build.get("sandbox") != sandbox:
            return error("delivery_already_confirmed", "A Structure import and read-back already succeeded. Close its checkpoint with advance_modelling_stage(stage='deliver', target); do not replay a different candidate.", target=target, revision=evidence.imports[target])
        advance = workflow.advance(evidence, "deliver", target=target)
        return {**advance, "receipt_reused": True, "target": target, "revision": evidence.imports[target],
                "read_back": {"revision": evidence.confirmed[target], "summary": None,
                              "source": "retained_host_confirmation"},
                "next_action": "Report the retained native revision confirmation. Read-back summary fields are unavailable in this receipt; do not invent them or repeat import."}
    try:
        validation_argv = _validate_argv(structure_path, validate)
        convert_argv = _skill_argv("to_atomsculptor_document", {"folder": ".", "file_name": structure_path,
                                                               "output_name": output_name or None})
    except DeliveryArguments as exc:
        evidence.delivery_issue = {"code": "delivery_arguments", "issues": exc.issues,
                                   "retry_tool": "run_delivery_step", "terminal": False}
        return error("delivery_arguments", str(exc), issues=exc.issues,
                     allowed_parameters=list(_cli_parameters("validate_structure")))
    evidence.delivery_issue = None
    operations = {evidence.semantic_tool(d.name): d for d in definitions}
    required = {"run_skill_script", "import_atom_structure_file", "inspect_atom_structure"}
    if not required <= operations.keys():
        return error("delivery_capabilities", "Connect the Skill run, Structure import and inspection capabilities.")
    for name in ("run_delivery_step", "run_skill_script", "import_atom_structure_file", "inspect_atom_structure"):
        denied = workflow.before_tool("structure_builder", name, {"target": target, "sandbox": sandbox}, evidence)
        if denied is not None:
            return denied
    hooks = evidence.tool_callbacks("structure_builder")
    steps: list[dict[str, Any]] = []

    async def operation(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if stopped.is_set():
            raise asyncio.CancelledError()
        definition = operations.get(name)
        if definition is None:
            return error("delivery_wait_unavailable", "The operation is still pending and no native waiter is authorized; inspect it before resuming.")
        tool = SimpleNamespace(name=definition.name)
        context = SimpleNamespace(function_call_id=f"{call_id}:{name}:{len(steps)}")
        rejected = await hooks["before_tool_callback"](tool, dict(args), context)
        if rejected is not None:
            return rejected
        result = (await execute_tool(lambda: invoke(definition.capability_id, dict(args)))).response
        await hooks["after_tool_callback"](tool, dict(args), context, result)
        return result

    async def run_skill(argv: list[str]) -> dict[str, Any]:
        if evidence.skill_operations:
            return error("delivery_pending", "Collect the previous Skill operation before starting another; nothing was replayed.",
                         operation_ids=list(evidence.skill_operations))
        result = await operation("run_skill_script", {"sandbox": sandbox, "skill": skill,
                                                      "script": "scripts/structure_inspect.py", "argv": argv})
        try:
            result = await collect_operation_result(result, lambda op, seconds: operation(
                "wait_sandbox_operation", {"sandbox": sandbox, "operation_id": op, "wait_seconds": seconds}),
                stopped, on_progress=lambda payload: evidence.sandbox_wait_progress(payload,
                    sandbox=sandbox, workflow_event="delivery_command_wait"))
        except OperationReceiptMismatch as exc:
            return error("delivery_pending", str(exc), operation_id=_payload(exc.pending).get("operation_id"))
        payload = _payload(result)
        return dict(payload) if payload else result if isinstance(result, Mapping) else {}

    # 1. Independent validation of the exact candidate file with explicit
    #    acceptance parameters; the model's own script assertions do not count.
    # Build already required independent validation. Reuse that receipt unless
    # explicit new parameters are requested; conversion uses the same input path.
    prior = evidence.outputs.get((sandbox, posixpath.normpath(structure_path)))
    if reusable_validation(evidence, prior, sandbox, skill, validation_argv, validate):
        report = prior["measurements"]
        validated = {"exit_code": 0, "receipt_reused": True}
    else:
        validated = await run_skill(validation_argv)
        if validated.get("exit_code") != 0:
            if validated.get("exit_code") == 2:
                evidence.delivery_issue = {"code": "delivery_cli_error", "step": "validate", "terminal": True}
                return error("delivery_cli_error", "The Skill CLI rejected the host's arguments before any structure validation. Report this interface blocker; do not change the structure or replay delivery.", validation=validated)
            return error("delivery_validation_failed", "Independent validation failed or did not finish; "
                         "no conversion or import was dispatched.", validation=validated)
        report = _report_json(validated)
    if (report.get("passed") is not True or report.get("file") != structure_path):
        return error("delivery_validation_failed", "Validation produced no passing receipt for this file.",
                     validation=validated)
    steps.append({"step": "validate", "file": report.get("file"),
                  "receipt_reused": validated.get("receipt_reused", False)})

    # 2. Convert the validated file through the native Skill executor.
    converted = await run_skill(convert_argv)
    if converted.get("exit_code") != 0:
        if converted.get("exit_code") == 2:
            evidence.delivery_issue = {"code": "delivery_cli_error", "step": "convert", "terminal": True}
            return error("delivery_cli_error", "The Skill CLI rejected conversion arguments before import. Report this interface blocker; do not replay delivery.", conversion=converted)
        return error("delivery_conversion_failed", "Conversion failed; no import was dispatched.", conversion=converted)
    document = _report_json(converted)
    document_file = document.get("output_document_file")
    if (not isinstance(document_file, str) or not document_file
            or document.get("input_structure_file") != structure_path
):
        return error("delivery_conversion_failed", "Conversion receipt does not bind the validated input; "
                     "revalidate this file and convert again.", conversion=converted)
    steps.append({"step": "convert", "document_file": document_file})

    # 3. A fresh revision for the optimistic-concurrency import check.
    inspected = await operation("inspect_atom_structure", {"target": target})
    revision = _payload(inspected).get("revision")
    if type(revision) is not int:
        return error("delivery_read_failed", "Inspect the target Structure before import.", inspection=inspected)
    imported = await operation("import_atom_structure_file", {"sandbox": sandbox, "file_name": document_file,
        "target": target, "expected_revision": revision})
    import_revision = _payload(imported).get("revision")
    if type(import_revision) is not int:
        if target in evidence.delivery_uncertainty:
            return error("delivery_import_unknown", "The import outcome is unknown; inspect native receipts and the live document before another import.",
                         pending=evidence.delivery_uncertainty[target], import_result=imported)
        return error("delivery_import_failed", "Import was rejected; inspect the conflict and re-inspect before retrying.",
                     import_result=imported)
    steps.append({"step": "import", "revision": import_revision})

    # 4. Read the live document back; only matching evidence confirms delivery.
    read_back = await operation("inspect_atom_structure", {"target": target})
    confirmed = _payload(read_back).get("revision") == import_revision and target in evidence.confirmed
    if not confirmed:
        return error("delivery_read_back_required", "Import revision has no matching read-back yet; "
                     "inspect the target again before claiming delivery.", read_back=read_back)
    steps.append({"step": "read_back", "revision": import_revision})
    advance = workflow.advance(evidence, "deliver", target=target)
    if not advance.get("ok"):
        return advance
    evidence.notice("structure_builder", "Delivery pipeline completed with native receipts",
                    workflow_event="delivery_pipeline", target=target, revision=import_revision)
    imported_summary = _payload(imported).get("summary") or {}
    read_back_summary = _payload(read_back).get("summary") or {}
    expected_count, actual_count = imported_summary.get("atom_count"), read_back_summary.get("atom_count")
    # The native inspector already supplies this bounded overview. Reuse it;
    # no second inspection or atom-window/coordinate-array transfer is needed.
    overview = _payload(read_back).get("value") or {}
    return {"ok": True, "steps": steps, "target": target, "revision": import_revision,
            "file": structure_path, "document_file": document_file,
            "import": {"revision": import_revision, "summary": _payload(imported).get("summary")},
            "read_back": {"revision": _payload(read_back).get("revision"),
                          "summary": _payload(read_back).get("summary"),
                          "structure": {key: overview[key] for key in ("atom_count", "formula", "coordinate_bounds", "cell", "pbc") if key in overview},
                          "source": "native_inspect_atom_structure"},
            "confirmation": {"revision_match": True,
                "atom_count_match": (actual_count == expected_count if expected_count is not None and actual_count is not None else None),
                "scope": "Native import/read-back revision and available atom_count. File validation is separate; species and per-atom geometry/scientific checks were not re-run on the read-back document."},
            "validation": {"passed": True, "scope": report.get("validation_scope"),
                           "receipt_reused": validated.get("receipt_reused", False)},
            "next_stage": workflow.current,
            "next_action": "The native inspect/import/inspect sequence is complete. Use read_back and confirmation for document facts and the validation receipt for file facts; finish_task with measured limitations. No additional inspection is needed to repeat this confirmation."}


def delivery_step_function(evidence: Any, definitions: Any, invoke: Any, stopped: Any):
    async def run_delivery_step_tool(sandbox: str, skill: str, structure_path: str, target: str, tool_context,
                                     output_name: str = "", validate: dict | None = None) -> dict:
        """Validate, convert, import and read back one finished candidate in a single action.

        Each step checks the previous native receipt; a failure stops at its exact
        step and nothing later is dispatched. validate carries the Skill's explicit
        acceptance parameters (for example expected_count or min_vacuum).
        output_name is a workspace-relative JSON filename ending in .json,
        for example candidate.json, not a display title; omit it for the default.
        """
        return await run_delivery_step(evidence, definitions, invoke, stopped,
            call_id=tool_context.function_call_id, sandbox=sandbox, skill=skill,
            structure_path=posixpath.normpath(structure_path), target=target,
            output_name=output_name, validate=validate)
    run_delivery_step_tool.__name__ = "run_delivery_step"
    return run_delivery_step_tool
