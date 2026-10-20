"""One measured script step, composed from existing authorized OAW operations.

The model supplies the code. The host saves, executes and collects it without
asking the model to reconsider the same code between those dependent actions.
No host process executes that code; native Sandbox capabilities own execution.
"""
from __future__ import annotations

import asyncio
import json
import posixpath
from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace

from open_agent_world.plugin_api.tool_execution import execute_tool, tool_payload as _payload


from .workflow import EXPERIMENT_SCHEMA, script_target


STEP_FIELDS = {"question": 300, "expected_output": 300, "stop_condition": 300}


ASSESSMENT_SCHEMA = {"type": "object", "properties": {
    **{key: {"type": "string", "minLength": 1, "description":
            "Optional: the host fills this receipt field from the latest finished execution."}
       for key in ("command_id", "sandbox", "script_path")},
    "plan_revision": {"type": "integer", "description": "Optional assessed plan revision; stale revisions are rejected."},
    **{key: {"type": "string", "minLength": 1} for key in ("hypothesis", "measurement", "next_step")},
    "outcome": {"type": "string", "enum": ["continue", "ready", "needs_plan_review", "blocked"]},
    "objective": {"type": "object", "properties": {
        "name": {"type": "string"}, "value": {"type": "number"},
        "direction": {"type": "string", "enum": ["minimize", "maximize"]}, "unit": {"type": "string"}},
        "required": ["name", "value", "direction"], "additionalProperties": False},
    "assumption_invalid": {"type": "boolean"},
    "next_experiment": EXPERIMENT_SCHEMA,
    "complete_stage": {"type": "boolean"}, "structure_path": {"type": "string"}},
    "required": ["hypothesis", "measurement", "outcome", "next_step"],
    "additionalProperties": False}
STEP_SCHEMA = {"type": "object", "properties": {
    **{key: {"type": "string", "minLength": 1, "description": f"Aim for {limit} characters; one current step."}
       for key, limit in STEP_FIELDS.items()},
    "sandbox": {"type": "string", "minLength": 1},
    "script_path": {"type": "string", "minLength": 1,
        "description": "ONE literal workspace-relative .py script, matching the current subtask artifact. Put reuse instructions in code/input_artifacts."},
    "action_mode": {"type": "string", "enum": ["new", "patch", "rewrite", "rerun", "search"],
        "description": "new requires source; patch requires edits; rewrite requires source; rerun/search use a saved path and no code. A repair is never a rerun."},
    "source": {"type": "string", "description": "Runnable code for a NEW script, or omit for an exact edit/reuse of a saved script."},
    "edits": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
        "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"], "additionalProperties": False}},
    "rewrite": {"type": "boolean", "description": "Replace an already executed script with full source instead of native edits; each script is hinted once."},
    "rewrite_reason": {"type": "string", "minLength": 1, "description": "For a local execution/format repair, explain why exact text edits cannot express it. Preserve the measured repair objective and scientific method."},
    "previous_assessment": ASSESSMENT_SCHEMA,
    "execution_scope": {"type": "string", "enum": ["sample", "search"],
        "description": "Run one small case first; search requires a successful sample of this exact saved source."},
    "repeat_purpose": {"type": "string", "minLength": 1, "maxLength": 400,
        "description": "Only for an intentional reproducibility/stochastic repeat of unchanged inputs. Otherwise the host reuses the confirmed result."},
    "argv": {"type": "array", "maxItems": 32, "items": {"type": "string"},
        "description": "Arguments AFTER the script path, e.g. --sample or --search. Never a command."},
    "input_artifacts": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
        "path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "description": "Consume HOST artifact_handoffs by path. Native Sandbox inspects inputs before modelling; preserve the saved frame/units/component identities."},
    "input_manifest_path": {"type": "string", "description": "Required with input_artifacts: a relative .json file, new on first use. Reuse the same HOST-confirmed manifest for unchanged inputs when patching; changed inputs need a new path. The host appends --oaw-input-manifest PATH to argv; parse it and load the input frames/units/components. Never overwrite an input or script."},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}},
    "required": ["sandbox", "script_path", "action_mode", *STEP_FIELDS],
    "additionalProperties": False}

# Flat, provider-portable contracts: selecting a tool selects its action.
# Do not rely on description-only conditional requirements or extract code
# from thoughts. All tools still use the same native receipt-based executor.
STEP_TOOL_MODES = {"run_modelling_step": "new", "patch_modelling_step": "patch",
                   "rewrite_modelling_step": "rewrite", "rerun_modelling_step": "rerun"}

TARGET_RECOVERY_SCHEMA = {"type": "object", "properties": {
    "subtask_id": {"type": "string", "description": "Copy the current target recovery subtask_id."},
    "script_path": {"type": "string", "description": "The single literal .py target. Use expected_script_path to correct the submitted action, or explicitly correct an unexecuted LOCAL subtask target."},
    "reason": {"type": "string", "minLength": 1, "description": "Explain only the rejected execution-field correction; code and scientific checks stay unchanged."},
    "corrections": {"type": "object", "properties": {
        key: {"anyOf": [deepcopy(STEP_SCHEMA["properties"][key]), {"type": "null"}]} for key in
        ("argv", "input_artifacts", "input_manifest_path", "timeout_seconds", "previous_assessment")},
        "additionalProperties": False, "description":
        "Optional corrected execution fields. Omit unchanged fields; null clears an optional field. Replacements pass all native checks."}},
    "required": ["subtask_id", "script_path", "reason"], "additionalProperties": False}


def step_tool_schema(name):
    from copy import deepcopy
    mode = STEP_TOOL_MODES[name]
    schema = deepcopy(STEP_SCHEMA)
    code_fields = {"new": {"source", "action_mode"}, "patch": {"edits"},
                   "rewrite": {"source"}, "rerun": {"execution_scope"}}[mode]
    schema["properties"] = {key: value for key, value in schema["properties"].items()
        if key not in {"source", "edits", "rewrite", "action_mode"} or key in code_fields}
    schema["required"] = [key for key in schema["required"] if key != "action_mode"]
    schema["required"] += sorted(code_fields - {"action_mode", "execution_scope"})
    if "source" in schema["properties"]:
        schema["properties"]["source"].update(minLength=1, description="Actual runnable Python source in THIS tool argument. Code in reasoning is not submitted or executed.")
    if "action_mode" in schema["properties"]:
        schema["properties"]["action_mode"] = {"type": "string", "enum": ["new"], "description": "This tool creates new scripts only."}
    if mode != "rerun":
        schema["properties"]["execution_scope"]["enum"] = ["sample"]
        schema["properties"].pop("repeat_purpose")
    else:
        schema["required"] += ["execution_scope", "argv"]
    if mode != "rewrite":
        schema["properties"].pop("rewrite_reason")
    return schema


def argument_error(name, args, schema=None):
    """Give actionable diagnostics before SDK defaults/validation hide a missing payload."""
    schema = schema or step_tool_schema(name)
    missing = [key for key in schema["required"] if key not in args or args[key] is None
              or (isinstance(args[key], str) and not args[key].strip())]
    unexpected = sorted(set(args) - set(schema["properties"]))
    invalid = []
    for key, spec in schema["properties"].items():
        value = args.get(key)
        if key not in args:
            continue
        if value is None and key not in schema["required"]:
            continue
        expected_type = {"string": str, "array": list, "object": Mapping, "integer": int, "boolean": bool}.get(spec.get("type"))
        if expected_type and (not isinstance(value, expected_type) or spec["type"] == "integer" and type(value) is not int):
            invalid.append(key)
        if (spec.get("enum") and value not in spec["enum"] or
                key == "edits" and (not isinstance(value, list) or not value or any(
                    not isinstance(edit, Mapping) or set(edit) != {"old", "new"}
                    or not all(isinstance(edit[k], str) for k in ("old", "new")) for edit in value))):
            invalid.append(key)
    fields = missing + unexpected + invalid
    if fields:
        return {"ok": False, "error": {"code": "step_arguments", "kind": "argument_validation",
            "command_dispatched": False,
            "fields": sorted(set(fields)), "required_fields": schema["required"], "retry_tool": name,
            "missing_fields": missing, "unexpected_fields": unexpected, "invalid_fields": sorted(set(invalid)),
            "assessment_field_locations": {key: "previous_assessment." + key for key in unexpected
                                           if key in ASSESSMENT_SCHEMA["properties"]},
            "message": "No script was saved or executed. Add missing fields, correct invalid values and REMOVE unexpected top-level fields. "
                       "Assessment fields belong inside previous_assessment when assessing the preceding execution; "
                       "a standalone stage decision uses advance_modelling_stage. Correct only the tool payload; "
                       "keep the unexecuted source/edits and scientific plan unchanged. Reasoning text is not a submitted payload."}}
    return None


def workflow_argument_error(name, args):
    """Use structured host feedback before the SDK can return a bare argument string."""
    if name == "resume_modelling_step":
        schema = TARGET_RECOVERY_SCHEMA
    else:
        fields = {
            "advance_modelling_stage": ({"stage"}, {k: "string" for k in
                ("stage", "sandbox", "script_path", "structure_path", "target", "reason", "hypothesis", "measurement", "outcome", "next_step")} |
                {"objective": "object", "assumption_invalid": "boolean", "next_experiment": "object"}),
            "validate_modelling_artifact": ({"sandbox", "skill", "structure_path"},
                {"sandbox": "string", "skill": "string", "structure_path": "string", "checks": "object"}),
            "read_modelling_source": ({"sandbox", "script_path"},
                {"sandbox": "string", "script_path": "string", "offset": "integer", "limit": "integer"})}
        if name not in fields:
            return None
        required, properties = fields[name]
        schema = {"required": sorted(required), "properties": {k: {"type": v} for k, v in properties.items()}}
    result = argument_error(name, args, schema)
    if result:
        result["error"]["code"] = "workflow_arguments"
        result["error"]["message"] = "No native operation ran. Correct the listed fields in " + name + "; keep code and scientific decisions unchanged."
    return result


def definite_save_rejection(response):
    """Native validation/authorization refused the write; transport failures stay unknown."""
    error = response.get("error") if isinstance(response, Mapping) else None
    return isinstance(error, Mapping) and error.get("type") in {"ResourceValidationError", "PermissionDeniedError"}


class ExecutionSteps:
    def __init__(self):
        self.available = False
        self.active = False
        self.records = []
        self.samples = set()
        self.sample_argv = {}
        self.argument_issue = None
        self.argument_failures = 0
        self.completed_results = {}
        self._target_rejection = None
        self.input_rejections = {}
        self.input_blocker = None
        self.helper_module = None
        self.guard_path = None

    def enable_helpers(self):
        # Unique to this Run evidence instance, including a reused Sandbox.
        import uuid
        if self.helper_module is None:
            self.helper_module = "_oaw_modelling_" + uuid.uuid4().hex

    def helper_contract(self):
        if not self.helper_module:
            return None
        return {"module": self.helper_module,
                "availability": "Staged beside the script by a native Sandbox write before the first modelling step in each Sandbox/script directory.",
                "example": "from " + self.helper_module + " import load_inputs, constraint, artifact, emit_measurements, write_extxyz\ninputs = load_inputs()  # path-indexed HOST manifest, or empty when absent\n# inputs.array(path, dataset), inputs.atoms(path), inputs.label_indices(path, array, value)\n# inputs.receipt(path) carries units/frame/components/layout\n# write_extxyz(path, atoms): ASE serialization plus file round-trip, not acceptance\n# emit_measurements(values={'observed_count': n}, coverage={'atoms': n}, constraints=[constraint(name, passed, reason, regions=['atoms'])])",
                "usage": "Reuse these standard I/O/reporting helpers and write only the current calculation. Use write_extxyz instead of hand-written file headers; independent Skill validation remains required. The _oaw_modelling_* module itself is host staging machinery: import its API; never read, reprint or validate its source.",
                "frames": "transform_points(points, source_frame, target_frame) requires identical frame.reference. Artifact.components are counts, not atom memberships: save actual per-atom component/region labels in NPZ/extxyz and consume them. Never guess indices or align unrelated coordinate frames.",
                "relaxation": "Optional relax_structure(atoms, calculator=..., movable_indices=..., fmax=..., steps=..., optimizer='FIRE' or 'LBFGS', maxstep=None) reuses ASE on a copy, preserves constraints and fixes the complement. Caller supplies suitable physics/parameters and convergence criteria. Returns (atoms, report); convergence is not acceptance or proof of stability. Skip when relaxation is unnecessary.",
                "validation": "validate_modelling_artifact uses existing independent structure-inspect checks. Region checks JSON contains regions; each region uses indices OR selector={array: saved_label_array, values: [label]} with name, degree_min and degree_max. Include the entire disjoint partition; failed checks remain failures."}

    def current_input_blocker(self, evidence):
        blocker = self.input_blocker
        if blocker and (blocker["plan_revision"] != evidence.workflow.plan_revision
                or blocker["stage"] != evidence.workflow.current
                or blocker["environment_revision"] != evidence.environment_revisions.get(blocker["sandbox"], 0)
                or any({k: v for k, v in evidence.artifact_handoffs.get((blocker["sandbox"], a["path"]), {}).items()
                        if k in a} != a for a in blocker["input_identity"])):
            self.input_blocker = blocker = None
        return blocker

    def target_recovery(self, evidence):
        retained = self._target_rejection
        pending = evidence.workflow.pending_experiment
        contract = retained["contract"] if retained else None
        if retained and (not evidence.workflow.active or evidence.workflow.blocked
                or evidence.workflow.review_request is not None or evidence.workflow.host_signal
                or pending != retained["pending_snapshot"] or contract["plan_revision"] != evidence.workflow.plan_revision
                or contract["stage"] != evidence.workflow.current
                or retained["execution_sequence"] != evidence.execution_sequence
                or any(r.get("subtask_id") == contract["subtask_id"] and r.get("state") not in
                       {"input_rejected", "input_manifest_changed", "input_save_failed", "save_rejected"} for r in self.records)):
            self._target_rejection = retained = None
        if retained is None:
            return None
        return {"subtask_id": contract["subtask_id"],
                "expected_script_path": contract["artifact"], "actual_script_path": retained["arguments"].get("script_path"),
                "can_correct_local_target": contract.get("origin") in {"assessment", "subtask", "local"},
                "rejected_fields": retained.get("fields", []),
                "correctable_fields": ["script_path", *TARGET_RECOVERY_SCHEMA["properties"]["corrections"]["properties"]],
                "submitted_changes_saved": False, "command_dispatched": False, "tool": "resume_modelling_step"}

    def retain_target_rejection(self, evidence, name, args, rejected):
        """Retain one unexecuted action in Run memory, never a Sandbox receipt."""
        problem = rejected.get("error", {})
        save_rejected = (problem.get("code") in {"step_input_save", "step_save_failed"} and
                         definite_save_rejection(problem.get("receipt", problem.get("save"))))
        if ((problem.get("code") not in {"planned_experiment_required", "planned_inputs_required", "step_arguments",
                "step_path", "step_inputs", "step_input_receipt", "step_input_manifest", "step_input_changed",
                "step_timeout", "step_input_manifest_changed", "experiment_observation_required", "assessment_fields", "assessment_receipt",
                "step_assessment_target"} and not save_rejected)
                or name not in STEP_TOOL_MODES):
            return
        properties = step_tool_schema(name)["properties"]
        values = {k: deepcopy(v) for k, v in args.items() if k in properties}
        invalid = argument_error(name, values)
        if invalid and set(invalid["error"]["fields"]) - {"script_path", *TARGET_RECOVERY_SCHEMA["properties"]["corrections"]["properties"]}:
            return
        if self.target_recovery(evidence) is None:
            values["source"] = values.get("source", "")
            values["action_mode"] = STEP_TOOL_MODES[name]
            if name == "rerun_modelling_step" and values.get("execution_scope") == "search":
                values["action_mode"] = "search"
            from uuid import uuid4
            proposal_id = uuid4().hex
            pending = deepcopy(evidence.workflow.pending_experiment)
            if pending is None and not script_target(values.get("script_path")):
                return
            contract = pending or {"artifact": values["script_path"], "origin": "local",
                "plan_revision": evidence.workflow.plan_revision, "stage": evidence.workflow.current,
                "subtask_id": f"{evidence.workflow.plan_revision}:{evidence.workflow.current}:proposal:{proposal_id}"}
            self._target_rejection = {"contract": contract, "pending_snapshot": pending,
                                      "execution_sequence": evidence.execution_sequence,
                                      "arguments": values,
                                      "fields": problem.get("fields", [problem.get("code")])}
        recovery = self.target_recovery(evidence)
        if recovery:
            rejected["error"]["recovery"] = {**recovery, "next_action":
                "Call resume_modelling_step with these IDs, script_path, reason and only the required corrections. "
                "The host retains the rejected code; omit source and do not generate another program."}

    async def resume(self, evidence, definitions, invoke, stopped, *, call_id, subtask_id,
                     script_path, reason, corrections=None):
        def error(code, message):
            return {"ok": False, "error": {"code": code, "message": message,
                                           "kind": "step_contract", "command_dispatched": False}}
        if stopped.is_set():
            from open_agent_world.plugin_api import AgentRuntimeError
            raise AgentRuntimeError("AtomSculptor Run was stopped")
        recovery = self.target_recovery(evidence)
        if not recovery or subtask_id != recovery["subtask_id"]:
            return error("target_recovery_stale", "Use the current retained action and subtask IDs; no command was dispatched.")
        if self.active or evidence.pending or evidence.executions or evidence.inline_pending or evidence.skill_operations:
            return error("step_in_progress", "Collect native execution receipts before correcting a target; never replay unknown work.")
        if not script_target(script_path) or not isinstance(reason, str) or not reason.strip():
            return error("target_recovery_fields", "Supply one literal relative .py target and explain only the rejected execution-field correction.")
        if corrections is not None and (not isinstance(corrections, Mapping) or set(corrections) -
                set(TARGET_RECOVERY_SCHEMA["properties"]["corrections"]["properties"])):
            return error("target_recovery_fields", "Correct only declared execution/previous_assessment fields; code and checks are immutable.")
        if script_path != recovery["expected_script_path"] and not recovery["can_correct_local_target"]:
            return error("target_recovery_plan", "Keep the Planner's target. Correct the submitted script_path or request Planner review.")
        # Only an unexecuted local action may change its filename. Its identity,
        # scientific contract, native facts and stage acceptance stay intact.
        retained = self._target_rejection
        values = deepcopy(retained["arguments"])
        values.update(deepcopy(corrections or {}))
        if evidence.workflow.pending_experiment is not None:
            evidence.workflow.pending_experiment["artifact"] = script_path
        values["script_path"] = script_path
        self._target_rejection = None  # Consume before dispatch, including an uncertain/failed native result.
        result = await self.run(evidence, definitions, invoke, stopped, call_id=call_id, **values)
        if result.get("error", {}).get("command_dispatched") is False:
            name = next(n for n, m in STEP_TOOL_MODES.items() if m == ("rerun" if values["action_mode"] == "search" else values["action_mode"]))
            self.retain_target_rejection(evidence, name, values, result)
        result["target_correction"] = {**recovery, "script_path": script_path, "reason": reason,
                                       "code_reused": True}
        return result

    def snapshot(self):
        return {"available": self.available, "active": self.active,
                "recent_steps": [{k: r[k] for k in ("script_path", "source_version", "state", "action_mode", "stage", "subtask_id") if k in r}
                                 for r in self.records[-3:]],
                "argument_issue": self.argument_issue,
                "support_files": [{"sandbox": sandbox, "path": path}
                    for (sandbox, path), owned in {(r["sandbox"], path): owned for r in self.records
                        for path, owned in r.get("host_input_files", {}).items()
                        if self.helper_module and posixpath.basename(path) == self.helper_module + ".py"}.items()],
                "input_blocker": deepcopy(self.input_blocker),
                "target_recovery": ({"subtask_id": self._target_rejection["contract"]["subtask_id"],
                    "expected_script_path": self._target_rejection["contract"]["artifact"],
                    "actual_script_path": self._target_rejection["arguments"].get("script_path"),
                    "submitted_changes_saved": False, "command_dispatched": False} if self._target_rejection else None),
                "contract": "One question -> saved code -> actual execution -> assessment. "
                    "Choose the next question only after that evidence; future stage goals are not this step."}

    async def run(self, evidence, definitions, invoke, stopped, *, call_id, sandbox,
                  script_path, source, question, expected_output, stop_condition, timeout_seconds=None,
                  previous_assessment=None, edits=None, execution_scope="sample", argv=None,
                  rewrite=False, action_mode="", input_artifacts=None, input_manifest_path=None, repeat_purpose=None, rewrite_reason=None):
        def error(code, message, **details):
            return {"ok": False, "error": {"code": code, "message": message,
                "kind": "step_contract", "command_dispatched": False, **details}}

        if self.active or evidence.pending or evidence.executions or evidence.inline_pending or evidence.skill_operations:
            return error("step_in_progress", "Collect the active execution step before starting another.")
        if not evidence.workflow.active or evidence.workflow.current not in {"probe", "build"}:
            return error("step_phase", "Use this tool only for the current probe/build experiment.")
        for name, value in {"question": question, "expected_output": expected_output,
                            "stop_condition": stop_condition}.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 12000:
                return error("step_field", "Describe one executable question and its measured output/finite stop rule.",
                             field=name, max_chars=12000)
        if not script_target(script_path):
            return error("step_path", "Use ONE literal relative .py target; put reuse instructions in code/input_artifacts.")
        if timeout_seconds is not None and (type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600):
            return error("step_timeout", "Use a finite timeout_seconds between 1 and 3600.")
        if execution_scope not in {"sample", "search"} or (argv is not None and
                (not isinstance(argv, list) or len(argv) > 32 or any(not isinstance(v, str) for v in argv))):
            return error("step_arguments", "Choose sample/search and a list of script arguments.")
        if repeat_purpose is not None and (not isinstance(repeat_purpose, str) or not repeat_purpose.strip()
                                          or len(repeat_purpose) > 400):
            return error("step_arguments", "Describe the intentional repeated measurement in repeat_purpose (1–400 characters).")
        key = (sandbox, posixpath.normpath(script_path))
        latest = evidence.latest_experiment()
        if (latest and evidence.workflow.protocol_issues(evidence)
                and key != (latest.sandbox, latest.path) and not evidence.workflow.host_signal):
            return error("measurement_repair_required", "Repair the current producer's listed measurement fields first. "
                "Use patch_modelling_step on its saved path; keep this protocol repair separate from scientific replanning.",
                issues=evidence.workflow.protocol_issues(evidence))
        from .measurements import relative_path, INPUT_GUARD
        if input_artifacts is not None and (not isinstance(input_artifacts, list) or not 1 <= len(input_artifacts) <= 8
                or any(not isinstance(a, Mapping) or set(a) != {"path"}
                       or not relative_path(a["path"]) for a in input_artifacts)
                or len({a["path"] for a in input_artifacts}) != len(input_artifacts)):
            return error("step_inputs", "Use up to eight distinct HOST artifact paths.")
        inputs = []
        for artifact in input_artifacts or []:
            handoff = evidence.artifact_handoffs.get((sandbox, artifact["path"]))
            if handoff is None:
                return error("step_input_receipt", "No matching produced artifact receipt; read HOST artifact_handoffs before computing.", path=artifact["path"])
            inputs.append({k: deepcopy(v) for k, v in handoff.items() if k != "inspection"})
        if inputs and (not relative_path(input_manifest_path) or not input_manifest_path.endswith(".json")
                       or input_manifest_path in {a["path"] for a in inputs} or input_manifest_path == key[1]):
            return error("step_input_manifest", "Use a relative .json manifest distinct from the inputs and script, and load it in this script.")
        if not inputs and input_manifest_path is not None:
            return error("step_input_manifest", "input_manifest_path requires input_artifacts.")
        if any(a == "--oaw-input-manifest" or a.startswith("--oaw-input-manifest=") for a in argv or []):
            return error("step_arguments", "--oaw-input-manifest is host-owned; use input_manifest_path and parse this flag in your script.")
        input_identity = [{k: deepcopy(a[k]) for k in ("path", "units", "frame", "components", "layout", "command_id", "execution_sequence") if k in a}
                          for a in inputs]
        existing = evidence.scripts.get(key)
        # Old direct callers can still create or supply an actual patch. A
        # missing code change must never silently mean rerun after a failure.
        mode = action_mode or ("search" if execution_scope == "search" else
            "patch" if edits is not None else "rewrite" if rewrite else "new" if not existing else "patch")
        if mode not in {"new", "patch", "rewrite", "rerun", "search"}:
            return error("step_mode", "Choose new, patch, rewrite, rerun or search.")
        if mode == "search":
            execution_scope = "search"
        if repeat_purpose is not None and mode not in {"rerun", "search"}:
            return error("step_arguments", "repeat_purpose is for explicit saved-code execution only, not repairs.")
        if source and edits:
            return error("step_source", "Supply source OR exact edits, not both.")
        if edits is not None and (not isinstance(edits, list) or not edits or any(
                not isinstance(v, Mapping) or set(v) != {"old", "new"} or
                not all(isinstance(v[k], str) for k in ("old", "new")) for v in edits)):
            return error("step_edits", "Supply native exact old/new edits.")
        if not isinstance(source, str) or (not source.strip() and not existing):
            return error("empty_script", "Supply runnable source, or edits/reuse with the saved script path.")
        version = existing.version if existing else 1
        if execution_scope == "search" and (edits or (sandbox, key[1], version) not in self.samples):
            return error("sample_required", "Run a small sample of this exact source successfully before its search. "
                         "Use script arguments to select sample/search; preserve the same code and saved outputs.")
        sample_argv = self.sample_argv.get((sandbox, key[1], version), [])
        if execution_scope == "search" and (not argv or list(argv) == sample_argv):
            return error("step_search_arguments", "Search must supply explicit argv different from the sampled execution. "
                "execution_scope labels and prose do not change script defaults; no command ran.", sample_argv=sample_argv)
        if mode == "new" and (existing or not source.strip() or edits is not None):
            return error("step_mode", "new needs source for a new artifact; use patch or explicit rewrite for existing source.")
        if mode == "patch" and (not existing or source.strip() or not edits):
            return error("step_patch_required", "A repair requires action_mode=patch, edits=[{old,new}]. "
                "No patch was supplied; no command ran.", source_context=evidence._current_source_head("structure_builder"))
        if mode == "rewrite" and (not existing or not source.strip() or edits is not None
                                  ):
            return error("step_revision", "Explicit rewrite needs full source for the saved path.")
        if mode in {"rerun", "search"} and (not existing or source.strip() or edits is not None):
            return error("step_mode", "rerun/search reuse saved bytes; omit source/edits.")
        if mode == "patch" and any(not v["old"] or v["old"] == v["new"] for v in edits):
            return error("step_no_change", "Patch old/new must describe a real change; no command ran.")
        if mode == "patch" and key in evidence.script_sources:
            from open_agent_world.plugin_api.sandbox import apply_text_edits
            from open_agent_world.plugin_api.sandbox import SandboxValidationError
            try:
                preview = apply_text_edits(evidence.script_sources[key].encode(), edits)
            except (ValueError, SandboxValidationError) as exc:
                return error("step_edits", str(exc))
            if preview.decode() == evidence.script_sources.get(key):
                return error("step_no_change", "The edits cancel out; no source change or command occurred.")
        if mode == "rewrite" and source == evidence.script_sources.get(key):
            return error("step_no_change", "Rewrite supplied identical bytes; choose a real patch or an explicit rerun.")
        if mode == "rerun" and not repeat_purpose and existing.exit_code not in {None, 0}:
            last = evidence.last_execution_inputs.get(key, {})
            if (list(argv or []) == last.get("argv", []) and
                    last.get("environment_revision", 0) == evidence.environment_revisions.get(sandbox, 0)
                    and input_identity == last.get("input_artifacts", [])):
                return error("step_unchanged_failure", "The failed source, arguments and observed environment are unchanged. "
                    "Patch the failure, change explicit experiment inputs, or request Planner review; no command ran.")
        repair = evidence.workflow.local_repair(evidence) if self.available else None
        if repair and (key != (repair["sandbox"], repair["script_path"]) or mode not in {"patch", "rewrite", "rerun"}
                or mode == "rewrite" and (not isinstance(rewrite_reason, str) or not rewrite_reason.strip())):
            return error("local_repair_required", repair["next_action"], repair=repair)
        operations = {evidence.semantic_tool(d.name): d for d in definitions}
        if not {"write_sandbox_text_file", "execute_command"} <= operations.keys():
            return error("step_capabilities", "Connect the native Sandbox write and execute capabilities.")
        reused_source = evidence.script_sources.get(key) if not source.strip() and not edits else None
        if not source.strip() and not edits and not reused_source:
            return error("step_source_unavailable", "Supply source or exact edits; this Run has no confirmed source to reuse.")
        prior = evidence.current_experiment()
        assessment_reused = prior["assessment_status"] == "recorded"
        if previous_assessment is not None:
            next_action = previous_assessment.get("next_experiment") if isinstance(previous_assessment, Mapping) else None
            if (isinstance(next_action, Mapping) and next_action.get("artifact") is not None
                    and next_action["artifact"] != script_path):
                # Assessing can schedule the next subtask. Check its target
                # before committing that decision or overwriting saved code.
                return error("step_assessment_target", "In a combined assessment+execution, next_experiment describes "
                    "THIS submitted action. Match its artifact to script_path; choose any later experiment only after "
                    "this result. Correct previous_assessment, retaining the unexecuted code.",
                    fields=["previous_assessment"], expected_script_path=script_path,
                    submitted_changes_saved=False)
            assessed = evidence.workflow.assess_previous(evidence, previous_assessment, reuse_recorded=True)
            if not assessed.get("ok"):
                return assessed
            if assessed.get("probe_completed") and assessed.get("checkpoint_status") == "completed":
                build = evidence.workflow.pending_experiment or {}
                if mode != "new" or script_path != build.get("artifact"):
                    return {"ok": True, "checkpoint_result": assessed["checkpoint_result"],
                        "next_stage": evidence.workflow.current, "command_dispatched": False,
                        "submitted_changes_saved": False,
                        "next_action": "The input checkpoint is complete. No proposed extra probe was saved or executed. "
                            "Use the committed build experiment to construct the first candidate from retained inputs."}
        if evidence.workflow.current not in {"probe", "build"}:
            return error("step_phase", "Checkpoint advanced; use the requested delivery tools instead of another experiment.")
        dependency_error = evidence.workflow.validate_dependencies(evidence.workflow.pending_experiment or {}, evidence, sandbox)
        if dependency_error:
            return dependency_error
        # The same receipt gates apply to the composite and ordinary tool path.
        denied = evidence.workflow.before_tool("structure_builder", "run_modelling_step", {}, evidence)
        if denied:
            return denied
        denied = evidence.workflow.before_tool("structure_builder", "run_modelling_step", {"script_path": script_path}, evidence)
        if denied:
            retained_args = dict(sandbox=sandbox, script_path=script_path, source=source, question=question,
                expected_output=expected_output, stop_condition=stop_condition, timeout_seconds=timeout_seconds,
                previous_assessment=previous_assessment, edits=edits,
                execution_scope=execution_scope, argv=argv, rewrite=rewrite, action_mode=mode,
                input_artifacts=input_artifacts, input_manifest_path=input_manifest_path, repeat_purpose=repeat_purpose,
                rewrite_reason=rewrite_reason)
            name = next(n for n, m in STEP_TOOL_MODES.items() if m == ("rerun" if mode == "search" else mode))
            self.retain_target_rejection(evidence, name, retained_args, denied)
            return denied
        planned = evidence.workflow.pending_experiment
        if planned and planned.get("input_paths") and not set(planned["input_paths"]) <= {a["path"] for a in inputs}:
            return error("planned_inputs_required", "Consume the reviewed experiment's input_paths from the HOST handoffs; "
                "do not recompute retained inputs.", required_input_paths=planned["input_paths"])
        identity = json.dumps([sandbox, key[1], version, list(argv or []), input_identity,
            evidence.environment_revisions.get(sandbox, 0)], sort_keys=True, ensure_ascii=False)
        guard_identity = json.dumps([sandbox, input_identity,
            evidence.environment_revisions.get(sandbox, 0)], sort_keys=True, ensure_ascii=False)
        self.input_blocker = None
        if inputs and guard_identity in self.input_rejections:
            self.input_blocker = deepcopy(self.input_rejections[guard_identity])
            self.input_blocker.update(plan_revision=evidence.workflow.plan_revision, stage=evidence.workflow.current,
                subtask_id=(evidence.workflow.pending_experiment or {}).get("subtask_id"), script_path=key[1])
            return error("step_input_changed", "This exact input/layout/guard/environment already failed its native check. "
                "Use the retained diagnosis; report blocked rather than resubmit code or request scientific review.",
                reused_input_check=True, input_check_dispatched=False, **deepcopy(self.input_blocker))
        cached = self.completed_results.get(identity)
        if mode in {"rerun", "search"} and not repeat_purpose and cached:
            return {"ok": cached["exit_code"] == 0, "reused_execution": True, "new_execution": False,
                    "no_new_evidence": True, **deepcopy(cached),
                    "next_action": "The same saved source, semantic argv, input artifacts and environment were already executed. "
                        "Use this receipt; change explicit inputs/code for new evidence, or supply repeat_purpose for an intentional repeat."}
        hooks = evidence.tool_callbacks("structure_builder")
        self.active = True
        self.argument_issue = None
        self.argument_failures = 0
        self._target_rejection = None
        record = {"question": question, "expected_output": expected_output, "stop_condition": stop_condition,
                  "sandbox": sandbox, "script_path": posixpath.normpath(script_path),
                  "source_version": version, "execution_scope": execution_scope, "action_mode": mode,
                  "plan_revision": evidence.workflow.plan_revision, "stage": evidence.workflow.current,
                  "previous_execution_sequence": prior.get("execution_sequence"), "assessment_reused": assessment_reused,
                  "input_artifacts": inputs, "input_manifest_path": input_manifest_path,
                  "subtask_id": planned.get("subtask_id") if planned else next((r["subtask_id"] for r in reversed(self.records)
                      if mode != "new" and r.get("sandbox") == sandbox and r.get("script_path") == key[1]
                      and r.get("stage") == evidence.workflow.current and r.get("plan_revision") == evidence.workflow.plan_revision
                      and r.get("subtask_id")), f"{evidence.workflow.plan_revision}:{evidence.workflow.current}:{call_id}"),
                  "semantic_argv": list(argv or []), "repeat_purpose": repeat_purpose,
                  "rewrite_reason": rewrite_reason,
                  "state": "started"}
        self.records.append(record)

        async def operation(name, args):
            if stopped.is_set():
                raise asyncio.CancelledError()
            definition = operations[name]
            tool = SimpleNamespace(name=definition.name)
            context = SimpleNamespace(function_call_id=f"{call_id}:{name}", native_invocation_call_id=call_id)
            if name == "execute_command":
                evidence._verified_execution_inputs[context.function_call_id] = {"input_artifacts": deepcopy(input_identity),
                    "argv": list(argv or []), "repeat_purpose": repeat_purpose}
            try:
                rejected = await hooks["before_tool_callback"](tool, args, context)
            finally:
                evidence._verified_execution_inputs.pop(context.function_call_id, None)
            if rejected is not None:
                return rejected
            response = (await execute_tool(lambda: invoke(definition.capability_id, args))).response
            # Real receipt and live authorization come from OAW. These callbacks
            # observe that result; they never invent function exchanges/events.
            await hooks["after_tool_callback"](tool, args, context, response)
            return response

        try:
            if inputs or self.helper_module:
                # Invoke existing authorized operations without recording the
                # host read-only guard as a model-produced experiment.
                async def native(name, args):
                    if stopped.is_set():
                        raise asyncio.CancelledError()
                    return (await execute_tool(lambda: invoke(operations[name].capability_id, args))).response
                from uuid import uuid4
                if self.guard_path is None:
                    self.guard_path = ".oaw-input-check-" + uuid4().hex + ".py"
                guard_path = self.guard_path
                # Reuse the same native ownership/file boundary for the guard
                # and the final, inspected manifest. Inspection precedes the
                # manifest so the consumer receives the current byte metadata.
                confirmed = {path: owned for previous in self.records[:-1] if previous["sandbox"] == sandbox
                             for path, owned in previous.get("host_input_files", {}).items()}
                async def save_input(path, content):
                    args = {"sandbox": sandbox, "path": path, "content": content,
                            "overwrite": path in confirmed}
                    saved_input = await native("write_sandbox_text_file", args)
                    payload = _payload(saved_input)
                    if payload.get("written") != len(content.encode()):
                        record["state"] = "input_save_failed"
                        return error("step_input_save", "Input guard/manifest save was not confirmed; no modelling command ran.", receipt=saved_input)
                    record.setdefault("host_input_files", {})[path] = True
                    return None
                if self.helper_module:
                    from pathlib import Path
                    # Python puts the invoked script's directory on sys.path,
                    # not necessarily its workspace cwd. No source/env rewrite.
                    helper_path = posixpath.join(posixpath.dirname(key[1]), self.helper_module + ".py")
                    helper_source = Path(__file__).with_name("modelling_io.py").read_text(encoding="utf-8")
                    if helper_path not in confirmed:
                        rejected = await save_input(helper_path, helper_source)
                        if rejected:
                            return rejected
                    else:
                        record.setdefault("host_input_files", {})[helper_path] = confirmed[helper_path]
            if inputs:
                rejected = await save_input(guard_path, INPUT_GUARD)
                if rejected:
                    return rejected
                guarded = await native("execute_command", {"sandbox": sandbox,
                    "argv": ["python3", guard_path, json.dumps(inputs, ensure_ascii=False, sort_keys=True)]})
                checked = _payload(guarded)
                if type(checked.get("exit_code")) is not int:
                    record["state"] = "input_check_unknown"
                    evidence.executions[f"{call_id}:input_check_unknown"] = []
                    return error("step_input_unknown", "Collect the native input check before retrying; no modelling command ran.",
                                 kind="execution_unknown", command_dispatched=None, input_check=guarded)
                try:
                    guard_report = json.loads(checked.get("stdout", ""))
                    matched = guard_report.get("oaw_inputs_verified") == inputs
                except (ValueError, AttributeError, TypeError):
                    matched = False
                if checked["exit_code"] != 0 or not matched:
                    record["state"] = "input_rejected"
                    try:
                        diagnosis = json.loads(checked.get("stdout", "")).get("oaw_input_error")
                    except (ValueError, AttributeError):
                        diagnosis = None
                    if isinstance(diagnosis, dict):
                        self.input_blocker = {"input_check": deepcopy(guarded), "diagnosis": diagnosis,
                            "plan_revision": evidence.workflow.plan_revision, "stage": evidence.workflow.current,
                            "subtask_id": record["subtask_id"], "script_path": record["script_path"],
                            "sandbox": sandbox, "input_identity": deepcopy(input_identity),
                            "environment_revision": evidence.environment_revisions.get(sandbox, 0),
                            "modelling_command_dispatched": False}
                        # A layout failure is deterministic for these verified
                        # bytes. A missing file could be externally
                        # repaired, so never cache it as an unchanged layout.
                        if str(diagnosis.get("field", "")).startswith("layout."):
                            self.input_rejections[guard_identity] = deepcopy(self.input_blocker)
                    return error("step_input_changed", "Input bytes or declared data layout failed the native check; inspect them before modelling.",
                                 input_check_dispatched=True, input_check=guarded, diagnosis=diagnosis,
                                 next_action="Use the exact guard diagnosis. If the producer receipt cannot be repaired safely, finish_task status=blocked; this is not new scientific evidence.")
                metadata = guard_report.get("oaw_input_metadata")
                if metadata is not None:
                    expected = {a["path"] for a in inputs}
                    if (not isinstance(metadata, list) or len(metadata) != len(inputs)
                            or any(not isinstance(m, Mapping) or not isinstance(m.get("path"), str) for m in metadata)
                            or {m["path"] for m in metadata} != expected):
                        record["state"] = "input_metadata_rejected"
                        return error("step_input_metadata", "Native metadata did not cover the verified input paths; no modelling command ran.")
                    record["input_metadata"] = deepcopy(metadata)
                    for artifact in inputs:
                        description = next(m for m in metadata if m["path"] == artifact["path"])
                        artifact["inspection"] = deepcopy(description)
                        evidence.artifact_handoffs[(sandbox, artifact["path"])]["inspection"] = deepcopy(description)
                rejected = await save_input(input_manifest_path, json.dumps(inputs, ensure_ascii=False, sort_keys=True))
                if rejected:
                    return rejected
                record["input_check"] = {"command_id": checked.get("command_id"), "exit_code": 0,
                                         "source": "native Sandbox file inspection"}
            if mode in {"rerun", "search"}:
                saved = {"reused_saved_file": True, "path": record["script_path"]}
            else:
                save_args = {"sandbox": sandbox, "path": record["script_path"], "overwrite": existing is not None}
                if edits:
                    save_args["edits"] = edits
                else:
                    save_args["content"] = source
                saved = await operation("write_sandbox_text_file", save_args)
                receipt = _payload(saved)
                if (not isinstance(receipt, Mapping) or type(receipt.get("written")) is not int
                        or receipt["written"] <= 0):
                    record["state"] = "save_rejected" if definite_save_rejection(saved) else "save_failed"
                    return error("step_save_failed", "Script save was not confirmed; no command dispatched.",
                                 kind="native_dependency", save=saved)
            record["state"] = "saved"
            current = evidence.scripts.get(key)
            record["source_version"] = current.version if current else version
            record["bytes_changed"] = existing is None or record["source_version"] != existing.version
            if mode in {"patch", "rewrite"} and not record["bytes_changed"]:
                record["state"] = "unchanged"
                return error("step_no_change", "The submitted edit did not change the saved source.", save=saved)
            args = {"sandbox": sandbox, "argv": ["python3", record["script_path"], *(argv or [])]}
            if inputs:
                args["argv"].extend(["--oaw-input-manifest", input_manifest_path])
            record["native_argv"] = list(args["argv"])
            execute = operations["execute_command"]
            if timeout_seconds is not None:
                if "timeout_seconds" not in evidence.tool_fields.get(execute.name, set()):
                    record["state"] = "execution_not_started"
                    return error("step_timeout_unavailable", "Native execute tool does not expose timeout_seconds; saved source retained.")
                args["timeout_seconds"] = timeout_seconds
            result = await operation("execute_command", args)
            rejection = result.get("error") if isinstance(result, Mapping) else None
            if isinstance(rejection, Mapping) and rejection.get("command_dispatched") is False:
                # A definite host refusal is not an unknown native execution.
                # Source was saved, but no command exists to wait for/replay.
                record["state"] = "execution_not_started"
                evidence.notice("structure_builder", "Saved step execution was rejected before dispatch",
                                workflow_event="modelling_step", **record)
                return {"ok": False, "step": dict(record), "save": saved, "execution": result,
                        "error": dict(rejection), "submitted_changes_saved": True,
                        "next_action": "Resolve the reported execution-field rejection using the saved source. "
                                       "No command was dispatched; do not wait for an unknown execution."}
            # invoke uses the normal native operation collection path. An absent
            # waiter or a denial preserves running/unknown, never implies exit 0.
            execution = _payload(result)
            record["state"] = ("finished" if isinstance(execution, Mapping) and type(execution.get("exit_code")) is int
                               else "running" if isinstance(execution, Mapping) and execution.get("status") == "running"
                               else "execution_unknown")
            record["exit_code"] = execution.get("exit_code") if isinstance(execution, Mapping) else None
            record["command_id"] = execution.get("command_id") if isinstance(execution, Mapping) else None
            if record["state"] == "finished" and record["exit_code"] == 0 and execution_scope == "sample":
                self.samples.add((sandbox, record["script_path"], record["source_version"]))
                self.sample_argv[(sandbox, record["script_path"], record["source_version"])] = list(argv or [])
            if record["state"] == "finished":
                completed_identity = json.dumps([sandbox, key[1], record["source_version"], list(argv or []),
                    input_identity, evidence.environment_revisions.get(sandbox, 0)], sort_keys=True, ensure_ascii=False)
                self.completed_results[completed_identity] = {"exit_code": record["exit_code"],
                    "command_id": record["command_id"], "script_path": key[1], "source_version": record["source_version"],
                    "semantic_argv": list(argv or []), "execution_scope": execution_scope,
                    "execution_sequence": evidence.execution_sequence,
                    "measurements": deepcopy(evidence.measurement_reports.get(evidence.execution_sequence))}
                pending = evidence.workflow.pending_experiment
                if pending and pending["plan_revision"] == evidence.workflow.plan_revision and (pending.get("origin") == "stage" or pending["artifact"] == key[1]):
                    evidence.workflow.pending_experiment = None
            evidence.notice("structure_builder", "Measured execution step returned; assess before the next question",
                            workflow_event="modelling_step", **record)
            producer_hint = ""
            if record["state"] == "finished" and record["exit_code"] == 0:
                produced = sorted(path for (box, path), row in evidence.artifact_handoffs.items()
                                  if box == sandbox and row.get("command_id") == record["command_id"])
                wanted = sorted({path for stage in evidence.workflow.plan or []
                                 for path in ((stage.get("experiment") or {}).get("input_paths") or [])})
                unreached = [path for path in wanted
                             if (sandbox, path) not in evidence.artifact_handoffs and path not in produced]
                if wanted and not produced and unreached:
                    # The measured deadlock: a later subtask plans to consume
                    # this script's outputs while no artifact receipt exists.
                    # Say it now, at the cheapest moment, not 20 rejections later.
                    producer_hint = (" NOTE: later planned subtasks declare inputs (" + ", ".join(unreached[:4]) +
                                     ") that have no artifact receipts yet, and this execution registered none. "
                                     "Rerun this producer once emitting oaw_measurements artifact records for the "
                                     "files it writes (modelling_io emit_measurements/artifact), or the consuming "
                                     "subtask will be rejected with subtask_inputs.")
            answer = {"ok": record["state"] in {"finished", "running"} and record["exit_code"] in {None, 0},
                      "assessment_reused": assessment_reused,
                      "exit_code": record["exit_code"], "step": dict(record), "save": saved, "execution": result,
                      "measurements": deepcopy(evidence.measurement_reports.get(evidence.execution_sequence)),
                      "measurement_issues": deepcopy(evidence.measurement_issues.get(evidence.execution_sequence, [])),
                      "next_action": ("Collect running/unknown execution first. For a justified next code action, include one brief "
                                     "previous_assessment in that next step. Use standalone observe only for a stage decision, review or validation. Once recorded, omit previous_assessment "
                                     "and execute the next bounded patch/experiment using the stored receipt. "
                                     "A completed step does not prove the stage or task is complete. At a measured subgoal boundary, "
                                     "assess with outcome=continue, then finish_task status=subtask_complete with one next_experiment "
                                     "to continue in a fresh delegation. Keep local repairs in this delegation." + producer_hint)}
            if record["state"] == "execution_unknown":
                # A returned tool error is not a command termination receipt.
                # Keep uncertainty explicit rather than silently resubmitting.
                evidence.executions[f"{call_id}:unconfirmed_step"] = [
                    ((sandbox, record["script_path"]), record["source_version"])]
                answer["error"] = {"code": "step_execution_unconfirmed", "message":
                    "No finished/running command receipt was confirmed. Inspect native Sandbox state; do not resubmit blindly.",
                    "kind": "execution_unknown", "command_dispatched": None}
            return answer
        except BaseException:
            record["state"] = "interrupted"
            raise
        finally:
            self.active = False


def source_reader_function(evidence):
    async def read_modelling_source(sandbox: str, script_path: str,
                                    offset: int = 0, limit: int = 4000) -> dict:
        """Read a native-confirmed source snapshot from this Run. Offset/limit are characters; no command runs."""
        key = (sandbox, script_path)
        item, source = evidence.scripts.get(key), evidence.script_sources.get(key)
        if not item or source is None:
            available = sorted(path for (box, path), snap in evidence.script_sources.items()
                               if snap and box == sandbox)
            hint = ("This Run has no native-confirmed sources in that Sandbox yet."
                    if not available else
                    "Native-confirmed sources in this Sandbox: " + ", ".join(available) + ".")
            if script_path.startswith("_oaw_modelling_"):
                hint += (" The _oaw_modelling_* module is host staging machinery: import its documented API "
                         "(load_inputs/emit_measurements/write_extxyz); its source is not a modelling artifact.")
            return {"ok": False, "error": {"code": "source_snapshot",
                "message": "Use the path of an available native-confirmed source in this Run. " + hint,
                "available_paths": available}}
        # Match the existing OAW archived tool-result reader's paging limit.
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            return {"ok": False, "error": {"code": "source_range", "message": "Use nonnegative offset and limit 1..8000 characters."}}
        end = min(len(source), offset + limit)
        return {"ok": True, "sandbox": sandbox, "path": script_path,
                "source": "last native-confirmed saved snapshot, not a live disk read", "text": source[offset:end],
                "offset": offset, "total_chars": len(source), "next_offset": end if end < len(source) else None}
    return read_modelling_source


def typed_step_functions(evidence, definitions, invoke, stopped):
    async def dispatch(name, context, values):
        rejected = argument_error(name, values)
        if rejected:
            evidence.steps.retain_target_rejection(evidence, name, values, rejected)
            return rejected
        original = deepcopy(values)
        mode = STEP_TOOL_MODES[name]
        values.pop("action_mode", None)
        if mode == "rerun" and values.get("execution_scope") == "search":
            mode = "search"
        result = await evidence.steps.run(evidence, definitions, invoke, stopped,
            call_id=context.function_call_id, source=values.pop("source", ""), action_mode=mode, **values)
        evidence.steps.retain_target_rejection(evidence, name, original, result)
        return result

    async def run_modelling_step(sandbox: str, script_path: str, source: str, question: str,
                                expected_output: str, stop_condition: str, tool_context,
                                timeout_seconds: int | None = None, previous_assessment: dict | None = None,
                                argv: list[str] | None = None, execution_scope: str = "sample", action_mode: str = "new",
                                input_artifacts: list[dict] | None = None, input_manifest_path: str | None = None) -> dict:
        """Create and execute ONE new Python script. source is REQUIRED in tool JSON; never put code only in thoughts."""
        return await dispatch("run_modelling_step", tool_context, dict(sandbox=sandbox, script_path=script_path,
            source=source, question=question, expected_output=expected_output, stop_condition=stop_condition,
            timeout_seconds=timeout_seconds, previous_assessment=previous_assessment, argv=argv,
            execution_scope=execution_scope, action_mode=action_mode,
            input_artifacts=input_artifacts, input_manifest_path=input_manifest_path))

    async def patch_modelling_step(sandbox: str, script_path: str, edits: list[dict[str, str]],
                                  question: str, expected_output: str, stop_condition: str, tool_context,
                                  timeout_seconds: int | None = None, previous_assessment: dict | None = None,
                                  argv: list[str] | None = None, execution_scope: str = "sample",
                                  input_artifacts: list[dict] | None = None, input_manifest_path: str | None = None) -> dict:
        """Apply REQUIRED old/new edits to the saved file, then execute ONE small repaired experiment."""
        return await dispatch("patch_modelling_step", tool_context, dict(sandbox=sandbox, script_path=script_path,
            edits=edits, question=question, expected_output=expected_output,
            stop_condition=stop_condition, timeout_seconds=timeout_seconds, previous_assessment=previous_assessment,
            argv=argv, execution_scope=execution_scope,
            input_artifacts=input_artifacts, input_manifest_path=input_manifest_path))

    async def rewrite_modelling_step(sandbox: str, script_path: str, source: str,
                                    question: str, expected_output: str, stop_condition: str, tool_context,
                                    timeout_seconds: int | None = None, previous_assessment: dict | None = None,
                                    argv: list[str] | None = None, execution_scope: str = "sample",
                                    input_artifacts: list[dict] | None = None, input_manifest_path: str | None = None,
                                    rewrite_reason: str | None = None) -> dict:
        """Explicitly replace a saved script with REQUIRED full source, then execute it. Prefer patch for repairs."""
        return await dispatch("rewrite_modelling_step", tool_context, dict(sandbox=sandbox, script_path=script_path,
            source=source, question=question, expected_output=expected_output,
            stop_condition=stop_condition, timeout_seconds=timeout_seconds, previous_assessment=previous_assessment,
            argv=argv, execution_scope=execution_scope,
            input_artifacts=input_artifacts, input_manifest_path=input_manifest_path, rewrite_reason=rewrite_reason))

    async def rerun_modelling_step(sandbox: str, script_path: str, question: str,
                                  expected_output: str, stop_condition: str, tool_context,
                                  timeout_seconds: int | None = None, previous_assessment: dict | None = None,
                                  argv: list[str] | None = None, execution_scope: str = "sample",
                                  input_artifacts: list[dict] | None = None, input_manifest_path: str | None = None,
                                  repeat_purpose: str | None = None) -> dict:
        """Execute saved bytes with explicit inputs; search needs a successful sample. Never use for repairs."""
        return await dispatch("rerun_modelling_step", tool_context, dict(sandbox=sandbox, script_path=script_path,
            question=question, expected_output=expected_output,
            stop_condition=stop_condition, timeout_seconds=timeout_seconds, previous_assessment=previous_assessment,
            argv=argv, execution_scope=execution_scope,
            input_artifacts=input_artifacts, input_manifest_path=input_manifest_path, repeat_purpose=repeat_purpose))

    async def resume_modelling_step(subtask_id: str, script_path: str,
                                   reason: str, tool_context, corrections: dict | None = None) -> dict:
        """Correct rejected execution fields and execute the retained action without resending its code."""
        return await evidence.steps.resume(evidence, definitions, invoke, stopped,
            call_id=tool_context.function_call_id, subtask_id=subtask_id,
            script_path=script_path, reason=reason, corrections=corrections)

    return (run_modelling_step, patch_modelling_step, rewrite_modelling_step, rerun_modelling_step, resume_modelling_step)
