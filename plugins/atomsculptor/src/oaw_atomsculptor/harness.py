"""Run-local evidence and limits. Tool success alone is not task completion."""
from __future__ import annotations

import json
import posixpath
import time
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from open_agent_world.plugin_api import AgentRuntimeError
from .workflow import ModellingWorkflow, PLAN_LIMITS, PLAN_REJECTION_LIMIT
from .actions import ExecutionActions, READ_DEPENDENCIES, inline_python
from open_agent_world.plugin_api.model_runtime import RequestRetryPolicy
from open_agent_world.plugin_api.tool_execution import tool_payload as _payload


class HarnessStopped(AgentRuntimeError):
    harness_stopped = True


class HarnessBudgetReached(HarnessStopped):
    budget_closeout = True


class HarnessLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_model_requests: int | None = Field(default=None, ge=4, le=256,
        description="Optional total request budget; null disables the former default 32-call cutoff.")
    # None inherits OAW's selected model output limit; role caps are opt-in.
    specialist_output_tokens: int | None = Field(default=None, ge=1024, le=262144)
    coordinator_output_tokens: int = Field(default=4096, ge=512, le=65536)
    max_run_seconds: int | None = Field(default=None, ge=30, le=14400,
        description="Total Run wall-clock limit; null disables it.")
    closeout_reserve_seconds: int = Field(default=180, ge=0, le=1800)
    unexecuted_write_limit: int = Field(default=2, ge=2, le=10)
    repeated_error_limit: int = Field(default=3, ge=2, le=10)
    # A hard request ceiling, not a measure of whether reasoning is stalled.
    # OAW's shared first-content/idle watchdog owns stream health detection.
    model_action_seconds: int = Field(default=600, ge=1, le=3600)
    action_recovery_seconds: int = Field(default=600, ge=1, le=3600)
    request_recovery: RequestRetryPolicy = Field(default_factory=RequestRetryPolicy,
        description="Native OAW model-request retry policy; recover only unpublished responses, never replay completed tools.")
    script_execution_seconds: int = Field(default=120, ge=1, le=3600)
    recovered_action_output_tokens: int | None = Field(default=None, ge=1024, le=262144)
    model_requests_without_progress: int | None = Field(default=None, ge=2, le=20,
        description="Optional no-progress request brake; null disables it. The former persisted default 6 migrates to null.")
    working_history_chars: int = Field(default=48000, ge=8000, le=256000)
    inline_probe_limit: int = Field(default=2, ge=1, le=6)
    inline_probe_chars: int = Field(default=1600, ge=128, le=8192)
    # Working-memory knobs from the 2026-10 architecture review. Defaults keep
    # established behaviour except the bounded reasoning tail, which replaces
    # "drop every unsigned thought" with "keep a bounded recent tail".
    retained_thought_chars: int = Field(default=4096, ge=0, le=32768,
        description="Chars of a specialist's own unsigned reasoning kept between its consecutive requests; 0 restores full omission.")

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_defaults(cls, value):
        # OAW serializes defaults into existing Agent cards. Updating only
        # Field defaults would leave those cards on the rejected 60/30 policy.
        # Upgrade that exact legacy pair; preserve other custom ceilings and
        # validate their types/ranges normally. Do not mutate caller config.
        if not isinstance(value, Mapping):
            return value
        upgraded = dict(value)
        if type(value.get("max_model_requests")) is int and value["max_model_requests"] == 32:
            upgraded["max_model_requests"] = None
        # Serialized defaults mark every field as explicitly set. Do not
        # mistake the old six-request default for a user-selected brake.
        if type(value.get("model_requests_without_progress")) is int and value["model_requests_without_progress"] == 6:
            upgraded["model_requests_without_progress"] = None
        # Persisted cards carry the former 20-minute default. Retire that
        # policy on validation too, without directly editing stored cards.
        if type(value.get("max_run_seconds")) is int and value["max_run_seconds"] == 1200:
            upgraded["max_run_seconds"] = None
        if (type(value.get("model_action_seconds")) is int
                and type(value.get("action_recovery_seconds")) is int
                and value["model_action_seconds"] == 60 and value["action_recovery_seconds"] == 30):
            upgraded.update(model_action_seconds=600, action_recovery_seconds=600)
        # Cards persist the old defaults. Migrate the exact old output policy
        # together, not a separately customized specialist/Run/recovery cap.
        if (type(value.get("max_output_tokens")) is int and value["max_output_tokens"] == 65536
                and type(value.get("specialist_output_tokens")) is int and value["specialist_output_tokens"] == 16384
                and ("recovered_action_output_tokens" not in value or
                     type(value.get("recovered_action_output_tokens")) is int and value["recovered_action_output_tokens"] == 4096)):
            upgraded.update(specialist_output_tokens=None,
                            recovered_action_output_tokens=None)
        for field in ("max_output_tokens", "closeout_reserve_tokens", "incremental_edit_hint"):
            upgraded.pop(field, None)
        return upgraded


@dataclass
class ScriptEvidence:
    sandbox: str
    path: str
    version: int = 1
    unexecuted_writes: int = 1
    phase: str = "written"
    command_id: str | None = None
    exit_code: int | None = None
    execution_sequence: int = 0


class RunEvidence:
    def __init__(self, trace: Any, original_request: str, limits: HarnessLimits | None = None,
                 selection_snapshot: Any = None, *, direct_builder: bool = False):
        self.trace = trace
        self.original_request = original_request
        self.selection_snapshot = deepcopy(selection_snapshot or [])
        self.limits = limits or HarnessLimits()
        self.direct_builder = direct_builder
        self.started = time.monotonic()
        self.model_requests = 0
        self.closing_reason = None
        self.script_sources = {}
        self.current_script_key = None
        self.output_tokens = 0
        self.output_recoveries: dict[str, int] = {}
        self.workflow = ModellingWorkflow(original_request, direct=direct_builder)
        self.actions = ExecutionActions()
        from .execution_steps import ExecutionSteps
        self.steps = ExecutionSteps()
        self.tool_semantics: dict[str, str] = {}
        self.tool_fields: dict[str, set[str]] = {}
        self.execution_sequence = 0
        self.execution_inputs = {}
        self.last_execution_inputs = {}
        self._verified_execution_inputs = {}
        self.environment_revisions = {}
        self.measured_objectives = {}
        self.measurement_reports = {}
        self.measurement_issues = {}
        self.execution_receipts = {}
        self._context_role = "structure_builder"
        self._rejected_actions = {}
        self._reference_cache = None
        self._source_reference_cache = {}
        self.artifact_handoffs = {}
        self.execution_observations: dict[tuple[str, str], str] = {}
        self.experiment_records: dict[tuple[int, int], dict[str, Any]] = {}
        self.scripts: dict[tuple[str, str], ScriptEvidence] = {}
        self.denied_writes: dict[tuple[str, str], int] = {}
        self.executions: dict[str, list[tuple[tuple[str, str], str]]] = {}
        self.pending: dict[str, list[tuple[tuple[str, str], str]]] = {}
        self.inline_executions: dict[str, dict[str, Any]] = {}
        self.inline_source_versions: dict[tuple[str, str], int] = {}
        self.inline_pending: dict[str, dict[str, Any]] = {}
        self._host_evidence_texts: set[str] = set()
        self._host_static_texts: set[str] = set()
        self._working_context_texts: set[str] = set()
        self.skill_operations: dict[str, Mapping[str, Any]] = {}
        self.outputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.receipt_issues: dict[tuple[str, str], dict[str, Any]] = {}
        self.sandbox_environments: dict[str, dict[str, Any]] = {}
        self.delivery_uncertainty = {}
        self.delivery_issue = None
        self.imports: dict[str, Any] = {}
        self.import_details: dict[str, Mapping[str, Any]] = {}
        self.confirmed: dict[str, Any] = {}
        self.failures: dict[tuple[str, str, str], tuple[str, int]] = {}
        self.correction_feedback: dict[str, Any] | None = None
        self.block_reason: str | None = None

    @property
    def experiment_history(self) -> list[dict[str, Any]]:
        """Chronological view of the canonical native execution assessments."""
        return list(self.experiment_records.values())

    @property
    def experiment_observations(self) -> dict[int, dict[str, Any]]:
        """Latest assessment per execution; prior plan assessments stay auditable."""
        return {row["execution_sequence"]: row for row in self.experiment_records.values()}

    @property
    def remaining_seconds(self) -> float | None:
        if self.limits.max_run_seconds is None:
            return None
        return max(0.001, self.limits.max_run_seconds - (time.monotonic() - self.started))

    def notice(self, role: str, text: str, **details: Any) -> None:
        self.trace.queue.put_nowait(("progress", {
            "kind": "status", "role": role, "text": text,
            **details,
        }))

    def stop(self, reason: str) -> None:
        raise HarnessStopped(f"AtomSculptor stopped before completion: {reason}. "
                                "Inspect Sandbox files and document state before retrying; "
                                "successful writes do not prove execution or validation.")

    def record_rejection(self, role, name, args, rejected):
        """Detect a confirmed unchanged protocol loop after feedback was sent.

        Changed fields or host state remain repairable. Pending native work is
        never treated as a protocol loop, and no request/time count is involved.
        """
        error = rejected.get("error", {})
        if (not isinstance(error, Mapping) or error.get("kind") not in {"argument_validation", "workflow_state", "step_contract"}
                or self.pending or self.executions or self.inline_pending or self.skill_operations):
            return
        state = {"role": role, "tool": name, "arguments": args, "error": error.get("code"),
                 "plan_revision": self.workflow.plan_revision, "stage": self.workflow.current,
                 "execution_sequence": self.execution_sequence, "environment": self.environment_revisions,
                 "artifacts": [(k, v.get("execution_sequence")) for k, v in self.artifact_handoffs.items()]}
        fingerprint = json.dumps(state, sort_keys=True, default=str)
        previous = self._rejected_actions.get(fingerprint)
        if previous and previous["feedback_offered"]:
            self.block_reason = f"unchanged rejected {name} action after corrective feedback ({error.get('code')}); no native work dispatched"
            self.notice(role, self.block_reason, workflow_event="protocol_repair_stalled", error_code=error.get("code"))
        self._rejected_actions[fingerprint] = {"role": role, "feedback_offered": False}

    def closeout_checkpoint(self, reason):
        return {"status": "unfinished", "reason": str(reason), "stage": self.workflow.current,
                "plan_revision": self.workflow.plan_revision,
                "scripts": [vars(item).copy() for item in self.scripts.values()],
                "last_assessments": self.experiment_history[-3:], "plan": self.workflow.snapshot(),
                "validated_files": list(self.outputs.values()), "document_import_revisions": dict(self.imports),
                "document_confirmations": dict(self.confirmed),
                "pending_operations": list(self.pending), "pending_skill_operations": list(self.skill_operations),
                "unknown_executions": list(self.executions), "unknown_imports": dict(self.delivery_uncertainty),
                "model_requests": self.model_requests, "output_tokens": self.output_tokens,
                "argument_issue": self.steps.argument_issue,
                "execution_steps": self.steps.snapshot(),
                "current_experiment": self.current_experiment(), "correction_feedback": self.correction_feedback,
                "artifact_handoffs": deepcopy(list(self.artifact_handoffs.values())),
                "requirement_coverage": self.workflow.requirement_coverage(self),
                "output_budget": self.output_budget(),
                "working_decision": self.working_decision(),
                "continuation": "Inspect these saved artifacts and native receipts before continuing; never replay unknown commands."}

    def semantic_tool(self, name):
        from .execution_steps import STEP_TOOL_MODES
        if name in STEP_TOOL_MODES:
            return "run_modelling_step"
        return self.tool_semantics.get(name, name)

    def latest_experiment(self):
        return max((item for item in self.scripts.values() if item.phase == "executed"
                    and item.execution_sequence > self.workflow.anchor),
                   key=lambda item: item.execution_sequence, default=None)

    def current_experiment(self, *, compact=False):
        """One native execution identity and its assessment state, without copying source or thoughts."""
        item = self.latest_experiment()
        if item is None:
            return {"assessment_status": "none", "plan_revision": self.workflow.plan_revision}
        observation = self.experiment_observations.get(item.execution_sequence)
        recorded = observation is not None and observation["plan_revision"] == self.workflow.plan_revision
        return {"assessment_status": "recorded" if recorded else "pending",
                "plan_revision": self.workflow.plan_revision, "execution_sequence": item.execution_sequence,
                "command_id": item.command_id, "sandbox": item.sandbox, "script_path": item.path,
                "script_version": item.version, "exit_code": item.exit_code,
                "execution_inputs": dict(self.last_execution_inputs.get((item.sandbox, item.path), {})),
                "measurements": self.measurement_summary(item.execution_sequence) if compact else deepcopy(self.measurement_reports.get(item.execution_sequence)),
                "measurement_issues": deepcopy(self.measurement_issues.get(item.execution_sequence, [])),
                "native_measurement_reference": self.measurement_reference(item.execution_sequence),
                "recorded_outcome": observation["outcome"] if recorded else None,
                "next_step": observation["next_step"][:1000] if recorded else "Record the actual result once."}

    def measurement_summary(self, sequence):
        measured = self.measurement_reports.get(sequence)
        if measured is None:
            return None
        # Keep all verdicts and population counts; large ordinary data remains
        # in the native invocation and produced artifacts, not repeated frames.
        result = {k: deepcopy(measured[k]) for k in ("kind", "measurement_revision", "objective", "coverage",
                  "constraints", "assumption_invalid", "contradiction") if k in measured}
        values = measured.get("values", measured.get("measurements", {}))
        result["values"] = {k: v for k, v in values.items() if v is None or type(v) in {str, int, float, bool}}
        result["saved_data_keys"] = [k for k, v in values.items() if isinstance(v, (list, dict))]
        metadata = measured.get("metadata", {})
        result["metadata"] = {k: deepcopy(v) for k, v in metadata.items() if
            v is None or type(v) in {str, int, float, bool} or
            isinstance(v, list) and len(v) <= 8 and all(x is None or type(x) in {str, int, float, bool} for x in v)}
        result["saved_metadata_keys"] = [k for k in metadata if k not in result["metadata"]]
        result["native_measurement_reference"] = self.measurement_reference(sequence)
        return result

    def measurement_reference(self, sequence):
        if self._reference_cache is not None and sequence in self._reference_cache:
            return deepcopy(self._reference_cache[sequence])
        receipt = self.execution_receipts.get(sequence, {})
        logs = getattr(self.trace, "invocations", None)
        window = getattr(self.trace, "context_windows", {}).get(self._context_role)
        if logs is not None and window is not None:
            matches = [row for row in logs.records("tools").values()
                       if row.get("call_id") == receipt.get("archive_call_id", receipt.get("call_id")) and row.get("role") == receipt.get("role")
                       and row.get("run_attempt", 0) == receipt.get("run_attempt", 0)]
            if len(matches) == 1:
                row = matches[0]
                reference = window.issue_reference(row["id"], source_role=receipt["role"])
                if reference:
                    def stdout_path(value, pointer=""):
                        if not isinstance(value, dict):
                            return None
                        if isinstance(value.get("stdout"), str) and value.get("command_id") == receipt.get("command_id"):
                            return pointer + "/stdout"
                        for key, child in value.items():
                            found = stdout_path(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                            if found:
                                return found
                        return None
                    pointer = stdout_path(row.get("response"))
                    if pointer:
                        return {**reference, "pointer": pointer, "execution_sequence": sequence,
                                "reader": "read_tool_result", "source": "native tool invocation stdout"}
        return {"execution_sequence": sequence, "availability": "unavailable",
                "source": "native tool invocation stdout", "reason": "No complete scoped native invocation has been archived yet"}

    def relevant_measurements(self):
        """Rank dependencies and current checks before recency."""
        pending = self.workflow.pending_experiment or {}
        paths = set(pending.get("input_paths", []))
        dependencies = set(pending.get("evidence_refs", []))
        current_ids = {r["id"] for r in self.workflow.requirements() if r["stage"] == self.workflow.current}
        artifact_sequences = {r.get("execution_sequence") for r in self.artifact_handoffs.values() if r["path"] in paths}
        def relevance(pair):
            seq, report = pair
            checks = {key for c in report.get("constraints", []) for key in c.get("check_ids", [])}
            return (seq in artifact_sequences or f"execution:{seq}" in dependencies, bool(checks & current_ids), seq)
        return sorted(self.measurement_reports.items(), key=relevance, reverse=True)

    def working_decision(self):
        """A bounded host ledger of decisions, not a reconstruction of thoughts."""
        from .measurements import constraint_failures
        rows = [row for row in self.experiment_history if row["plan_revision"] == self.workflow.plan_revision
                and row["execution_sequence"] > self.workflow.anchor]
        latest = rows[-1] if rows else None
        step = next((s for s in reversed(self.steps.records) if s.get("plan_revision") == self.workflow.plan_revision
                     and s.get("stage") == self.workflow.current), None)
        current = self.latest_experiment()
        pending = bool(current and (not latest or latest["execution_sequence"] != current.execution_sequence))
        experiment = (self.workflow.plan[self.workflow.index].get("experiment", {})
            if self.workflow.plan and self.workflow.current in {"probe", "build"} else {})
        planned = self.workflow.pending_experiment
        if planned and (planned["plan_revision"] != self.workflow.plan_revision or planned["stage"] != self.workflow.current):
            planned = None
        return {"plan_revision": self.workflow.plan_revision, "stage": self.workflow.current,
                "current_subtask": {"state": "execute_planned_step" if planned and planned.get("origin") != "stage" else
                    "assess_result" if pending else "execute_planned_step" if planned else "choose_one_experiment",
                    "subtask_id": planned.get("subtask_id") if planned else step.get("subtask_id") if step else latest.get("subtask_id") if latest else None,
                    "artifact": planned["artifact"] if planned else step["script_path"] if step else experiment.get("artifact"),
                    "question": planned["question"] if planned else step["question"][:600] if step and pending else
                        latest["next_step"][:600] if latest else experiment.get("question"),
                    "method": planned.get("method") if planned else None,
                    "expected_output": planned.get("expected_output", "Measure only the current unknown; save intermediate data") if planned else
                        step["expected_output"][:600] if step and pending else "Measure only the current unknown; save intermediate data",
                    "stop_condition": planned["stop_condition"] if planned else step["stop_condition"][:600] if step and pending else experiment.get("stop_condition"),
                    "depends_on": planned.get("depends_on", []) if planned else [],
                    "evidence_refs": planned.get("evidence_refs", []) if planned else [],
                    "input_paths": planned.get("input_paths", [a["path"] for a in self.artifact_handoffs.values()]) if planned else
                        [a["path"] for a in step.get("input_artifacts", [])] if step else [],
                    "required_previous_assessment": pending,
                    "execution_sequence": current.execution_sequence if current else None},
                "retained_facts": [{"execution_sequence": seq, "measurements": self.measurement_summary(seq)}
                    for seq, _ in self.relevant_measurements()[:2] if planned],
                "last_decision": {k: latest[k][:400] if isinstance(latest[k], str) else latest[k]
                                  for k in ("execution_sequence", "outcome", "next_step", "review_reason") if k in latest}
                                  if latest else None,
                "invalid_candidates": [{"execution_sequence": seq, "failed_constraints": [c["name"] for c in constraint_failures(r)]}
                    for seq, r in list(self.measurement_reports.items())[-8:] if constraint_failures(r)],
                "rejected_methods": [{"reason": r["reason"][:800], "script_path": r["script_path"],
                    "script_version": r["script_version"]} for r in sorted(self.workflow.review_history,
                        key=lambda row: (row.get("stage") == self.workflow.current, row.get("plan_revision", 0)), reverse=True)[:2]],
                "decision_checkpoint": self.decision_checkpoint(),
                "instruction": "Use the host decision, artifact frames and executed contradictions. Do not reconstruct a future algorithm from reasoning tails."}

    def decision_checkpoint(self):
        """Relevant counterexamples survive settling many newer exchanges.

        These are references to real measurements and declared reviews. They
        never confer validation or claim a proposed next action was executed.
        """
        from .measurements import constraint_failures
        failed = {}
        for sequence, report in self.measurement_reports.items():
            names = tuple(sorted(c["name"] for c in constraint_failures(report)))
            if not names:
                continue
            key = (report.get("measurement_revision"), names)
            # Retain the first counterexample and latest occurrence for each
            # invariant instead of losing old contradictions to last-N slicing.
            record = {"execution_sequence": sequence, "measurement_revision": report.get("measurement_revision"),
                "failed_constraints": list(names), "kind": report.get("kind"),
                "native_measurement_reference": self.measurement_reference(sequence)}
            if key not in failed:
                failed[key] = {"first": record, "latest": record}
            else:
                failed[key]["latest"] = record
        rejected = {}
        for row in self.workflow.review_history:
            reason = row["reason"]
            key = (row.get("stage"), reason)
            rejected[key] = {"reason": reason[:800], "stage": row.get("stage"),
                "plan_revision": row.get("plan_revision"), "script_path": row["script_path"],
                "script_version": row["script_version"]}
        rank = {seq: i for i, (seq, _) in enumerate(self.relevant_measurements())}
        counterexamples = sorted(failed.values(), key=lambda r: rank[r["latest"]["execution_sequence"]])
        reviews = sorted(rejected.values(), key=lambda r: (r["stage"] == self.workflow.current, r["plan_revision"]), reverse=True)
        return {"counterexamples": counterexamples[:16], "rejected_methods": reviews[:16],
            "omitted_counterexample_groups": max(0, len(failed) - 16),
            "omitted_review_groups": max(0, len(rejected) - 16),
            "scope": "Historical contradictions with their original measurement/plan revisions; do not transfer a verdict to a changed artifact.",
            "source": "native measurement records and Planner reviews, not reconstructed reasoning"}

    def output_budget(self):
        return {"used_tokens": self.output_tokens}

    def time_budget(self):
        if self.limits.max_run_seconds is None:
            return {"total_seconds": None, "remaining_seconds": None,
                    "closeout_reserve_seconds": 0, "construction_seconds": None,
                    "phase": "closeout" if self.closing_reason else "experiment",
                    "effective_brakes": {"remaining_output_tokens": None,
                        "request_limit": self.limits.max_model_requests, "model_action_seconds": self.limits.model_action_seconds,
                        "script_execution_seconds": self.limits.script_execution_seconds,
                        "no_progress_requests": self.limits.model_requests_without_progress,
                        "repeated_error_limit": self.limits.repeated_error_limit, "stream_watchdog": "native OAW policy"},
                    "closeout_instruction":
                        "No total wall-clock deadline is configured. Continue measured experiments; "
                        "each script still needs a finite stopping rule. Validate and deliver when ready."}
        remaining = round(self.remaining_seconds, 3)
        reserve = min(self.limits.closeout_reserve_seconds, self.limits.max_run_seconds * .2)
        return {"total_seconds": self.limits.max_run_seconds, "remaining_seconds": remaining,
                "closeout_reserve_seconds": reserve, "construction_seconds": max(0, round(remaining - reserve, 3)),
                "phase": "closeout" if remaining <= reserve else "experiment",
                "closeout_instruction": "Reserve time for independent validation and requested delivery. "
                    "If the candidate remains unfinished, report the measured limitation and saved artifacts; "
                    "do not start another redesign or claim partial output is complete."}

    def configure_tools(self, definitions):
        # Projected operations keep a stable semantic capability ID even when
        # an adapter uses a different visible tool name.
        self.tool_semantics = {tool.name: str(tool.capability_id).removeprefix("operation:")
            if str(tool.capability_id).startswith("operation:") else tool.name for tool in definitions}
        self.tool_fields = {tool.name: set((tool.input_schema or {}).get("properties", {})) |
                           {p.name for p in tool.parameters} for tool in definitions}
        self.workflow.configure_tools(self.tool_semantics.values())
        self.notice("atom_sculptor", "Authorized modelling tools checked", workflow_event="modelling_capabilities",
                    tools=self.tool_semantics)

    def before_model(self, role: str, request: Any, bindings: Any, output_limit: int | None) -> None:
        self._context_role = role
        self._reference_cache = None
        self._source_reference_cache = {}
        logs = getattr(self.trace, "invocations", None)
        window = getattr(self.trace, "context_windows", {}).get(role)
        if logs is not None and window is not None:
            # Scope was set by the runtime before this callback. Reissue actual
            # evidence on fresh delegations and read the native store in batches.
            native_rows = logs.records("tools")
            references = [(f"tool-call:{r.get('run_attempt', 0)}:{r['role']}:{r.get('archive_call_id', r['call_id'])}", r["role"])
                          for r in self.execution_receipts.values()]
            # Reuse native argument archives for exact saved-source reads. Only
            # advertise full source only when its bytes match the saved source;
            # a patch invocation's edits are not the current full program.
            source_candidates = {}
            for key, script in list(self.scripts.items())[-4:]:
                for row in reversed(list(native_rows.values())):
                    args = row.get("effective_arguments", row.get("arguments"))
                    if (not isinstance(args, dict) or not row.get("arguments_complete")
                            or row.get("state") != "returned" or not row.get("response_complete")):
                        continue
                    path = args.get("script_path", args.get("path"))
                    if args.get("sandbox") != key[0] or path != key[1]:
                        continue
                    field = "source" if isinstance(args.get("source"), str) else "content"
                    source = args.get(field)
                    if isinstance(source, str) and source == self.script_sources.get(key):
                        source_candidates[key] = (row, field)
                        references.append((row["id"], row["role"]))
                        break
            issued = window.issue_references(references)
            for key, (row, field) in source_candidates.items():
                if row["id"] in issued:
                    self._source_reference_cache[key] = {**issued[row["id"]], "section": "arguments",
                        "pointer": "/" + field, "version": self.scripts[key].version,
                        "reader": "read_tool_result", "max_read_chars": 8000}
            self._reference_cache = {}
            for sequence, receipt in self.execution_receipts.items():
                identity = f"tool-call:{receipt.get('run_attempt', 0)}:{receipt['role']}:{receipt.get('archive_call_id', receipt['call_id'])}"
                row = native_rows.get(identity)
                response = row.get("response") if row else None
                pointer = ("/stdout" if isinstance(response, dict) and "stdout" in response
                           and response.get("command_id") == receipt.get("command_id") else
                           "/execution/stdout" if isinstance(response, dict) and isinstance(response.get("execution"), dict)
                           and "stdout" in response["execution"] and response["execution"].get("command_id") == receipt.get("command_id") else
                           "/result/stdout" if isinstance(response, dict) and isinstance(response.get("result"), dict)
                           and "stdout" in response["result"] and response["result"].get("command_id") == receipt.get("command_id") else None)
                if identity in issued and pointer:
                    self._reference_cache[sequence] = {**issued[identity], "pointer": pointer,
                        "execution_sequence": sequence, "reader": "read_tool_result", "source": "native tool invocation stdout"}
        for rejection in self._rejected_actions.values():
            if rejection["role"] == role:
                rejection["feedback_offered"] = True
        if role == "planner" and self.workflow.repeated_plan_rejections >= PLAN_REJECTION_LIMIT:
            fields = ", ".join(item["field"] for item in self.workflow.plan_issues)
            self.stop(f"Planner repeated the same unresolved plan issue {PLAN_REJECTION_LIMIT} times; invalid fields: {fields}")
        if self.block_reason is not None:
            self.stop(self.block_reason)
        if (self.limits.max_run_seconds is not None
                and time.monotonic() - self.started >= self.limits.max_run_seconds):
            self.stop("run time budget reached")
        if self.limits.max_model_requests is not None and self.model_requests >= self.limits.max_model_requests:
            raise HarnessBudgetReached("model request budget reached")
        if self.limits.max_model_requests is not None and self.model_requests >= self.limits.max_model_requests - 2:
            self.closing_reason = "model request budget nearly reached"
        self.model_requests += 1
        config = getattr(request, "config", None)
        if config is not None:
            role_limit = (self.limits.coordinator_output_tokens if role == "atom_sculptor" and not self.direct_builder
                          else self.limits.specialist_output_tokens)
            configured = getattr(config, "max_output_tokens", None)
            ceilings = [v for v in (configured, output_limit, role_limit) if v is not None]
            config.max_output_tokens = min(ceilings) if ceilings else None
        self.workflow.prepare_request(role, request, self)
        self.actions.prepare(self, role, request, bindings)
        # This is host evidence, never a replacement for an historical call.
        # The original user input travels independently of a coordinator's paraphrase.
        if bindings is not None and isinstance(getattr(request, "contents", None), list):
            # Stable frame: identical bytes whenever its facts are unchanged, so
            # it is sent once per change instead of once per request and stays
            # prefix-cache friendly. Everything below changes turn by turn.
            static = {
                "execution_role": role,
                "original_user_request": self.original_request,
                "turn_start_structure_selection": self.selection_snapshot,
                "authorized_tools": self.tool_semantics,
                "sandbox_python_environments": self.sandbox_environments,
            }
            if not self.direct_builder:
                requirements = self.workflow.requirements()
                visible_plan = [self.workflow.plan[self.workflow.index]] if role == "structure_builder" and self.workflow.plan and self.workflow.index < len(self.workflow.plan) else self.workflow.plan
                checks = {stage["id"]: stage["checks"] for stage in self.workflow.plan or []}
                if role == "structure_builder":
                    # The committed requirement IDs already own the full check
                    # text. Plans and stage indexes reference those IDs instead
                    # of sending the same acceptance text three times.
                    checks = {stage["id"]: [r["id"] for r in requirements if r["stage"] == stage["id"]]
                              for stage in self.workflow.plan or []}
                    visible_plan = [{**{k: deepcopy(v) for k, v in stage.items() if k != "checks"},
                                     "check_ids": checks[stage["id"]]} for stage in visible_plan or []]
                static["modelling_plan_context"] = {
                    "plan": visible_plan,
                    "acceptance_checks": checks,
                    "current_stage": self.workflow.current, "delivery": self.workflow.delivery,
                    "assumptions": self.workflow.assumptions,
                    "plan_revision": self.workflow.plan_revision, "plan_limits": PLAN_LIMITS,
                    "review_history": self.workflow.review_history,
                    "missing_tools": self.workflow.missing_tools,
                    "requirements": requirements,
                }
                if role == "structure_builder":
                    static["modelling_plan_context"]["check_text_source"] = "requirements: each id maps to its full committed check text; plan.check_ids and acceptance_checks reference these IDs."
            if role == "structure_builder":
                from .measurements import measurement_contract
                static["measurement_contract"] = measurement_contract()
                if self.steps.helper_module:
                    static["modelling_io"] = self.steps.helper_contract()
                if "modelling_plan_context" in static:
                    static["modelling_plan_context"]["review_history"] = [
                        {k: row[k][:800] if isinstance(row[k], str) else row[k]
                         for k in ("plan_revision", "reason", "stage", "execution_sequence", "script_path", "script_version") if k in row}
                        for row in self.workflow.review_history]
            static_text = ("HOST STABLE CONTEXT (not a tool invocation). Committed request, resources "
                           "and plan; one current copy in working history, retained unchanged until "
                           "its facts change. Each API request still includes this frame. "
                           "Delegation advice and derived assumptions must not add user "
                           "constraints.\n" + json.dumps(static, ensure_ascii=False, sort_keys=True))
            view = self.workflow.snapshot(execution_focus=role == "structure_builder")
            view["dependency_registry"] = self.workflow.dependency_registry(self)
            if role == "structure_builder" and not self.direct_builder:
                # The stable frame already carries every stage's checks and the
                # committed assumptions; keep only phase state in this view.
                for key in ("plan", "acceptance_checks", "assumptions", "plan_limits", "missing_tools"):
                    view.pop(key, None)
            snapshot = {
                "execution_role": role,
                "run_budget": {"request_limit": self.limits.max_model_requests, "requests_used": self.model_requests,
                               "closing_reason": self.closing_reason},
                "scripts": [vars(item) for item in list(self.scripts.values())[-4:]],
                "current_saved_source": self._current_source_head(role),
                "document_import_revisions": self.imports,
                "document_confirmations": self.confirmed,
                "delivery_issue": self.delivery_issue,
                "available_tools": [d.name for t in getattr(request.config, "tools", None) or []
                                    for d in getattr(t, "function_declarations", None) or []],
                "unknown_imports": self.delivery_uncertainty,
                "file_validation_and_exports": list(self.outputs.values())[-4:],
                "file_receipt_issues": list(self.receipt_issues.values())[-4:],
                "output_limit_recoveries": self.output_recoveries,
                "output_budget": {"selected_model_limit": output_limit,
                    "specialist_limit": self.limits.specialist_output_tokens,
                    "recovered_action_limit": self.limits.recovered_action_output_tokens,
                    "remaining_run_tokens": None,
                    "effective_request_limit": getattr(config, "max_output_tokens", None)},
                "modelling_workflow": view,
                "execution_steps": self.steps.snapshot(),
                "current_experiment": self.current_experiment(compact=True), "correction_feedback": self.correction_feedback,
                "execution_action": self.actions.snapshot(),
                "time_budget": self.time_budget(),
                "experiment_observations": [{k: row[k][:400] if isinstance(row[k], str) else row[k]
                    for k in ("execution_sequence", "outcome", "next_step", "plan_revision") if k in row}
                    for row in list(self.experiment_observations.values())[-3:]],
                "artifact_handoffs": list(self.artifact_handoffs.values())[-8:],
                "working_decision": self.working_decision(),
                "requirement_coverage": self.workflow.requirement_coverage(self),
            }
            snapshot["output_budget"].update(self.output_budget())
            if snapshot["correction_feedback"]:
                snapshot["correction_feedback"] = {k: v for k,v in snapshot["correction_feedback"].items()
                                                   if k != "current_experiment"}
            text = ("HOST RUN EVIDENCE (not a tool invocation). The original user request in the stable frame "
                    "is authoritative; delegation advice and derived assumptions must "
                    "not add user constraints. A written script contains the real saved source, "
                    "even if a UI log redacts it. Execute it and inspect the result before "
                    "rewriting it. Never infer validated/exported/imported/confirmed from written "
                    "or executed alone. The stable frame earlier in this context carries the "
                    "committed plan, authorized tools and turn-start selection.\n" + json.dumps(snapshot, ensure_ascii=False))
            # Recovery invokes this callback on the same request. Replace only
            # snapshots actually emitted by this Run, never user text or receipts.
            host_texts = self._host_evidence_texts | self._host_static_texts
            signed = any(getattr(p, "thought_signature", None) for c in request.contents for p in (getattr(c, "parts", None) or []))
            retained = []
            for content in request.contents:
                original_parts = getattr(content, "parts", None) or []
                parts = [part for part in original_parts
                         if not (not signed and getattr(content, "role", None) == "user"
                             and getattr(part, "text", None) in host_texts
                             and not getattr(part, "thought_signature", None)
                             and not getattr(part, "function_call", None)
                             and not getattr(part, "function_response", None))]
                if len(parts) == len(original_parts):
                    retained.append(content)
                elif parts:
                    retained.append(content.model_copy(update={"parts": parts}) if hasattr(content, "model_copy") else
                                    bindings.types.Content(role=getattr(content, "role", None), parts=parts))
            request.contents[:] = retained
            self._host_static_texts.add(static_text)
            if not any(getattr(part, "text", None) == static_text
                       for content in request.contents
                       for part in (getattr(content, "parts", None) or ())):
                # The stable frame sits directly after the opening turn (the
                # delegation or user request) and before settled history.
                position = len(request.contents) if signed else 1 if request.contents else 0
                request.contents.insert(position, bindings.types.Content(
                    role="user", parts=[bindings.types.Part.from_text(text=static_text)]))
            self._host_evidence_texts.add(text)
            request.contents.append(bindings.types.Content(
                role="user", parts=[bindings.types.Part.from_text(text=text)]))
            if role == "structure_builder" and self.steps.available:
                from .working_context import focus_history
                focus_history(self, request, bindings)

    def _current_source_head(self, role: str, limit: int = 800) -> dict[str, Any] | None:
        """Expose the newest saved script's head so native exact edits are possible
        without a model turn spent re-reading the file. Native exact text matching stays authoritative."""
        if role != "structure_builder" or not self.scripts:
            return None
        key = self.current_script_key or next(reversed(self.scripts))
        source = self.script_sources.get(key)
        if not source:
            return None
        item = self.scripts[key]
        import re
        lines = source.splitlines(keepends=True)
        matches = re.findall(r'File "[^"\n]*' + re.escape(key[1]) + r'", line (\d+)', self.execution_observations.get(key, ""))
        error_line = int(matches[-1]) if matches else None
        start = max(0, error_line - 5) if error_line else 0
        excerpt = "".join(lines[start:start + 9])[:2400] if error_line else source[:limit]
        issues = self.measurement_issues.get(item.execution_sequence, [])
        producer_line = None
        if issues and not error_line:
            import ast
            # Select existing source only; never execute it or invent a repair.
            # A rejected leaf may be produced dynamically, so follow the data
            # field's assigned variable back to its builder when possible.
            parts = str(issues[0].get("pointer", "")).split("/")[2:]
            fields = [p.replace("~1", "/").replace("~0", "~") for p in parts]
            legacy_fields = str(issues[0].get("field", "")).split(".")
            field = fields[0] if fields else legacy_fields[1] if len(legacy_fields) > 1 else ""
            try:
                nodes = list(ast.walk(ast.parse(source)))
                names = [fields[-1], field] if fields else [field]
                for name in names:
                    found = next(((node, value) for node in nodes if isinstance(node, ast.Dict)
                        for key_node, value in zip(node.keys, node.values)
                        if isinstance(key_node, ast.Constant) and key_node.value == name), None)
                    if found:
                        node, value = found
                        producer_line = value.lineno
                        if isinstance(value, ast.Name):
                            assignment = next((n for n in nodes if isinstance(n, ast.Assign)
                                and any(isinstance(t, ast.Name) and t.id == value.id for t in n.targets)), None)
                            if assignment:
                                producer_line = assignment.lineno
                        break
            except (SyntaxError, ValueError, RecursionError):
                pass  # The ordinary source head remains a truthful fallback.
            if producer_line:
                start = max(0, producer_line - 4)
                excerpt = "".join(lines[start:start + 24])[:2400]
        result = {"sandbox": key[0], "path": key[1], "version": item.version,
                "source_chars": len(source), "source_head": source[:limit],
                "reader": {"tool": "read_modelling_source", "paging": "offset and limit in characters; native-confirmed saved source snapshot"},
                "error_line": error_line, "excerpt_start_line": start + 1}
        reference = self._source_reference_cache.get(key)
        if reference and reference["version"] == item.version:
            result["source_reference"] = deepcopy(reference)
        if producer_line:
            result.update(excerpt_reason="measurement_contract", producer_line=producer_line)
        if excerpt != result["source_head"]:
            result["source_excerpt"] = excerpt
        else:
            result["excerpt_source"] = "source_head"
        return result

    @staticmethod
    def _stdout_objective(stdout):
        from .measurements import parse_measurements
        report, _ = parse_measurements(stdout)
        return report.get("objective") if report else None

    def after_model(self, response: Any) -> None:
        if getattr(response, "partial", False):
            return
        count = getattr(getattr(response, "usage_metadata", None), "candidates_token_count", None)
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            self.output_tokens += count

    def tool_callbacks(self, role: str) -> dict[str, Any]:
        async def before_tool(tool: Any, args: Any, tool_context: Any):
            if not isinstance(args, Mapping):
                return None
            name = self.semantic_tool(getattr(tool, "name", ""))
            if name == "finish_task" and role == "planner" and set(args) - {"result"}:
                self.workflow.plan_issues = [{"field": "finish_task.result." + key,
                    "message": "Place plan fields inside result, not beside it."} for key in sorted(set(args) - {"result"})]
                self.workflow.record_plan_rejection()
                return {"ok": False, "error": {"code": "plan_arguments", "kind": "argument_validation",
                    "message": "finish_task accepts only result. Place assumptions inside result.assumptions, alongside status/delivery/stages.",
                    "fields": sorted(set(args) - {"result"}), "retry_tool": "finish_task"}}
            from .execution_steps import workflow_argument_error
            rejected_arguments = workflow_argument_error(name, args)
            if rejected_arguments:
                self.record_rejection(role, name, args, rejected_arguments)
                return rejected_arguments
            if name in {"read_tool_result", "read_modelling_source"}:
                return None  # Scoped native reader validates Run/role/reference.
            blocked = self.workflow.before_tool(role, name, args, self)
            if blocked is not None:
                self.steps.retain_target_rejection(self, getattr(tool, "name", name), args, blocked)
                self.record_rejection(role, name, args, blocked)
                return blocked
            from .execution_steps import STEP_TOOL_MODES, argument_error
            actual_name = getattr(tool, "name", "")
            if actual_name in STEP_TOOL_MODES:
                rejected = argument_error(actual_name, args)
                if rejected:
                    self.steps.retain_target_rejection(self, actual_name, args, rejected)
                    self.record_rejection(role, actual_name, args, rejected)
                    return rejected
                self.steps.argument_issue = None
                self.steps.argument_failures = 0
            if name == "finish_task" and isinstance(args.get("result"), Mapping):
                # ADK's real finish tool expects a string. Serialize only after
                # workflow validation, for successful and blocked reports too.
                args["result"] = json.dumps(args["result"], ensure_ascii=False, separators=(",", ":"))
            sandbox = str(args.get("sandbox", ""))
            call_id = str(getattr(tool_context, "function_call_id", ""))
            target = str(args.get("target", ""))
            if name in {"import_atom_structure_file", "replace_atom_structure"} and target in self.delivery_uncertainty:
                return {"ok": False, "error": {"code": "delivery_import_unknown", "message":
                    "An earlier structure write has no confirmed outcome. Inspect native receipts and the live document before another write.",
                    "pending": self.delivery_uncertainty[target]}}
            if name == "import_atom_structure_file":
                exported = self.outputs.get((sandbox, posixpath.normpath(str(args.get("file_name", "")))))
                issue = self.receipt_issues.get((sandbox, posixpath.normpath(str(args.get("file_name", "")))), {})
                generated = any(key[0] == sandbox for key in self.scripts) or exported is not None or bool(issue)
                if generated and (not exported or exported["phase"] != "exported"):
                    return {"ok": False, "error": {"code": "independent_validation_required", "message":
                        "Generated output has no independent validation/conversion receipt for this file. "
                        "Run the authorized structure-inspect Skill with run_skill_script, then convert that "
                        "same validated file before importing. Copied scripts and arbitrary stdout are not trusted receipts.",
                        "receipt_issue": issue or {"reason": "missing_conversion_receipt"}}}
            if name == "write_sandbox_text_file":
                key = (sandbox, posixpath.normpath(str(args.get("path", ""))))
                if not key[1].endswith(".py"):
                    return None
                if isinstance(args.get("content"), str) and not args["content"].strip():
                    return {"ok": False, "error": {"code": "empty_script", "message": "Save a runnable probe, not an empty Python file."}}
                item = self.scripts.get(key)
                # Reuse the native overwrite flag for a Run-owned saved script.
                if item and isinstance(args.get("content"), str):
                    if "overwrite" not in args:
                        args["overwrite"] = True
                running = any(key == k for group in [*self.pending.values(), *self.executions.values()] for k, _ in group)
                if running or (item and item.unexecuted_writes >= self.limits.unexecuted_write_limit):
                    self.denied_writes[key] = self.denied_writes.get(key, 0) + 1
                    if self.denied_writes[key] > 1:
                        self.stop(f"repeated overwrite without new execution evidence for {key[1]}")
                    return {"ok": False, "error": {
                        "code": "execution_required", "message":
                        f"{key[1]} already contains real source. " +
                        ("Its command is still running; collect its result before editing."
                         if running else "Execute the saved script and inspect exit_code/stdout/stderr before rewriting."),
                    }}
            if name == "execute_command":
                argv = args.get("argv", [])
                source = inline_python(argv)
                measuring = self.actions.enabled(self, role) and self.workflow.current == "probe"
                if measuring and not self.scripts and source is None:
                    return self.workflow.error("input_measurement_required", "Before a saved probe exists, use "
                        "direct python3 -c for one small input measurement, or save the probe file. "
                        "Shell wrappers and unrelated scripts are not input-measurement receipts.")
                if measuring and source is not None:
                    if self.scripts:
                        return self.workflow.error("saved_probe_required", "Execute the saved probe file; "
                            "an inline command cannot replace its execution receipt.")
                    if self.actions.inline_attempts >= self.limits.inline_probe_limit:
                        return self.workflow.error("inline_probe_limit", "Inline measurement limit reached. "
                            "Save the input-measurement script and execute it; do not continue command-only exploration.")
                    if not source.strip() or len(source) > self.limits.inline_probe_chars:
                        return self.workflow.error("inline_probe_size", f"Use one nonempty measurement of at most "
                            f"{self.limits.inline_probe_chars} Python characters, or save it as the probe file.")
                    self.actions.inline_attempts += 1
                    source_key = (sandbox, source)
                    self.inline_source_versions.setdefault(source_key, len(self.inline_source_versions) + 1)
                    self.inline_executions[call_id] = {"sandbox": sandbox,
                        "source_version": self.inline_source_versions[source_key]}
                if isinstance(argv, (list, tuple)):
                    # Match an actual direct Python file invocation, not wc/cat,
                    # a shell string, python -c/-m, or an unrelated probe.
                    from open_agent_world.plugin_api.sandbox import python_entrypoint
                    entry = python_entrypoint(argv)
                    if entry and entry[0] == "script":
                        index = entry[2] - 1
                        path = posixpath.normpath(entry[1])
                        item = self.scripts.get((sandbox, path))
                        if item:
                            if (self.actions.enabled(self, role)
                                    and "timeout_seconds" in self.tool_fields.get(getattr(tool, "name", ""), set())
                                    and "timeout_seconds" not in args):
                                remaining = self.remaining_seconds
                                args["timeout_seconds"] = (self.limits.script_execution_seconds if remaining is None
                                                           else min(self.limits.script_execution_seconds, remaining))
                                self.notice(role, f"Saved script uses command deadline {args['timeout_seconds']:.1f}s",
                                    workflow_event="modelling_command_deadline", path=path, sandbox=sandbox,
                                    command_deadline_seconds=args["timeout_seconds"])
                            previous = self.last_execution_inputs.get((sandbox, path))
                            verified = self._verified_execution_inputs.pop(call_id, None)
                            inputs = {"version": item.version, "argv": verified["argv"] if verified else list(argv[index + 1:]),
                                      "environment_revision": self.environment_revisions.get(sandbox, 0),
                                      "input_artifacts": verified["input_artifacts"] if verified else []}
                            if previous and previous["exit_code"] != 0 and not (verified and verified.get("repeat_purpose")) and all(previous.get(k, [] if k == "input_artifacts" else None) == v for k, v in inputs.items()):
                                return self.workflow.error("step_unchanged_failure", "The last failed source, arguments and environment are unchanged; patch or request review.")
                            self.execution_inputs[call_id] = inputs
                            self.executions[call_id] = [((sandbox, path), item.version)]
            if name in {"import_atom_structure_file", "replace_atom_structure"}:
                # Mark before dispatch, after all host rejections. Cancellation
                # or a lost response can follow a committed document mutation.
                self.delivery_uncertainty[str(args.get("target", ""))] = {
                    "call_id": call_id, "sandbox": sandbox, "file": args.get("file_name"),
                    "expected_revision": args.get("expected_revision")}
            return None

        async def after_tool(tool: Any, args: Any, tool_context: Any, tool_response: Any):
            if not isinstance(args, Mapping):
                return None
            name = self.semantic_tool(getattr(tool, "name", ""))
            if name == "finish_task":
                previous_revision = self.workflow.plan_revision
                previous_review = self.workflow.review_request
                previous_subtasks = len(self.workflow.subtask_history)
                self.workflow.observe_finish(role, args, tool_response, evidence=self)
                if previous_subtasks != len(self.workflow.subtask_history):
                    self.correction_feedback = None
                    self.notice(role, "Measured local checkpoint returned; continue the workflow", workflow_event="modelling_subtask",
                                **self.workflow.subtask_history[-1])
                if previous_revision != self.workflow.plan_revision:
                    self.correction_feedback = None
                    self.notice(role, "Planner plan accepted · current stage=" + self.workflow.current,
                                workflow_event="modelling_plan", stage="planned", plan_revision=self.workflow.plan_revision)
                if previous_review is None and self.workflow.review_request is not None:
                    self.notice(role, "Measured plan review requested · returning to coordinator and Planner",
                                workflow_event="modelling_plan_review", stage=self.workflow.current,
                                plan_revision=self.workflow.plan_revision)
            result = _payload(tool_response)
            sandbox = str(args.get("sandbox", ""))
            call_id = str(getattr(tool_context, "function_call_id", ""))
            if name in {"inspect_sandbox", "start_sandbox"} and isinstance(result.get("state"), str):
                self.actions.sandbox_states[sandbox] = result["state"]
            if name in {"inspect_sandbox", "start_sandbox"} and isinstance(result.get("python_environment"), dict):
                self.sandbox_environments[sandbox] = dict(result["python_environment"])
            # Different commands in one Sandbox are different dependencies.
            # Compare stable exit/error evidence, never command IDs or timing.
            operation_args = {key: args.get(key) for key in ("argv", "skill", "script", "script_path", "operation_id", "path", "file_name",
                              "expected_revision")
                              if key in args}
            if sandbox:
                operation_args["environment_revision"] = self.environment_revisions.get(sandbox, 0)
            proposed = args.get("source", args.get("content"))
            if isinstance(proposed, str):
                operation_args["proposed_source_chars"] = len(proposed)
            dependency = (name, sandbox or str(args.get("target") or ""),
                          json.dumps(operation_args, sort_keys=True, default=str))
            error = tool_response.get("error") if isinstance(tool_response, Mapping) else None
            if isinstance(error, Mapping):
                # Typed tools can reject inside their implementation, after the
                # pre-dispatch callback. Give those definite host rejections
                # the same corrective-feedback policy as pre-dispatch rejects.
                self.steps.retain_target_rejection(self, getattr(tool, "name", name), args, tool_response)
                self.record_rejection(role, getattr(tool, "name", name), args, tool_response)
            if not error and isinstance(result.get("exit_code"), int) and result["exit_code"] != 0:
                error = {"exit_code": result["exit_code"], "stderr": result.get("stderr", "")}
                if not error["stderr"]:
                    error["stdout"] = result.get("stdout", "")
            step = tool_response.get("step", {}) if isinstance(tool_response, Mapping) else {}
            if not isinstance(step, Mapping):
                step = {}
            recoverable_script = bool((name == "run_modelling_step" and step.get("command_id")) or self.executions.get(call_id) or
                (name == "wait_sandbox_operation" and self.pending.get(str(args.get("operation_id", "")))))
            parameter_error = isinstance(error, Mapping) and error.get("kind") == "argument_validation"
            host_rejection = isinstance(error, Mapping) and error.get("kind") in {
                "workflow_state", "step_contract", "execution_unknown", "plan_review", "workflow_blocked"}
            if error:
                # Only host-created rejection kinds bypass dependency counting.
                # Real native failures retain their existing bounded policy.
                kind = error.get("kind", "native_dependency") if isinstance(error, Mapping) else "native_dependency"
                self.correction_feedback = {"role": role, "tool": getattr(tool, "name", name), "kind": kind,
                    "plan_revision": self.workflow.plan_revision,
                    "code": error.get("code") if isinstance(error, Mapping) else None,
                    "message": str(error.get("message", error) if isinstance(error, Mapping) else error)[:1600],
                    "command_dispatched": error.get("command_dispatched") if isinstance(error, Mapping) else None,
                    "current_experiment": self.current_experiment()}
                if host_rejection:
                    self.correction_feedback.update({k: deepcopy(error[k]) for k in
                        ("field", "expected_script_path", "actual_script_path", "issues") if k in error})
                if host_rejection and isinstance(error, dict):
                    action = ("Collect native execution receipts without replaying unknown commands." if kind == "execution_unknown"
                              else "Return finish_task status=needs_plan_review with this execution and the measured contradiction." if kind == "plan_review"
                              else "Return finish_task status=blocked with saved artifacts and unmet checks." if kind == "workflow_blocked"
                              else "Align the listed receipt/fields with host state, then take the next bounded action. "
                                "Recorded assessments are reused by modelling tools; omit previous_assessment after observe. "
                                "Do not change the scientific plan to repair a protocol rejection.")
                    recovery = error.setdefault("recovery", {})
                    recovery["current_experiment"] = self.current_experiment()
                    recovery.setdefault("next_action", action)
            elif (tool_response and isinstance(tool_response, Mapping) and tool_response.get("ok") is not False
                  and name not in READ_DEPENDENCIES):
                self.correction_feedback = None
            if parameter_error and name == "run_modelling_step":
                self.steps.argument_failures += 1
                self.steps.argument_issue = dict(error)
                self.notice(role, "Modelling argument rejected; no native work dispatched",
                    workflow_event="modelling_argument_error", **self.steps.argument_issue,
                    attempts=self.steps.argument_failures)
                if self.steps.argument_failures >= self.limits.repeated_error_limit:
                    self.block_reason = (f"Model could not submit complete modelling tool arguments after {self.steps.argument_failures} attempts; "
                        f"missing/invalid fields: {', '.join(error.get('fields', []))}. This is an argument error, not a script or dependency failure")
            elif name == "run_modelling_step" and result.get("step"):
                self.steps.argument_failures = 0
                self.steps.argument_issue = None
            if error and not parameter_error and not host_rejection and name not in READ_DEPENDENCIES:
                # A usage error on an informational reader (wrong path, bad
                # offset) is guidance for the next call, not a failing
                # dependency; it must never trip the repeated-error fuse.
                fingerprint = json.dumps(error, sort_keys=True, default=str)
                previous = self.failures.get(dependency)
                count = previous[1]+1 if previous and previous[0] == fingerprint else 1
                self.failures[dependency] = (fingerprint, count)
                if count >= self.limits.repeated_error_limit and not recoverable_script:
                    self.block_reason = f"{name} repeatedly returned the same dependency error ({count} attempts)"
                    self.notice(role, f"Blocked · {name} returned the same error {count} times", stage="blocked")
            elif result and not error:
                self.failures.pop(dependency, None)
            if name in {"start_sandbox", "install_python_packages"} and result and not error:
                self.environment_revisions[sandbox] = self.environment_revisions.get(sandbox, 0) + 1
            written = result.get("written")
            if name == "write_sandbox_text_file" and isinstance(written, int) and written > 0:
                path = posixpath.normpath(str(args.get("path", "")))
                if (sandbox, path) in self.outputs:
                    self._invalidate_file(sandbox, path, "file_written")
                if path.endswith(".py"):
                    key = (sandbox, path)
                    previous = self.scripts.get(key)
                    old_source = self.script_sources.get(key)
                    cached = args.get("content")
                    if not isinstance(cached, str):
                        cached = None
                        if old_source is not None and args.get("edits"):
                            from open_agent_world.plugin_api.sandbox import apply_text_edits
                            from open_agent_world.plugin_api.sandbox import SandboxValidationError
                            try:
                                cached = apply_text_edits(old_source.encode(), args["edits"]).decode()
                            except (ValueError, SandboxValidationError):
                                pass
                    changed = cached is None or cached != old_source
                    item = ScriptEvidence(sandbox, path,
                        version=previous.version + int(changed) if previous else 1,
                        unexecuted_writes=previous.unexecuted_writes + 1 if previous else 1)
                    self.scripts[key] = item
                    self.current_script_key = key
                    if cached is not None:
                        self.script_sources[key] = cached
                    else:
                        self.script_sources.pop(key, None)
                    self.notice(role, f"Script saved · {path}; execution not yet verified",
                                stage="written", path=path, sandbox=sandbox, version=item.version, call_id=call_id)
                    if item.unexecuted_writes >= self.limits.unexecuted_write_limit:
                        self.notice(role, f"Execution needed · {path} has been written repeatedly without execution",
                                    stage="execution_required", path=path)
            skill_args = args if name == "run_skill_script" else None
            if name == "wait_sandbox_operation":
                skill_args = self.skill_operations.get(str(args.get("operation_id", "")))
            if skill_args is not None:
                op = str(result.get("operation_id") or args.get("operation_id") or "")
                if result.get("status") == "running" and op:
                    self.skill_operations[op] = dict(skill_args)
                elif "exit_code" in result:
                    self.skill_operations.pop(op, None)
                    self._observe_skill(role, skill_args, result)
            measurement = self.inline_executions.pop(call_id, None)
            if name == "wait_sandbox_operation":
                measurement = self.inline_pending.pop(str(args.get("operation_id", "")), None)
            if measurement is not None:
                operation = str(result.get("operation_id") or result.get("command_id") or args.get("operation_id") or "")
                if result.get("status") == "running" and operation:
                    self.inline_pending[operation] = measurement
                else:
                    exit_code = result.get("exit_code")
                    observation = {key: measurement[key] for key in ("sandbox", "source_version")}
                    observation.update({"exit_code": exit_code if type(exit_code) is int else None,
                        "stdout": str(result.get("stdout", ""))[:1000],
                        "stderr": str(result.get("stderr", "") or tool_response.get("error", ""))[:1000]
                                  if isinstance(tool_response, Mapping) else ""})
                    self.actions.inline_measurements.append(observation)
                    if type(exit_code) is int and exit_code == 0:
                        self.actions.successful_measurements.add((measurement["sandbox"], measurement["source_version"]))
                    self.notice(role, "Inline input measurement returned; saved probe checkpoint still required",
                        workflow_event="inline_measurement", **observation)
            group = self.executions.pop(call_id, [])
            operation = str(result.get("operation_id") or result.get("command_id") or "")
            if name == "wait_sandbox_operation":
                operation = str(args.get("operation_id") or operation)
                group = self.pending.get(operation, [])
            if group and result.get("status") == "running":
                self.pending[operation] = group
                if call_id in self.execution_inputs:
                    self.execution_inputs[operation] = self.execution_inputs.pop(call_id)
            if group and isinstance(result.get("exit_code"), int):
                self.pending.pop(operation, None)
                for key, version in group:
                    item = self.scripts[key]
                    if item.version != version:
                        continue  # An older command never validates a newer version.
                    item.phase = "executed"
                    item.exit_code = result["exit_code"]
                    item.command_id = operation or None
                    self.execution_sequence += 1
                    item.execution_sequence = self.execution_sequence
                    self.execution_receipts[item.execution_sequence] = {"call_id": call_id, "role": role,
                        "archive_call_id": getattr(tool_context, "native_invocation_call_id", call_id),
                        "run_attempt": getattr(self.trace, "run_attempt", 0), "command_id": item.command_id,
                        "sandbox": item.sandbox, "script_path": item.path, "source_version": version,
                        "plan_revision": self.workflow.plan_revision, "stage": self.workflow.current,
                        "stderr": str(result.get("stderr", "")), "stdout": str(result.get("stdout", ""))}
                    self.execution_observations[key] = str(result.get("stdout", ""))[:1500]
                    if result.get("stderr"):
                        self.execution_observations[key] += "\nSTDERR:\n" + str(result["stderr"])[:1500]
                    inputs = self.execution_inputs.pop(call_id, None)
                    if inputs is None:
                        inputs = self.execution_inputs.pop(operation, {})
                    self.execution_inputs[operation] = dict(inputs)
                    self.last_execution_inputs[key] = {**inputs, "exit_code": item.exit_code}
                    from .measurements import parse_measurements, constraint_failures
                    measured, issues = parse_measurements(result.get("stdout", "")) if item.exit_code == 0 else (None, [])
                    if measured:
                        known_checks = {r["id"] for r in self.workflow.requirements()}
                        unknown_checks = sorted({i for c in measured.get("constraints", []) for i in c.get("check_ids", [])} - known_checks)
                        if unknown_checks:
                            issues.append({"code": "measurement_contract", "field": "oaw_measurements.constraints.check_ids",
                                "message": "Unknown HOST requirement IDs: " + ", ".join(unknown_checks),
                                "next_action": "Use stable IDs from HOST requirements; never invent an ID."})
                            measured = None
                    if measured:
                        self.measurement_reports[item.execution_sequence] = deepcopy(measured)
                        for artifact in measured.get("artifacts", []):
                            self.artifact_handoffs[(item.sandbox, artifact["path"])] = {**deepcopy(artifact),
                                "sandbox": item.sandbox, "command_id": item.command_id,
                                "producer_script_version": item.version, "execution_sequence": item.execution_sequence,
                                "plan_revision": self.workflow.plan_revision,
                                "source": "native stdout report; file bytes are checked before consumption; independent validation still required",
                                "candidate_eligible": measured.get("kind") == "candidate" and not constraint_failures(measured)}
                    if issues:
                        self.measurement_issues[item.execution_sequence] = issues
                        self.notice(role, "Measurement output was not parsed; repair its contract", workflow_event="measurement_contract", issues=issues)
                    metric = measured.get("objective") if measured else None
                    declared = (self.workflow.plan[self.workflow.index]["experiment"].get("objective")
                        if self.workflow.active and self.workflow.current == "build" else None)
                    if declared and item.exit_code == 0 and (not metric or any(
                            metric.get(k, "") != declared.get(k, "") for k in ("name", "direction", "unit"))):
                        issue = {"code": "objective_unavailable", "message": "No comparable planned objective was received; this is diagnostic evidence, not candidate improvement.",
                                 "expected_objective": declared,
                                 "next_action": "Use the executed facts for one small repair; print the actual planned metric with stable definition, or mark kind=diagnostic. Never invent a value."}
                        self.measurement_issues.setdefault(item.execution_sequence, []).append(issue)
                    if metric is not None:
                        self.measured_objectives[item.execution_sequence] = metric
                    self.workflow.observe_execution(self, item)
                    item.unexecuted_writes = 0
                    self.denied_writes.pop(key, None)
                    self.notice(role, f"Script executed · {item.path} · exit_code={item.exit_code}; validation still required",
                                stage="executed", script_version=version, command_id=operation,
                                path=item.path, exit_code=item.exit_code, call_id=call_id)
            target = str(args.get("target", ""))
            if name in {"import_atom_structure_file", "replace_atom_structure"}:
                pending = self.delivery_uncertainty.get(target)
                # Only native, definite pre-write rejections release the latch.
                # Generic execution/timeout/cancellation errors are ambiguous.
                rejected = (tool_response.get("error") if isinstance(tool_response, Mapping) else None)
                code = rejected.get("code") if isinstance(rejected, Mapping) else None
                if pending and pending.get("call_id") == call_id and (
                        type(result.get("revision")) is int or code in {
                            "revision_conflict", "permission_denied", "not_found", "unsafe_path", "invalid_resource"}):
                    self.delivery_uncertainty.pop(target, None)
            if name in {"import_atom_structure_file", "replace_atom_structure"} and type(result.get("revision")) is int:
                self.imports[target] = result["revision"]
                self.import_details[target] = dict(result)
                self.notice(role, f"Structure imported · revision {result['revision']}; read-back still required",
                            stage="imported", target=target, revision=result["revision"])
            if name == "inspect_atom_structure" and target in self.imports:
                detail = self.import_details[target]
                same_revision = result.get("revision") == self.imports[target]
                expected_count = detail.get("summary", {}).get("atom_count")
                actual_count = result.get("summary", {}).get("atom_count")
                if same_revision and (expected_count is None or actual_count == expected_count):
                    if self.confirmed.get(target) != self.imports[target]:
                        self.confirmed[target] = self.imports[target]
                        self.notice(role, f"Structure read-back confirmed · revision {result['revision']}",
                                    stage="confirmed", target=target, revision=result["revision"])
            return None  # Preserve the actual tool response.

        return {"before_tool_callback": before_tool, "after_tool_callback": after_tool}

    def _invalidate_file(self, sandbox: str, path: str, reason: str) -> None:
        """Invalidate changed bytes and their exports, not unrelated scripts.

        The native write invalidates validation of this path and its exports.
        A new Python source file is not evidence either output file changed.
        """
        path = posixpath.normpath(path)
        affected = [key for key, value in self.outputs.items() if key[0] == sandbox
                    and (key[1] == path or value.get("validated_source") == path)]
        for key in affected:
            self.outputs.pop(key)
            self.receipt_issues[key] = {"path": key[1], "source": path, "reason": reason}

    def require_read_back(self) -> None:
        if self.block_reason is not None:
            self.stop(self.block_reason)
        if self.workflow.blocked:
            self.stop("modelling goal unfinished: " + str(self.workflow.blocked))
        if self.executions or self.pending or self.inline_pending or self.skill_operations or self.delivery_uncertainty:
            self.stop("execution or document outcome remains unconfirmed")
        if any(self.confirmed.get(target) != revision for target, revision in self.imports.items()):
            self.stop("document import has no matching read-back confirmation")
        if self.workflow.active and self.workflow.current != "complete" and not self.workflow.blocked:
            self.stop(f"modelling stage {self.workflow.current} has no completed checkpoint or explicit blocked report")

    def _observe_skill(self, role: str, args: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        script = args.get("script_path") or args.get("script")
        argv = args.get("argv")
        if result.get("exit_code") != 0 or not isinstance(argv, list) or not argv:
            return
        try:
            report = json.loads(result.get("stdout", ""))
        except (ValueError, TypeError):
            return
        if not isinstance(report, dict):
            return
        sandbox = str(args.get("sandbox", ""))
        if script == "scripts/interface_builder.py":
            for candidate in report.get("interface_candidates", []):
                path = candidate.get("file_name") if isinstance(candidate, dict) else None
                if isinstance(path, str):
                    key = (sandbox, posixpath.normpath(path))
                    self._invalidate_file(sandbox, key[1], "candidate_generated")
                    self.outputs[key] = {"sandbox": sandbox, "path": key[1], "phase": "candidate",
                                         "registration_status": "unverified"}
        if script != "scripts/structure_inspect.py":
            return
        if argv[0] in {"validate_structure", "validate_nanotube"}:
            path = report.get("file")
            if isinstance(path, str) and report.get("passed") is True:
                key = (sandbox, posixpath.normpath(path))
                self.outputs[key] = {"sandbox": sandbox, "path": key[1], "phase": "validated",
                    "measurements": report, "execution_sequence": self.execution_sequence}
                self.receipt_issues.pop(key, None)
                self.notice(role, f"Independent file validation passed · {path}", stage="validated",
                            path=path, command_id=result.get("command_id"))
            elif isinstance(path, str) and report.get("passed") is False:
                self._invalidate_file(sandbox, path, "validation_failed")
        if argv[0] == "to_atomsculptor_document":
            source, path = report.get("input_structure_file"), report.get("output_document_file")
            if not isinstance(path, str):
                return
            prior = self.outputs.get((sandbox, posixpath.normpath(source))) if isinstance(source, str) else None
            key = (sandbox, posixpath.normpath(path))
            if prior and prior.get("phase") == "validated":
                self.outputs[key] = {"sandbox": sandbox, "path": key[1], "phase": "exported",
                    "validated_source": posixpath.normpath(source)}
                self.receipt_issues.pop(key, None)
                self.notice(role, f"Validated structure converted · {path}; document import still required",
                            stage="exported", path=path)
            else:
                self.outputs.pop(key, None)
                self.receipt_issues[key] = {"path": path, "source": source, "reason": "missing_source_validation"}
                self.notice(role, f"Conversion receipt missing source validation · {path}",
                            stage="validation_required", **self.receipt_issues[key])
