"""Run-local planning and execution checkpoints, without domain acceptance claims."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from collections.abc import Mapping
from open_agent_world.plugin_api import AgentRuntimeError

from .operation_request import parse_operation_request


STAGES = ("probe", "build", "deliver")
EXPERIMENT_SCOPES = {"probe": "input_measurement", "build": "candidate_work"}
PLAN_LIMITS = {"result_chars": 12000, "goal_chars": 400, "checks_per_stage": 12,
               "check_chars": 400, "experiment_field_chars": 300}
PLAN_REJECTION_LIMIT = 3
OBSERVATION_LIMITS = {"hypothesis": 400, "measurement": 1200, "next_step": 400}
OBJECTIVE_SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string", "minLength": 1, "maxLength": 200},
    "direction": {"type": "string", "enum": ["minimize", "maximize"]},
    "unit": {"type": "string", "maxLength": 200}},
    "required": ["name", "direction"], "additionalProperties": False,
    "description": "Optional BUILD optimization target only. Ordinary input measurements have no minimize/maximize direction."}

# Advertise an object at the model boundary, then encode once for ADK's
# string-valued finish_task. The host still validates stage order and bounds.
PLAN_RESULT_SCHEMA = {"type": "object", "properties": {
    "status": {"type": "string", "enum": ["planned", "blocked"]},
    "delivery": {"type": "string", "enum": ["document", "files", "report"]},
    "assumptions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 400}},
    "limitations": {"type": "array", "items": {"type": "string"}},
    "stages": {"type": "array", "minItems": 2, "maxItems": 3,
        "description": "Use build, deliver by default. Prepend probe only for a specific missing input that prevents construction; known primitive inputs can be generated and measured in build.", "items": {
        "type": "object", "properties": {
            "id": {"type": "string", "enum": list(STAGES)},
            "goal": {"type": "string", "maxLength": 400},
            "checks": {"type": "array", "minItems": 1, "maxItems": 12,
                       "items": {"type": "string", "maxLength": 400}},
            "experiment": {"type": "object", "properties": {
                "scope": {"type": "string", "enum": list(EXPERIMENT_SCOPES.values())},
                "objective": OBJECTIVE_SCHEMA,
                "expected_output": {"type": "string", "minLength": 1, "maxLength": 300},
                # Constrained tool decoders may not support uniqueItems.
                # validate_plan enforces uniqueness for plans and continuations.
                "input_paths": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 512},
                    "description": "Distinct known HOST artifact paths for this one experiment; uniqueness is checked by the host. Omit for legacy plans."},
                "depends_on": {"type": "array", "items": {"type": "string"},
                    "description": "Completed HOST subtask IDs needed by this experiment. Omit when there are no subtask prerequisites."},
                "evidence_refs": {"type": "array", "items": {"type": "string"},
                    "description": "Assessed HOST execution evidence IDs, e.g. execution:2. Reference measured prerequisites instead of rederiving them."},
                **{key: {"type": "string", "maxLength": 300} for key in
                   ("artifact", "question", "method", "stop_condition")}},
                "required": ["scope", "artifact", "question", "method", "stop_condition"]}},
        "required": ["id", "goal", "checks"]}}}, "required": ["status"]}
PLAN_RESULT_SCHEMA["properties"]["stages"]["items"]["properties"]["experiment"]["properties"]["artifact"]["description"] = (
    "ONE literal workspace-relative Python script path, such as measurements/next step.py. "
    "Use letters, numbers, spaces, dots, underscores or hyphens in the filename. "
    "Put reuse instructions in method and retained data paths in input_paths; do not combine filenames or annotations here.")

# Prose lengths are writing guidance. The existing whole-result boundary still
# protects payload size; a two-character prose overrun is not a failed experiment.
def _prose_guidance(schema):
    if not isinstance(schema, dict):
        return
    if "maxLength" in schema and schema.get("type") == "string":
        size = schema.pop("maxLength")
        schema["description"] = (schema.get("description", "") + f" Aim for {size} characters; preserve the necessary facts.").strip()
    for key, value in schema.items():
        if key in {"objective", "input_paths"}:
            continue  # Identifiers and filesystem bounds are not prose guidance.
        if isinstance(value, dict):
            _prose_guidance(value)
        elif isinstance(value, list):
            for item in value:
                _prose_guidance(item)

_prose_guidance(PLAN_RESULT_SCHEMA)


# Reuse the Planner's action contract for local continuations. A next action
# cannot replace the stage's objective or acceptance checks.
EXPERIMENT_SCHEMA = deepcopy(PLAN_RESULT_SCHEMA["properties"]["stages"]["items"]["properties"]["experiment"])
for _field in ("objective",):
    EXPERIMENT_SCHEMA["properties"].pop(_field)
EXPERIMENT_SCHEMA["required"] = ["scope", "artifact", "question", "method", "expected_output", "stop_condition"]
EXPERIMENT_SCHEMA["additionalProperties"] = False


def script_target(value):
    """A literal executable target, separate from dependency/prose fields."""
    import posixpath
    import unicodedata
    from .measurements import relative_path
    if not relative_path(value) or posixpath.normpath(value) != value or not value.endswith(".py"):
        return False
    stem = posixpath.basename(value)[:-3]
    return bool(stem.strip() and all(c.isalnum() or unicodedata.category(c).startswith("M")
                                    or c in " _.-" for c in stem)
                and not any(unicodedata.category(c).startswith("C") for c in value))


def finite_number(value):
    import math
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def report(value):
    if isinstance(value, str):
        if len(value) > PLAN_LIMITS["result_chars"]:
            return None
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, Mapping) else None


def validate_plan(value):
    """Return the accepted plan or bounded, field-specific repair diagnostics.

    Counts bound context size, not domain acceptance. Never drop checks to fit.
    """
    issues = []

    def issue(field, message, **details):
        if len(issues) < 8:
            issues.append({"field": field, "message": message, **details})

    def text(value, field, maximum):
        if not isinstance(value, str) or not value.strip():
            issue(field, "Must be a nonempty string.")
            return False
        if field.endswith("objective.name") and len(value) > maximum:
            issue(field, "Objective identifier exceeds its measurement-contract bound.", max_chars=maximum)
            return False
        return True

    if isinstance(value, str):
        if len(value) > PLAN_LIMITS["result_chars"]:
            issue("result", "Plan JSON exceeds the total character limit; shorten prose, preserve user checks.",
                  actual_chars=len(value), max_chars=PLAN_LIMITS["result_chars"])
            return None, issues
        try:
            value = json.loads(value)
        except ValueError as exc:
            issue("result", "Invalid JSON: send one JSON-encoded object as the result string.",
                  position=getattr(exc, "pos", None))
            return None, issues
    if not isinstance(value, Mapping):
        issue("result", "Expected a plan object encoded once as JSON; do not double-encode it.")
        return None, issues
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        issue("result", "Plan must contain JSON-compatible values.")
        return None, issues
    if encoded_size > PLAN_LIMITS["result_chars"]:
        issue("result", "Plan exceeds the total character limit; shorten prose, preserve user checks.",
              actual_chars=encoded_size, max_chars=PLAN_LIMITS["result_chars"])
        return None, issues
    if value.get("status") != "planned":
        issue("result.status", "Use 'planned' for a plan, or report an actual blocker with status='blocked'.")
    delivery = value.get("delivery")
    if delivery not in ("document", "files", "report"):
        issue("result.delivery", "Choose 'document', 'files' or 'report' from the original request.")
    stages = value.get("stages")
    stage_ids = tuple(item.get("id") if isinstance(item, Mapping) else None for item in stages) if isinstance(stages, list) else ()
    if stage_ids not in (STAGES, STAGES[1:]):
        issue("result.stages", "Supply build, deliver; prepend probe only for an input unknown that prevents construction.")
        return None, issues
    checked = []
    for index, (expected, item) in enumerate(zip(stage_ids, stages)):
        field = f"result.stages[{index}]"
        if not isinstance(item, Mapping):
            issue(field, "Must be a stage object.")
            continue
        if item.get("id") != expected:
            issue(field + ".id", f"Use '{expected}' at this position.")
        text(item.get("goal"), field + ".goal", PLAN_LIMITS["goal_chars"])
        checks = item.get("checks")
        if not isinstance(checks, list) or not 1 <= len(checks) <= PLAN_LIMITS["checks_per_stage"]:
            issue(field + ".checks", f"Use 1–{PLAN_LIMITS['checks_per_stage']} concise checks; preserve user constraints.",
                  actual_items=len(checks) if isinstance(checks, list) else None,
                  max_items=PLAN_LIMITS["checks_per_stage"])
        else:
            for position, check in enumerate(checks):
                text(check, f"{field}.checks[{position}]", PLAN_LIMITS["check_chars"])
        stage = {"id": expected, "goal": item.get("goal"), "checks": checks}
        if expected != "deliver":
            action = item.get("experiment")
            if not isinstance(action, Mapping):
                issue(field + ".experiment", "Supply artifact, question, method and stop_condition in an object.")
            else:
                for key in ("artifact", "question", "method", "stop_condition"):
                    valid = text(action.get(key), field + ".experiment." + key, PLAN_LIMITS["experiment_field_chars"])
                    if key == "artifact" and valid:
                        if not script_target(action[key]):
                            issue(field + ".experiment.artifact", "Name ONE literal workspace-relative .py script. "
                                  "Put reused files in input_paths/method; do not combine filenames or annotations.")
                if action.get("scope") != EXPERIMENT_SCOPES[expected]:
                    issue(field + ".experiment.scope", f"Use '{EXPERIMENT_SCOPES[expected]}'. " +
                          ("Measure one primitive input; move fitting, matching and candidate search into build."
                           if expected == "probe" else "Use the measured inputs for candidate construction or analysis."))
                stage["experiment"] = {key: action.get(key) for key in
                                       ("scope", "artifact", "question", "method", "stop_condition")}
                if "expected_output" in action:
                    if text(action["expected_output"], field + ".experiment.expected_output", 300):
                        stage["experiment"]["expected_output"] = action["expected_output"]
                if "input_paths" in action:
                    from .measurements import relative_path
                    paths = action["input_paths"]
                    if (not isinstance(paths, list) or len(paths) > 8 or any(not relative_path(p) for p in paths)
                            or len(set(paths)) != len(paths)):
                        issue(field + ".experiment.input_paths", "Use at most eight distinct relative artifact paths.")
                    else:
                        stage["experiment"]["input_paths"] = list(paths)
                for key in ("depends_on", "evidence_refs"):
                    if key in action:
                        refs = action[key]
                        if (not isinstance(refs, list) or any(not isinstance(r, str) or not r.strip() for r in refs)
                                or len(set(refs)) != len(refs)):
                            issue(field + ".experiment." + key, "Use distinct nonempty HOST evidence/subtask IDs.")
                        else:
                            stage["experiment"][key] = list(refs)
                objective = action.get("objective")
                # Old persisted plans may contain diagnostic_limit; routing is
                # evidence-based, so discard that retired setting on loading.
                if objective is not None:
                    field_path = field + ".experiment.objective"
                    if expected != "build":
                        issue(field_path, "Only build optimization declares an objective. Input measurements do not need a direction.")
                    elif (not isinstance(objective, Mapping) or set(objective) - {"name", "direction", "unit"}
                          or objective.get("direction") not in ("minimize", "maximize")
                          or not isinstance(objective.get("unit", ""), str) or len(objective.get("unit", "")) > 200):
                        issue(field_path, "Use {name,direction,unit?} for a meaningful optimization target; omit for ordinary measurements.")
                    elif text(objective.get("name"), field_path + ".name", 200):
                        stage["experiment"]["objective"] = dict(objective)
        checked.append(stage)
    assumptions = value.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 8:
        issue("result.assumptions", "Use at most eight exploratory assumptions, separate from user requirements.")
    else:
        for index, assumption in enumerate(assumptions):
            text(assumption, f"result.assumptions[{index}]", 400)
    parsed = {"stages": checked, "delivery": delivery}
    if assumptions:
        parsed["assumptions"] = assumptions
    return (None, issues) if issues else (parsed, [])


def plan(value):
    return validate_plan(value)[0]


class ModellingWorkflow:
    def __init__(self, request: str, *, direct=False):
        # Typed panel operations already have bounded implementation paths.
        # Unknown free-form modelling is not classified by language keywords.
        self.required = not direct and parse_operation_request(request) is None
        self.direct = direct
        self.plan = None
        self.delivery = "document"
        self.active = False
        self.index = 0
        self.anchor = 0
        self.checkpoints = []
        self.blocked = None
        self.routing_recoveries = 0
        self.planner_dispatches = 0
        self.tool_names = None
        self.missing_tools = []
        self.plan_rejections = 0
        self.repeated_plan_rejections = 0
        self._plan_rejection_fingerprint = None
        self.plan_issues = []
        self.assumptions = []
        self.plan_revision = 0
        self.review_request = None
        self.review_history = []
        self._pending_review = None
        self._progress = {}
        self._failed_experiments = 0
        self.host_signal = None
        self._diagnostic_experiments = 0
        self._invalid_candidates = 0
        self._protocol_repairs = 0
        self._covered_checks = set()
        self._observed_checks = set()
        self.pending_experiment = None
        self.subtask_history = []
        self._subtask_counter = 0
        self._pending_subtask = None
        self.recovery = None
        self._last_scientific_state = None
        self._problem_states = {}

    def prerequisite_state(self, evidence, ref, sandbox=None, row=None):
        """Classify an execution reference against the prerequisite rules.

        Returns None when the reference is usable as prior evidence, else a
        concrete reason. An execution whose review the current plan revision
        already consumed is exactly the evidence that revision was based on;
        keeping it disqualified would deadlock the revised experiment against
        the immutability of the recorded subtask.
        """
        if row is None:
            row = next((r for r in evidence.experiment_history
                        if ref == f"execution:{r['execution_sequence']}"), None)
        if not row:
            return "no assessed execution with that id exists in this Run"
        if sandbox is not None and row.get("sandbox") != sandbox:
            return f"it ran in Sandbox {row.get('sandbox')!r}, not {sandbox!r}"
        if row.get("exit_code") != 0:
            return "its execution did not finish with exit 0"
        if row["execution_sequence"] not in evidence.measurement_reports:
            return "it recorded no measurement report"
        outcome = row.get("outcome")
        if outcome not in {"continue", "ready"}:
            consumed = (outcome == "needs_plan_review"
                        and isinstance(row.get("plan_revision"), int)
                        and row["plan_revision"] < self.plan_revision)
            if not consumed:
                return (f"its assessment outcome is {outcome!r}; only continue/ready executions, or a "
                        "review already consumed by the current plan revision, can be prerequisites")
        return None

    def schedule_experiment(self, experiment, *, origin, evidence=None, committed_sequence=None):
        if self.current not in EXPERIMENT_SCOPES:
            self.pending_experiment = None
            return
        prepared = deepcopy(experiment)
        stripped = []
        if evidence is not None:
            # A recorded subtask is immutable once scheduled; committing one
            # whose evidence_refs the validator can never accept is the
            # measured deadlock. Provenance-only references are dropped here
            # and remain available in review_history and the native archive.
            kept = []
            for ref in prepared.get("evidence_refs", []):
                # Mirror the validator's atomic-observe override: the execution
                # committed by this very assessment is its own valid evidence.
                if (committed_sequence is not None and ref == f"execution:{committed_sequence}"
                        or self.prerequisite_state(evidence, ref) is None):
                    kept.append(ref)
                else:
                    stripped.append(ref)
            if stripped:
                prepared["evidence_refs"] = kept
        self._subtask_counter += 1
        self.pending_experiment = {**prepared, "plan_revision": self.plan_revision,
            "stage": self.current, "origin": origin,
            "subtask_id": f"{self.plan_revision}:{self.current}:{self._subtask_counter}"}
        if stripped:
            self.pending_experiment["stripped_evidence_refs"] = stripped
        self.pending_experiment.setdefault("expected_output", prepared.get("question", ""))

    def record_plan_rejection(self):
        # Count recurrence of one unresolved protocol issue, not all repairs.
        # Different payload lengths/JSON positions are not a resolved issue.
        fingerprint = json.dumps([{k: issue[k] for k in ("field", "message")}
                                  for issue in self.plan_issues], ensure_ascii=False, sort_keys=True)
        self.repeated_plan_rejections = self.repeated_plan_rejections + 1 if fingerprint == self._plan_rejection_fingerprint else 1
        self._plan_rejection_fingerprint = fingerprint
        self.plan_rejections += 1

    def merge_plan_revision(self, value):
        """Merge an anchored current/future delta; frozen stages come from host state."""
        if not isinstance(value, Mapping):
            return value, None
        revision = value.get("plan_revision")
        if revision is not None and (type(revision) is not int or revision != self.plan_revision):
            return None, [{"field": "result.plan_revision", "message": "Use the current HOST plan_revision; stale revisions cannot be committed."}]
        stages = value.get("stages")
        # Legacy complete plans remain supported, with strict frozen-prefix validation.
        if (isinstance(stages, list) and len(stages) == len(self.stage_ids) and
                all(isinstance(s, Mapping) and {"id", "goal", "checks"} <= s.keys() for s in stages)):
            return value, None
        if revision is None:
            return None, [{"field": "result.plan_revision", "message": "An incremental revision requires current HOST plan_revision."}]
        if (not isinstance(stages, list) or not stages or any(not isinstance(s, Mapping) for s in stages)
                or any(s.get("id") not in self.stage_ids[self.index:] for s in stages)
                or len({s.get("id") for s in stages}) != len(stages)):
            return None, [{"field": "result.stages", "message": "Revise distinct current/future stage IDs only; completed stages are supplied by the host."}]
        if set(value) - {"status", "delivery", "assumptions", "stages", "plan_revision"}:
            return None, [{"field": "result", "message": "Use the declared incremental revision fields only."}]
        merged = {"status": value.get("status"), "delivery": value.get("delivery", self.delivery),
                  "assumptions": deepcopy(value.get("assumptions", self.assumptions)), "stages": deepcopy(self.plan)}
        for change in stages:
            if (set(change) - {"id", "goal", "checks", "experiment"} or
                    isinstance(change.get("experiment"), Mapping) and set(change["experiment"]) -
                    set(PLAN_RESULT_SCHEMA["properties"]["stages"]["items"]["properties"]["experiment"]["properties"])):
                return None, [{"field": "result.stages", "message": "Use declared stage/experiment fields; omitted fields retain host values."}]
            stage = merged["stages"][self.stage_ids.index(change["id"])]
            fields = dict(change)
            if isinstance(fields.get("experiment"), Mapping):
                fields["experiment"] = {**stage.get("experiment", {}), **fields["experiment"]}
            stage.update(deepcopy(fields))
        return merged, None

    def dependency_registry(self, evidence):
        done = {r["subtask_id"]: {"id": r["subtask_id"], "execution_sequence": r["execution_sequence"], "stage": r["stage"]}
                for r in [*self.subtask_history, *self.checkpoints] if r.get("subtask_id")}
        # Advertise exactly the references the validator accepts; a divergence
        # here once deadlocked a run between an advertised enum and the rule.
        refs = [f"execution:{r['execution_sequence']}" for r in evidence.experiment_history
                if self.prerequisite_state(evidence, f"execution:{r['execution_sequence']}",
                                           row=r) is None]
        return {"completed_subtasks": list(done.values()), "execution_refs": refs}

    def action_schema(self, schema, evidence):
        schema = deepcopy(schema)
        if evidence is not None:
            registry = self.dependency_registry(evidence)
            for key, values in (("depends_on", [r["id"] for r in registry["completed_subtasks"]]),
                                ("evidence_refs", registry["execution_refs"])):
                field = schema["properties"][key]
                if values:
                    field["items"]["enum"] = values
                else:
                    field["maxItems"] = 0
                field["description"] += " Only the HOST IDs listed here are available; omit when empty."
        return schema

    def validate_next_experiment(self, value, evidence, sandbox, *, assessing=None):
        if self.current not in EXPERIMENT_SCOPES or not isinstance(value, Mapping):
            return None, self.error("subtask_contract", "Supply one next_experiment for the current probe/build stage.")
        if set(value) - set(EXPERIMENT_SCHEMA["properties"]) or any(k not in value for k in EXPERIMENT_SCHEMA["required"]):
            return None, self.error("subtask_contract", "Use the next_experiment fields in the advertised action contract; retain the stage checks.")
        proposed = {"status": "planned", "delivery": self.delivery, "stages": deepcopy(self.plan)}
        proposed["stages"][self.index]["experiment"] = dict(value)
        parsed, issues = validate_plan(proposed)
        if issues:
            error = self.error("subtask_contract", "Correct next_experiment at " + issues[0]["field"] + ": "
                + issues[0]["message"] + " Preserve the measurement and remaining experiment fields; no next action was registered.")
            error["error"]["issues"] = issues
            return None, error
        action = parsed["stages"][self.index]["experiment"]
        rejected = self.validate_dependencies(action, evidence, sandbox, assessing=assessing)
        if rejected:
            return None, rejected
        return action, None

    def validate_dependencies(self, action, evidence, sandbox, *, assessing=None):
        """Reuse OAW's task graph and host-assessed execution facts."""
        from types import SimpleNamespace
        from open_agent_world.task_graph import validate_task_graph
        tasks = [SimpleNamespace(id=r["subtask_id"], title=r["subtask_id"], status="done",
                                 depends_on=r.get("depends_on", []))
                 for r in {r["subtask_id"]: r for r in [*self.subtask_history, *self.checkpoints] if r.get("subtask_id")}.values()]
        tasks.append(SimpleNamespace(id="next_experiment", title="next experiment", status="doing",
                                     depends_on=action.get("depends_on", [])))
        try:
            validate_task_graph(tasks)
        except ValueError as exc:
            return self.error("subtask_dependencies", str(exc))
        refs = set(action.get("evidence_refs", []))
        missing = [path for path in action.get("input_paths", [])
                   if (sandbox, path) not in evidence.artifact_handoffs]
        if missing:
            return self.error("subtask_inputs",
                "These planned input_paths have no HOST artifact receipts: " + ", ".join(missing) +
                ". Fix by exactly ONE route: (1) rerun the producer step so it emits oaw_measurements "
                "artifact records for those files (modelling_io emit_measurements/artifact); "
                "(2) finish_task status=needs_plan_review to revise the plan so this subtask does not "
                "consume them; (3) if an input is unrelated, correct next_experiment.input_paths. "
                "Inspecting files cannot create a receipt.")
        refs.update(f"execution:{evidence.artifact_handoffs[(sandbox, path)]['execution_sequence']}"
                    for path in action.get("input_paths", [])
                    if type(evidence.artifact_handoffs[(sandbox, path)].get("execution_sequence")) is int)
        for ref in refs:
            row = None
            # An observe+next action is one atomic proposal. Its current native
            # receipt can be a prerequisite before the assessment is committed;
            # this never publishes a provisional row or grants task acceptance.
            if assessing and ref == f"execution:{assessing['execution_sequence']}":
                row = assessing
            state = self.prerequisite_state(evidence, ref, sandbox, row=row)
            if state is not None:
                return self.error("subtask_evidence",
                    f"Prerequisite {ref} cannot support a new experiment: {state}. "
                    "Cite an execution with outcome continue/ready and a measurement report, or one whose "
                    "review the current plan revision already consumed; provenance-only references belong "
                    "in the plan text, not evidence_refs.")
        return None

    def execution_subtask(self, evidence, item):
        step = next((r for r in reversed(evidence.steps.records) if r.get("command_id") == item.command_id
                     and r.get("sandbox") == item.sandbox and r.get("source_version") == item.version
                     and r.get("plan_revision") == self.plan_revision and r.get("stage") == self.current), {})
        observed = evidence.experiment_observations.get(item.execution_sequence, {})
        if observed.get("plan_revision") != self.plan_revision:
            observed = {}
        pending = self.pending_experiment or {}
        if pending.get("plan_revision") != self.plan_revision or pending.get("stage") != self.current:
            pending = {}
        return (step.get("subtask_id") or observed.get("subtask_id") or pending.get("subtask_id")
                or f"{self.plan_revision}:{self.current}:execution-{item.execution_sequence}")

    def prepare_subtask_finish(self, value, evidence):
        self._pending_subtask = None
        if set(value) - {"status", "next_experiment", "command_id", "plan_revision", "subtask_id"}:
            return self.error("subtask_fields", "Return status=subtask_complete and next_experiment; the host fills the result facts.")
        item = evidence.latest_experiment()
        observation = evidence.experiment_observations.get(item.execution_sequence, {}) if item else {}
        measured = evidence.measurement_reports.get(item.execution_sequence) if item else None
        has_data = bool(measured and any(measured.get(k) for k in ("values", "measurements", "objective", "constraints", "coverage", "artifacts")))
        if (self.current not in EXPERIMENT_SCOPES or not item or item.exit_code != 0
                or item.execution_sequence <= self.anchor
                or observation.get("plan_revision") != self.plan_revision or observation.get("outcome") != "continue"
                or self.host_signal or self.protocol_issues(evidence) or not has_data):
            return self.error("subtask_evidence", "A local return needs a successful, assessed execution with valid measurements in this stage; local completion never proves stage acceptance.")
        if (evidence.pending or evidence.executions or evidence.inline_pending or evidence.skill_operations
                or evidence.delivery_uncertainty or evidence.steps.active):
            return self.error("subtask_pending", "Collect or resolve outstanding native operations before returning a local result.")
        if self.subtask_history and item.execution_sequence <= self.subtask_history[-1]["execution_sequence"]:
            return self.error("subtask_duplicate", "This local receipt was already returned; execute the next subtask instead of folding it again.")
        subtask_id = self.execution_subtask(evidence, item)
        actual = {"command_id": item.command_id,
                  "plan_revision": self.plan_revision, "subtask_id": subtask_id}
        if ("plan_revision" in value and type(value["plan_revision"]) is not int
                or any(k in value and value[k] != v for k, v in actual.items())):
            return self.error("subtask_receipt", "Omit receipt fields to use host facts; an explicitly stale receipt is rejected.")
        action, rejected = self.validate_next_experiment(value.get("next_experiment"), evidence, item.sandbox)
        if rejected:
            return rejected
        self._pending_subtask = {"status": "subtask_complete", "task_complete": False, "stage_completed": False,
            **actual, "stage": self.current, "sandbox": item.sandbox, "script_path": item.path,
            "execution_sequence": item.execution_sequence, "measurements": evidence.measurement_summary(item.execution_sequence),
            "next_experiment": action, "depends_on": (self.pending_experiment or {}).get("depends_on", []),
            "evidence_refs": (self.pending_experiment or {}).get("evidence_refs", [])}
        return None

    def uses_measurement_contract(self, evidence):
        # An invalid typed record must not permit silently dropping back to
        # legacy unstructured evidence on the next execution.
        return bool(any(set(r) & {"kind", "schema_version", "constraints", "coverage", "artifacts"}
                        for r in evidence.measurement_reports.values())
                    or any(i["code"] == "measurement_contract" for issues in evidence.measurement_issues.values() for i in issues))

    def requirements(self):
        """Stable identities survive reordering and unchanged checks across revisions."""
        return [{"id": stage["id"] + ":" + hashlib.sha256(check.encode()).hexdigest()[:12],
                 "stage": stage["id"], "check": check}
                for stage in self.plan or [] for check in stage["checks"]]

    def requirement_coverage(self, evidence, *, execution=None):
        item = execution if execution is not None else evidence.latest_experiment()
        measured = evidence.measurement_reports.get(item.execution_sequence, {}) if item else {}
        rows = []
        completed = {row["id"]: row for checkpoint in self.checkpoints
                     for row in checkpoint.get("requirement_evidence", [])}
        for requirement in self.requirements():
            if requirement["id"] in completed:
                rows.append(deepcopy(completed[requirement["id"]]))
                continue
            checks = [c for c in measured.get("constraints", []) if requirement["id"] in c.get("check_ids", [])]
            failed = any(not c["passed"] or any(not t["passed"] for t in c.get("controls", [])) or
                         any(measured.get("coverage", {}).get(r, 0) == 0 for r in c.get("regions", [])) for c in checks)
            rows.append({"id": requirement["id"], "stage": requirement["stage"],
                "status": "reported_fail" if failed else "reported_pass" if checks else "unknown",
                "execution_sequence": item.execution_sequence if checks else None,
                "checks": [c["name"] for c in checks]})
        return rows

    def protocol_issues(self, evidence):
        item = evidence.latest_experiment()
        return [issue for issue in evidence.measurement_issues.get(item.execution_sequence, [])
                if issue["code"] == "measurement_contract"] if item else []

    def probe_ready(self, evidence):
        """A satisfied input checkpoint cannot become an open-ended research stage.

        Reuse the existing current-execution coverage and checkpoint gates. This
        does not merge results from different candidates or infer a pass from exit 0.
        """
        if (not self.active or self.current != "probe" or self.blocked or self.host_signal
                or self.review_request is not None or self._pending_review is not None
                or evidence.pending or evidence.executions or evidence.inline_pending
                or evidence.skill_operations or evidence.delivery_uncertainty):
            return False
        item = evidence.latest_experiment()
        if not item or item.phase != "executed" or item.exit_code != 0 or item.execution_sequence <= self.anchor:
            return False
        receipt = evidence.execution_receipts.get(item.execution_sequence, {})
        observation = evidence.experiment_observations.get(item.execution_sequence, {})
        if (receipt.get("plan_revision") != self.plan_revision
                or observation.get("outcome") in {"needs_plan_review", "blocked"}):
            return False
        measured = evidence.measurement_reports.get(item.execution_sequence, {})
        from .measurements import constraint_failures
        if (not self.uses_measurement_contract(evidence) or not measured or measured.get("assumption_invalid")
                or constraint_failures(measured) or evidence.measurement_issues.get(item.execution_sequence)):
            return False
        checks = [row for row in self.requirement_coverage(evidence, execution=item) if row["stage"] == "probe"]
        return bool(checks) and all(row["status"] == "reported_pass" for row in checks)

    def observe_execution(self, evidence, item):
        """Route real failures before another model request, including an unassessed last result."""
        if not self.active or self.current not in {"probe", "build"}:
            return
        from .measurements import constraint_failures
        import re
        measured = evidence.measurement_reports.get(item.execution_sequence, {})
        metric = evidence.measured_objectives.get(item.execution_sequence)
        declared = self.plan[self.index]["experiment"].get("objective") if self.current == "build" else None
        failed_checks = constraint_failures(measured)
        protocol = self.protocol_issues(evidence)
        self._protocol_repairs = self._protocol_repairs + 1 if protocol else 0
        self._failed_experiments = self._failed_experiments + 1 if item.exit_code != 0 else 0
        matching = bool(metric and declared and all(metric.get(k, "") == declared.get(k, "")
                                                    for k in ("name", "direction", "unit")))
        ledger = [r for r in self.requirement_coverage(evidence) if r["stage"] == self.current]
        covered = {r["id"] for r in ledger if r["status"] == "reported_pass"}
        observed = {r["id"] for r in ledger if r["status"] != "unknown"}
        new_facts = (covered - self._covered_checks) | (observed - self._observed_checks)
        self._covered_checks.update(covered)
        self._observed_checks.update(observed)
        required = {r["id"] for r in self.requirements() if r["stage"] == self.current}
        candidate = bool(item.exit_code == 0 and not failed_checks and measured.get("kind") == "candidate"
                         and covered >= required and (not declared or matching))
        self._invalid_candidates = self._invalid_candidates + 1 if failed_checks and measured.get("kind") == "candidate" else 0
        diagnostic = bool(item.exit_code == 0 and not candidate and
                          (measured.get("kind") == "diagnostic" or declared or self.uses_measurement_contract(evidence)))
        if not protocol:
            self._diagnostic_experiments = self._diagnostic_experiments + 1 if diagnostic and not new_facts else 0
        receipt = evidence.execution_receipts.get(item.execution_sequence, {})
        stderr = receipt.get("stderr") or evidence.execution_observations.get((item.sandbox, item.path), "")
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        # Native command IDs, source versions and traceback line numbers cannot
        # disguise the same unresolved cause as scientific progress.
        cause = re.sub(r"line \d+", "line <n>", lines[-1] if lines else f"exit_code={item.exit_code}")
        inputs = {k: v for k, v in evidence.last_execution_inputs.get((item.sandbox, item.path), {}).items()
                  if k not in {"exit_code", "version", "source_version", "script_version", "command_id"}}
        category = ("execution_repair" if item.exit_code != 0 else "protocol_repair" if protocol else
                    "method_contradiction" if measured.get("assumption_invalid") else
                    "constraint_repair" if failed_checks else "diagnostic" if diagnostic else "measured_candidate")
        # Compare scientific identities and results, not prose or display
        # revision labels. Renaming a method/check cannot reset a counterexample.
        def scientific_check(check):
            return {"identity": sorted(check.get("check_ids") or [check["name"]]),
                    "regions": sorted(check.get("regions", [])), "passed": check["passed"],
                    "controls": sorted((c["name"], c["passed"]) for c in check.get("controls", []))}
        checks = sorted((scientific_check(c) for c in measured.get("constraints", [])),
                        key=lambda c: json.dumps(c, sort_keys=True))
        def numeric_facts(value):
            if isinstance(value, dict):
                if "detail_source" in value and "record_pointer" in value:
                    return {"saved_diagnostic_size": value.get("size")}
                return {k: fact for k, v in value.items() if (fact := numeric_facts(v)) is not None}
            if isinstance(value, list):
                return [fact for v in value if (fact := numeric_facts(v)) is not None]
            return value if type(value) in {bool, int, float} else None
        state = {"category": category, "cause": cause if item.exit_code != 0 else None,
                 "inputs": inputs, "environment_revision": evidence.environment_revisions.get(item.sandbox, 0),
                 "objective": metric, "constraints": checks,
                 "coverage": measured.get("coverage", {}),
                 "reported_numeric_facts": numeric_facts({k: measured.get(k, {}) for k in ("values", "measurements")})}
        fingerprint = json.dumps(state, sort_keys=True, default=str)
        previous = self._last_scientific_state
        problem = (self.current, category, cause if item.exit_code != 0 else
                   tuple(sorted(tuple(sorted(c.get("check_ids") or [c["name"]])) for c in failed_checks)))
        prior_problem = self._problem_states.get(problem, set())
        unchanged = bool(fingerprint in prior_problem and not new_facts)
        self.recovery = {"category": category, "fingerprint": fingerprint, "unchanged_evidence": unchanged,
            "execution_sequence": item.execution_sequence, "source_changed": bool(previous and
                (previous["source_key"] != (item.sandbox, item.path) or previous["source_version"] != item.version)),
            "next_action": "Repair only the listed measurement fields; keep the scientific method." if protocol else
                           "Inspect the actual failure and patch one cause, then execute." if item.exit_code != 0 else
                           "Choose one next experiment using the measured facts."}
        # Protocol correction is not scientific failure and does not erase the
        # last scientific state. Network retries never enter this observer.
        if not protocol:
            self._last_scientific_state = {"fingerprint": fingerprint, "source_version": item.version,
                "source_key": (item.sandbox, item.path)}
            self._problem_states.setdefault(problem, set()).add(fingerprint)
        reason = None
        if item.exit_code == 0 and measured.get("assumption_invalid"):
            reason = "Executed method contradiction: " + measured["contradiction"]
        elif unchanged and category in {"constraint_repair", "diagnostic"}:
            reason = "The same unresolved " + category + " recurred without changed inputs, environment or reported numeric/check evidence; review this cause and method."
        if item.exit_code == 0 and matching and not failed_checks and measured.get("kind") not in {"diagnostic", "baseline"}:
            key = (self.current, metric["name"], metric["direction"], metric.get("unit", ""),
                   measured.get("measurement_revision", "legacy"))
            best, stagnant = self._progress.get(key, (None, 0))
            improving = best is None or (metric["value"] < best if metric["direction"] == "minimize" else metric["value"] > best)
            self._progress[key] = (metric["value"] if improving else best, 0 if improving else stagnant + 1)
            if not improving and unchanged:
                reason = "The same measured objective and evidence recurred without improvement; choose another method."
        if reason:
            self.recovery["next_action"] = "Return this native evidence for Planner review; preserve valid intermediate data."
            self.host_signal = {"reason": reason, "outcome": "needs_plan_review", "recovery": deepcopy(self.recovery),
                "execution_sequence": item.execution_sequence, "plan_revision": self.plan_revision}
            evidence.notice("structure_builder", reason, workflow_event="execution_review_signal", **self.host_signal)

    def local_repair(self, evidence):
        """An executed code/contract fault is repaired before designing another experiment."""
        item = evidence.latest_experiment()
        if (not item or self.current not in EXPERIMENT_SCOPES or self.review_request or self.host_signal
                or self.blocked or evidence.steps.current_input_blocker(evidence)
                or item.execution_sequence <= self.anchor
                or evidence.execution_receipts.get(item.execution_sequence, {}).get("plan_revision") != self.plan_revision):
            return None
        observation = evidence.experiment_observations.get(item.execution_sequence, {})
        if observation.get("outcome") in {"blocked", "needs_plan_review"}:
            return None
        if item.exit_code != 0 or self.protocol_issues(evidence):
            return {"sandbox": item.sandbox, "script_path": item.path,
                    "execution_sequence": item.execution_sequence,
                    "next_action": "Read the actual error/source and patch one cause. Keep the scientific method; first obtain a successful diagnostic/round-trip. Rewrite requires an explicit reason why exact edits cannot express the repair."}
        return None

    def configure_tools(self, names):
        self.tool_names = frozenset(names)

    def preflight(self):
        if self.tool_names is None:  # Pure receipt/unit-test callers have no graph snapshot.
            return
        required = {"write_sandbox_text_file", "execute_command"}
        if self.delivery != "report":
            required.add("run_skill_script")
            if not {"read_skill", "read_skills"} & self.tool_names:
                required.add("read_skill")
        if self.delivery == "document":
            required.update({"import_atom_structure_file", "inspect_atom_structure"})
        self.missing_tools = sorted(required - self.tool_names)
        if self.missing_tools:
            self.blocked = "Missing authorized tools: " + ", ".join(self.missing_tools)
            raise AgentRuntimeError("AtomSculptor capability preflight blocked: " + self.blocked +
                ". Connect the Agent to a writable/executable Sandbox, the structure-inspect Skill "
                "(Read + Run, paired with that Sandbox), and the requested Atom Structure "
                "(Inspect + Modify), as applicable. No Builder task was dispatched; authorization was not bypassed.")

    @property
    def stage_ids(self):
        return tuple(stage["id"] for stage in self.plan) if self.plan is not None else STAGES

    @property
    def current(self):
        return self.stage_ids[self.index] if self.index < len(self.stage_ids) else "complete"

    def snapshot(self, *, execution_focus=False):
        view = {"required": self.required, "active": self.active,
                "plan_source": None if self.direct else "planner",
                "plan": self.plan, "current_stage": self.current,
                "delivery": self.delivery, "checkpoints": self.checkpoints, "blocked": self.blocked,
                "routing_recoveries": self.routing_recoveries, "planner_dispatches": self.planner_dispatches,
                "builder_delegation": "blocked" if self.blocked else
                    "awaiting_plan_review" if self.review_request is not None else
                    "available" if not self.required or self.plan is not None else "awaiting_planner",
                "missing_tools": self.missing_tools, "plan_limits": PLAN_LIMITS,
                "plan_rejections": self.plan_rejections, "plan_issues": self.plan_issues,
                "repeated_plan_rejections": self.repeated_plan_rejections,
                "assumptions": self.assumptions, "plan_revision": self.plan_revision,
                "review_request": self.review_request, "review_history": self.review_history,
                "local_checkpoints": [{k: row[k] for k in ("subtask_id", "stage", "execution_sequence", "script_path")}
                                      for row in self.subtask_history[-3:]]}
        if execution_focus:
            # Preserve all acceptance checks, but avoid repeatedly assigning the
            # whole future algorithm to each Builder model request. Full plan
            # remains in native logs and the Planner/coordinator context.
            view["plan"] = [self.plan[self.index]] if self.plan and self.index < len(self.plan) else []
            view["remaining_stages"] = list(self.stage_ids[self.index:])
            view["acceptance_checks"] = {item["id"]: item["checks"] for item in self.plan or []}
            # Full history belongs to native records / the stable frame.
            view.pop("review_history", None)
            view["checkpoints"] = [{k: v for k,v in row.items() if k not in {"observations", "requirement_evidence"}}
                                   for row in self.checkpoints]
        view["host_execution_signal"] = self.host_signal
        view["recovery"] = deepcopy(self.recovery)
        view["diagnostic_progress"] = {"steps_without_valid_candidate": self._diagnostic_experiments,
            "consecutive_invalid_candidates": self._invalid_candidates,
            "consecutive_protocol_repairs": self._protocol_repairs,
            "covered_build_check_ids": sorted(self._covered_checks),
            "observed_build_check_ids": sorted(self._observed_checks),
            "review_policy": "repeated_cause_without_evidence_change"}
        return view

    def prepare_request(self, role, request, evidence=None):
        if role == "structure_builder" and self.active:
            for tool in getattr(request.config, "tools", None) or []:
                for declaration in getattr(tool, "function_declarations", None) or []:
                    from .execution_steps import STEP_TOOL_MODES, step_tool_schema
                    if declaration.name in STEP_TOOL_MODES:
                        declaration.parameters = None
                        declaration.parameters_json_schema = step_tool_schema(declaration.name)
                        latest = evidence.latest_experiment() if evidence is not None else None
                        observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
                        if latest and (observation is None or observation["plan_revision"] != self.plan_revision):
                            declaration.parameters_json_schema["required"].append("previous_assessment")
                        if latest is None:
                            # There is no execution to assess yet. Do not send
                            # an irrelevant nested receipt contract in every
                            # new-script declaration (or invite invented IDs).
                            declaration.parameters_json_schema["properties"].pop("previous_assessment")
                        else:
                            declaration.parameters_json_schema["properties"]["previous_assessment"]["description"] = (
                                "Record the latest unassessed execution once, or use stage=observe separately. "
                                "If already recorded under this plan, OMIT this field: the host reuses its original assessment. "
                                "A redundant matching receipt is accepted without overwriting the record.")
                    if declaration.name == "finish_task" and self.current in EXPERIMENT_SCOPES:
                        declaration.parameters = None
                        declaration.parameters_json_schema = {"type": "object", "properties": {"result": {
                            "type": "object", "properties": {
                                "status": {"type": "string", "enum": ["subtask_complete", "needs_plan_review", "blocked", "failed"]},
                                "next_experiment": self.action_schema(EXPERIMENT_SCHEMA, evidence),
                                **{key: {"type": "string"} for key in ("reason", "sandbox", "script_path", "command_id", "subtask_id")},
                                "plan_revision": {"type": "integer"},
                                "limitations": {"type": "array", "items": {"type": "string"}}},
                            "required": ["status"], "description": "subtask_complete requires next_experiment and a valid measured local result; it does NOT complete the workflow. The host fills the local result from native receipts."}},
                            "required": ["result"], "additionalProperties": False}
                        if evidence is not None and evidence.steps.available and self.local_repair(evidence):
                            declaration.parameters_json_schema["properties"]["result"]["properties"]["status"]["enum"] = ["blocked", "failed"]
                    if declaration.name == "advance_modelling_stage":
                        declaration.parameters = None
                        declaration.parameters_json_schema = {"type": "object", "properties": {
                            "stage": {"type": "string", "enum": ["observe", *STAGES, "blocked"]},
                            **{key: {"type": "string", **({"description": (
                                "Optional for stage=observe: the host fills the latest finished execution. "
                                "Required with stage=probe/build receipts.")} if key in ("sandbox", "script_path") else {})}
                               for key in ("sandbox", "script_path", "structure_path", "target", "reason")},
                            **{key: {"type": "string", "maxLength": 12000,
                                     "description": f"Required for stage=observe; aim for {limit} characters."}
                               for key, limit in OBSERVATION_LIMITS.items()},
                            "outcome": {"type": "string", "enum": ["continue", "ready", "needs_plan_review", "blocked"],
                                "description": "Required for stage=observe; ready does not replace independent validation."}},
                            "required": ["stage"]}
                        from .execution_steps import ASSESSMENT_SCHEMA
                        for key in ("objective", "assumption_invalid", "next_experiment"):
                            declaration.parameters_json_schema["properties"][key] = deepcopy(ASSESSMENT_SCHEMA["properties"][key])
                        declaration.parameters_json_schema["properties"]["next_experiment"] = self.action_schema(EXPERIMENT_SCHEMA, evidence)
                        latest = evidence.latest_experiment() if evidence is not None else None
                        observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
                        delivery_confirmed = (self.current == "deliver" and evidence is not None and
                            any(evidence.confirmed.get(t) == revision and t not in evidence.delivery_uncertainty
                                for t, revision in evidence.imports.items()))
                        needs_assessment = bool(latest and (observation is None or observation["plan_revision"] != self.plan_revision)
                                                and not delivery_confirmed)
                        if needs_assessment:
                            schema = declaration.parameters_json_schema
                            schema["properties"] = {key: schema["properties"][key] for key in
                                ("stage", "sandbox", "script_path", "hypothesis", "measurement", "outcome", "next_step", "objective", "assumption_invalid", "next_experiment")}
                            schema["properties"]["stage"]["enum"] = ["observe"]
                            schema["required"] = [k for k in schema["properties"] if k not in
                                {"objective", "assumption_invalid", "sandbox", "script_path", "next_experiment"}]
                            schema["additionalProperties"] = False
                        if self.current == "deliver" and not needs_assessment:
                            schema = declaration.parameters_json_schema
                            schema["properties"] = {key: schema["properties"][key] for key in ("stage", "target", "reason")}
                            schema["properties"]["stage"]["enum"] = ["deliver"]
                            schema["properties"]["target"]["description"] = "The exact Structure target already imported and read back; copy the native document_confirmations target."
                            schema["required"] = ["stage", "target"] if self.delivery == "document" else ["stage"]
                            schema["additionalProperties"] = False
                    if declaration.name in {"run_delivery_step", "validate_modelling_artifact"}:
                        from .delivery import validation_schema
                        field = "validate" if declaration.name == "run_delivery_step" else "checks"
                        schema = declaration.parameters_json_schema or declaration.parameters
                        if isinstance(schema, dict):
                            schema["properties"][field] = validation_schema()
                        elif schema is not None:
                            # ADK generated Schema becomes a regular JSON schema
                            # so explicit acceptance keys/types survive adapters.
                            declaration.parameters_json_schema = schema.model_dump(mode="json", exclude_none=True)
                            declaration.parameters_json_schema["properties"][field] = validation_schema()
                        if field == "checks":
                            projected = declaration.parameters_json_schema
                            if projected:
                                projected["properties"][field]["description"] = "Explicit independent acceptance parameters; path are bound by the host."
                        if declaration.parameters_json_schema is not None:
                            declaration.parameters = None
                    if declaration.name == "resume_modelling_step":
                        from .execution_steps import TARGET_RECOVERY_SCHEMA
                        declaration.parameters = None
                        declaration.parameters_json_schema = deepcopy(TARGET_RECOVERY_SCHEMA)
                        recovery = evidence.steps.target_recovery(evidence) if evidence is not None else None
                        if recovery:
                            for key in ("subtask_id",):
                                declaration.parameters_json_schema["properties"][key]["enum"] = [recovery[key]]
        if role == "planner" and self.required and (self.plan is None or self.review_request is not None):
            for tool in getattr(request.config, "tools", None) or []:
                for declaration in getattr(tool, "function_declarations", None) or []:
                    if declaration.name == "finish_task":
                        declaration.parameters = None
                        schema = deepcopy(PLAN_RESULT_SCHEMA)
                        if self.review_request is not None:
                            stage = schema["properties"]["stages"]["items"]
                            stage["required"] = ["id"]
                            stage["properties"]["id"]["enum"] = list(self.stage_ids[self.index:])
                            stage["properties"]["experiment"]["required"] = []
                            stage["properties"]["experiment"] = self.action_schema(stage["properties"]["experiment"], evidence)
                            schema["properties"]["stages"].update(minItems=1, maxItems=len(self.stage_ids)-self.index,
                                description="Only changed current/future stages. Omitted fields retain host values; completed stages are supplied by the host.")
                            schema["properties"]["plan_revision"] = {"type": "integer", "enum": [self.plan_revision]}
                            schema["required"] = ["status", "plan_revision", "stages"]
                        declaration.parameters_json_schema = {"type": "object",
                            "properties": {"result": schema}, "required": ["result"], "additionalProperties": False}
            return
        if role != "atom_sculptor" or not self.required or (self.plan is not None and self.review_request is None):
            return
        for tool in getattr(request.config, "tools", None) or []:
            declarations = getattr(tool, "function_declarations", None)
            if declarations:
                tool.function_declarations = [d for d in declarations if d.name not in {
                    "structure_builder"}]
        if isinstance(getattr(request, "tools_dict", None), dict):
            for name in ("structure_builder",):
                request.tools_dict.pop(name, None)

    def accept_delegations(self, evidence, role, parts):
        if role != "atom_sculptor" or self.direct:
            return None
        names = [p.function_call.name for p in parts if getattr(p, "function_call", None)]
        if self.required and (self.plan is None or self.review_request is not None) and any(n in {"structure_builder"} for n in names):
            return self.error("planner_required", "Call planner before Builder, sequentially. "
                "Return the original request; Planner supplies probe/build/deliver checkpoints. "
                "No tool from this rejected response was dispatched.")
        planner_calls = sum(n in {"planner"} for n in names)
        if self.planner_dispatches + planner_calls > 2 + len(self.review_history):
            evidence.stop("Planner delegation limit exceeded before dispatch")
        self.planner_dispatches += planner_calls
        if any(n in {"structure_builder"} for n in names):
            self.before_delegation("structure_builder")
            for part in parts:
                call = getattr(part, "function_call", None)
                if (call is not None and call.name in {"structure_builder"}
                        and not getattr(part, "thought_signature", None)):
                    args = dict(call.args or {})
                    args["request"] = (evidence.original_request + "\n\nHOST EXECUTION HANDOFF (not new user requirements):\n" +
                        json.dumps({"plan_revision": self.plan_revision, "current_stage": self.current}, ensure_ascii=False) +
                        "\nExecute only the current measured step. Do not derive or implement the whole future pipeline "
                        "before that step returns. The host retains the full plan and original acceptance checks." +
                        ("\nNEXT EXECUTABLE ACTION:\n" + json.dumps(evidence.working_decision(), ensure_ascii=False)
                         if self.current in EXPERIMENT_SCOPES else ""))
                    call.args = args
        return None

    def error(self, code, message):
        kind = ("plan_review" if code in {"plan_review_required", "plan_review_limit", "assessment_conflict"}
                else "workflow_blocked" if code == "workflow_blocked" else "workflow_state")
        return {"ok": False, "error": {"code": code, "message": message,
                "current_stage": self.current, "kind": kind, "command_dispatched": False}}

    def before_delegation(self, name):
        if name not in {"structure_builder"} or not self.required:
            return None
        if self.plan is None:
            return self.error("planner_required", "Call planner first for free-form/custom modelling. "
                "Parameters being complete does not establish an executable algorithm. "
                "Planner must return the probe/build/deliver plan through finish_task.")
        if self.review_request is not None:
            return self.error("plan_review_required", "Return the measured review_request to Planner before resuming Builder. "
                              "Keep the original user request and existing receipts unchanged.")
        self.preflight()
        self.active = True
        return None

    def before_tool(self, role, name, args, evidence=None):
        if (role == "structure_builder" and evidence is not None and self.pending_experiment
                and name == "execute_command"):
            rejected = self.validate_dependencies(self.pending_experiment, evidence, str(args.get("sandbox", "")))
            if rejected:
                return rejected
        if role == "planner" and self.required and (self.plan is None or self.review_request is not None) and name == "finish_task":
            result = report(args.get("result"))
            if result and result.get("status") == "blocked":
                if isinstance(args.get("result"), Mapping):
                    args["result"] = json.dumps(args["result"], ensure_ascii=False, separators=(",", ":"))
                return None
            parsed, self.plan_issues = validate_plan(args.get("result"))
            candidate = args.get("result")
            if self.review_request is not None:
                candidate, merge_issues = self.merge_plan_revision(report(candidate))
                parsed, self.plan_issues = (None, merge_issues) if merge_issues else validate_plan(candidate)
            if parsed is not None and self.review_request is not None:
                if (parsed["delivery"] != self.delivery or parsed["stages"][:self.index] != self.plan[:self.index]
                        or tuple(s["id"] for s in parsed["stages"]) != self.stage_ids):
                    parsed = None
                    self.plan_issues = [{"field": "result.stages", "message":
                        "Preserve completed stages and requested delivery; revise only the current and remaining stages."}]
                elif evidence is not None:
                    paths = parsed["stages"][self.index].get("experiment", {}).get("input_paths", [])
                    unknown = [p for p in paths if not any(path == p for _, path in evidence.artifact_handoffs)]
                    if unknown:
                        parsed = None
                        self.plan_issues = [{"field": f"result.stages[{self.index}].experiment.input_paths",
                            "message": "Use HOST artifact receipt paths; no producer receipt for: " + ", ".join(unknown)}]
            if parsed is None:
                self.record_plan_rejection()
                response = self.error("plan_required", "Plan rejected at " + self.plan_issues[0]["field"] + ": " +
                    self.plan_issues[0]["message"] + " Fix only the listed fields and retain valid stages/checks. "
                    "Prefer an object in finish_task.result; a legacy string must encode the object once. Unicode is supported. "
                    "Do not change delivery or remove user constraints to guess a different format.")
                response["error"].update(issues=self.plan_issues, limits=PLAN_LIMITS)
                return response
            # ADK declares result as string. Tolerate a valid object without
            # committing until the real SDK finish succeeds; serialize once.
            if isinstance(candidate, Mapping):
                args["result"] = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if not self.active or role not in {"structure_builder", "atom_sculptor"}:
            return None
        if evidence is not None:
            repair = self.local_repair(evidence) if evidence.steps.available else None
            if repair and name == "write_sandbox_text_file" and not evidence.steps.active and str(args.get("path", "")).endswith(".py"):
                if args.get("path") != repair["script_path"] or args.get("sandbox") != repair["sandbox"] or not args.get("edits"):
                    return self.error("local_repair_required", "Repair the current saved source with exact edits; a full rewrite needs rewrite_modelling_step with rewrite_reason.")
            pending = self.pending_experiment
            if (pending and pending["plan_revision"] == self.plan_revision and pending["stage"] == self.current
                    and role == "structure_builder" and pending.get("origin", "planner_review") != "stage"):
                path = args.get("script_path") if name == "run_modelling_step" else args.get("path") if name == "write_sandbox_text_file" else None
                argv = args.get("argv")
                if name == "execute_command" and isinstance(argv, list) and len(argv) >= 2:
                    from open_agent_world.plugin_api.sandbox import python_entrypoint
                    entry = python_entrypoint(argv)
                    if entry and entry[0] == "script":
                        path = entry[1]
                if isinstance(path, str) and path.endswith(".py") and path != pending["artifact"] and not self.protocol_issues(evidence):
                    rejected = self.error("planned_experiment_required", "script_path does not match the current subtask's artifact. "
                        "Correct the target field; no code was saved or executed. Do not rewrite the calculations.")
                    rejected["error"].update(field="script_path", expected_script_path=pending["artifact"],
                        actual_script_path=path, subtask_id=pending["subtask_id"])
                    return rejected
            latest = evidence.latest_experiment()
            from .delivery import is_independent_validation
            independent_validation = name == "run_skill_script" and is_independent_validation(args, evidence)
            signal = self.host_signal
            if signal and signal["plan_revision"] == self.plan_revision and name != "finish_task" and not (
                    name == "advance_modelling_stage" and args.get("stage") == "observe"):
                return self.error("plan_review_required", signal["reason"] + " Return the native evidence through finish_task.")
            observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
            if observation and observation["outcome"] == "needs_plan_review" and observation["plan_revision"] == self.plan_revision and name not in {"finish_task"}:
                return self.error("plan_review_required", "Return the measured contradiction through finish_task "
                    "status=needs_plan_review before new construction; preserve the executed source and measurements.")
            if observation and observation["outcome"] == "blocked" and observation["plan_revision"] == self.plan_revision and name != "finish_task":
                return self.error("workflow_blocked", "Report the measured blocker through finish_task; do not resume construction implicitly.")
            if latest and (observation is None or observation["plan_revision"] != self.plan_revision) and name in {"run_modelling_step", "write_sandbox_text_file", "execute_command", "run_skill_script",
                                                          "import_atom_structure_file", "replace_atom_structure"}:
                if independent_validation or name == "run_modelling_step" and isinstance(args.get("previous_assessment"), Mapping):
                    return None  # Composite validates and commits before native operations.
                return self.error("experiment_observation_required", "First assess the finished script with "
                    "advance_modelling_stage(stage='observe', sandbox, script_path, hypothesis, measurement, outcome, next_step). "
                    "Record actual evidence before another experiment; exit_code=0 is not acceptance.")
        if name == "finish_task":
            value = report(args.get("result"))
            if value and value.get("status") == "subtask_complete":
                if role != "structure_builder":
                    return self.error("subtask_role", "Only the executing Builder can return a local checkpoint.")
                rejected = self.prepare_subtask_finish(value, evidence)
                if rejected:
                    return rejected
                args["result"] = json.dumps(self._pending_subtask, ensure_ascii=False, separators=(",", ":"))
                return None
            if value and value.get("status") == "needs_plan_review":
                self._pending_review = None
                if evidence and evidence.steps.available and self.local_repair(evidence):
                    return self.error("execution_repair_required", "The current execution/serialization fault needs a local code repair, not a scientific Planner review. Use patch_modelling_step or report blocked with the actual limitation.")
                blocker = evidence.steps.current_input_blocker(evidence) if evidence else None
                if blocker:
                    response = self.error("input_dependency_blocked", "The native input guard failed before modelling execution. "
                        "Return finish_task status=blocked with this guard diagnosis; an old science execution is not new review evidence.")
                    response["error"]["diagnosis"] = blocker["diagnosis"]
                    return response
                latest = evidence.latest_experiment() if evidence else None
                observation = evidence.experiment_observations.get(latest.execution_sequence, {}) if latest else {}
                if (evidence and self.protocol_issues(evidence) and not self.host_signal
                        and not observation.get("assumption_invalid") and value.get("assumption_invalid") is not True):
                    response = self.error("measurement_contract", "Repair the producer's listed measurement fields in Builder; "
                        "this does not consume a scientific Planner review. Finish blocked only if the protocol cannot be repaired.")
                    response["error"]["issues"] = self.protocol_issues(evidence)
                    return response
                if self.review_request is not None:
                    return self.error("plan_review_required", "Complete the pending Planner review before requesting another.")
                reason = value.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    response = self.error("review_reason_required", "finish_task.result.reason must describe the measured contradiction or invalid assumption; aim for 800 characters.")
                    response["error"].update(field="finish_task.result.reason",
                        actual_chars=len(reason) if isinstance(reason, str) else None)
                    return response
                import posixpath
                value = dict(value)
                sandbox = value.get("sandbox") or (latest.sandbox if latest else None)
                script_path = value.get("script_path") or (latest.path if latest else None)
                value.update(sandbox=sandbox, script_path=script_path)
                if not isinstance(sandbox, str) or not isinstance(script_path, str):
                    return self.error("review_execution_required", "Supply the observed sandbox and script_path strings.")
                item = evidence.scripts.get((sandbox, posixpath.normpath(script_path))) if evidence else None
                if (not item or item.phase != "executed" or type(item.exit_code) is not int
                        or item.execution_sequence <= self.anchor or self.current not in {"probe", "build"}):
                    return self.error("review_execution_required", "Request review with sandbox and script_path of an observed finished execution.")
                if any(r.get("execution_sequence") == item.execution_sequence and r.get("stage") == self.current
                       for r in self.review_history):
                    return self.error("review_evidence_unchanged", "This execution was already reviewed. Execute the revised experiment or report its concrete blocker; a new delegation is not new evidence.")
                self._pending_review = {"reason": reason, "stage": self.current,
                    "sandbox": item.sandbox, "script_path": item.path, "script_version": item.version,
                    "exit_code": item.exit_code, "execution_sequence": item.execution_sequence,
                    "observations": evidence.execution_observations.get((item.sandbox, item.path), "")}
                from copy import deepcopy
                self._pending_review.update(measurements=deepcopy(evidence.measurement_reports.get(item.execution_sequence)),
                    measurement_issues=deepcopy(evidence.measurement_issues.get(item.execution_sequence, [])),
                    working_decision=evidence.working_decision(),
                    assumptions_to_reconsider=deepcopy(self.assumptions),
                    review_question="Which assumption or representation does this executed counterexample test? "
                        "Preserve valid intermediate results; choose one experiment that changes that premise, "
                        "rather than renaming the same algorithm. Untested assumptions remain hypotheses.")
                args["result"] = json.dumps(value, ensure_ascii=False)
                return None
            if value and value.get("status") in {"blocked", "failed"}:
                return None
        if self.blocked:
            return self.error("workflow_blocked", "This modelling workflow is blocked; report the actual blocker.")
        if name == "finish_task" and self.current != "complete":
            return self.error("stage_incomplete", "Complete the current stage with advance_modelling_stage "
                "and actual receipts, or finish with JSON status=blocked/failed and the actual limitation.")
        argv = args.get("argv") or []
        command = argv[0] if isinstance(argv, list) and argv else None
        if (name == "run_skill_script" and command in {"validate_structure", "validate_nanotube"} and self.current == "probe"
                and (evidence is None or not is_independent_validation(args, evidence))):
            return self.error("stage_incomplete", "Execute a saved probe script and advance probe before candidate validation.")
        if ((name == "run_skill_script" and command == "to_atomsculptor_document")
                or name in {"import_atom_structure_file", "replace_atom_structure"}):
            if self.current != "deliver":
                return self.error("stage_incomplete", "Finish probe and build checkpoints before conversion/import.")
        return None

    def observe_finish(self, role, args, response, evidence=None):
        # Only a real SDK finish_task success commits the report, not thoughts
        # or a coordinator's paraphrase/deferred task marker.
        if response != "Task completed." and not (isinstance(response, Mapping) and response.get("result") == "Task completed."):
            if role in {"structure_builder", "atom_sculptor"}:
                self._pending_review = None
                self._pending_subtask = None
            return
        # Host-filled receipts can exceed the model's plan/result text bound.
        # Commit only the exact prevalidated SDK argument, without reparsing it
        # through a limit intended for untrusted model-authored plans.
        pending = self._pending_subtask if role == "structure_builder" else None
        local_result_matches = bool(pending and args.get("result") == json.dumps(pending, ensure_ascii=False, separators=(",", ":")))
        value = pending if local_result_matches else report(args.get("result"))
        if role == "planner" and self.required and (self.plan is None or self.review_request is not None):
            if value and value.get("status") == "blocked":
                self.blocked = value.get("limitations") or "Planner reported blocked"
                return
            parsed = plan(value)
            if parsed:
                reviewed = self.review_request is not None
                self.plan = parsed["stages"]
                self.delivery = parsed["delivery"]
                self.assumptions = parsed.get("assumptions", [])
                self.plan_revision += 1
                self._progress.clear()
                self._covered_checks.clear()
                self._observed_checks.clear()
                self._failed_experiments = 0
                self._diagnostic_experiments = 0
                self._invalid_candidates = 0
                self.host_signal = None
                self.review_request = None
                self.plan_rejections = 0
                self.repeated_plan_rejections = 0
                self._plan_rejection_fingerprint = None
                if self.current in EXPERIMENT_SCOPES:
                    self.schedule_experiment(self.plan[self.index]["experiment"], origin="planner_review" if reviewed else "stage", evidence=evidence)
        elif (self.active and role == "structure_builder" and value
              and value.get("status") == "subtask_complete" and local_result_matches):
            checkpoint = self._pending_subtask
            self._pending_subtask = None
            self.subtask_history.append(checkpoint)
            self.schedule_experiment(checkpoint["next_experiment"], origin="subtask", evidence=evidence)
        elif (self.active and role in {"structure_builder", "atom_sculptor"} and value
              and value.get("status") == "needs_plan_review" and self._pending_review is not None):
            self.review_request = self._pending_review
            self.review_history.append({"plan_revision": self.plan_revision, **self.review_request})
            self._pending_review = None
        elif self.active and role in {"structure_builder", "atom_sculptor"} and value and value.get("status") in {"blocked", "failed"}:
            self.blocked = value.get("limitations") or value.get("status")

    def assess_previous(self, evidence, assessment, *, reuse_recorded=False):
        from .execution_steps import ASSESSMENT_SCHEMA
        if not isinstance(assessment, Mapping) or set(assessment) - set(ASSESSMENT_SCHEMA["properties"]):
            return self.error("assessment_fields", "Supply the previous assessment fields only.")
        if any(k not in assessment for k in ASSESSMENT_SCHEMA["required"]):
            return self.error("assessment_fields", "Supply the required measured assessment fields.")
        for key in ASSESSMENT_SCHEMA["required"]:
            if not isinstance(assessment[key], str) or not assessment[key].strip():
                return self.error("assessment_fields", "Required assessment fields must be nonempty strings.")
        if not isinstance(assessment.get("structure_path", ""), str):
            return self.error("assessment_fields", "structure_path must be a string.")
        latest = evidence.latest_experiment()
        if latest is None:
            return self.error("execution_required", "No finished experiment is awaiting assessment; run a saved script first.")
        # The host already knows which execution is latest. A missing receipt
        # field is filled from that record instead of forcing the model to echo
        # identifiers it can only copy; a contradictory value still rejects the
        # whole step so stale results are never assessed as current.
        assessment = dict(assessment)
        for field, actual in (("command_id", latest.command_id), ("sandbox", latest.sandbox),
                              ("script_path", latest.path)):
            supplied = assessment.get(field)
            if supplied is None or (isinstance(supplied, str) and not supplied.strip()):
                assessment[field] = actual
            elif supplied != actual:
                return self.error("assessment_receipt",
                                  "Assess the latest confirmed execution: command_id="
                                  f"{latest.command_id}, sandbox={latest.sandbox}, script_path={latest.path}. "
                                  "Omit these receipt fields and the host fills them; stale results cannot be assessed.")
        if type(assessment.get("complete_stage", False)) is not bool:
            return self.error("assessment_fields", "complete_stage must be boolean.")
        if (assessment["outcome"] not in {"continue", "ready", "needs_plan_review", "blocked"}
                or type(assessment.get("assumption_invalid", False)) is not bool):
            return self.error("assessment_fields", "Use the declared outcome and boolean assumption_invalid.")
        objective = assessment.get("objective")
        if objective is not None and (not isinstance(objective, Mapping) or set(objective) - {"name", "value", "direction", "unit"}
                or not isinstance(objective.get("name"), str) or not objective["name"].strip()
                or len(objective["name"]) > 200 or not finite_number(objective.get("value"))
                or objective.get("direction") not in {"minimize", "maximize"}
                or not isinstance(objective.get("unit", ""), str) or len(objective.get("unit", "")) > 200):
            return self.error("assessment_fields", "Use a finite measured objective with name, direction and optional unit.")
        if ("plan_revision" in assessment and (type(assessment["plan_revision"]) is not int
                or assessment["plan_revision"] != self.plan_revision)):
            return self.error("assessment_receipt", "The assessed plan revision is stale. "
                              "Use the current_experiment receipt; omit copied receipt fields to use host facts.")
        previous = evidence.experiment_observations.get(latest.execution_sequence)
        if reuse_recorded and previous and previous["plan_revision"] == self.plan_revision:
            if (previous["script_version"] != latest.version or previous["command_id"] != latest.command_id
                    or previous["sandbox"] != latest.sandbox or previous["script_path"] != latest.path):
                return self.error("assessment_receipt", "Stored assessment does not match the current execution receipt.")
            if ((assessment.get("assumption_invalid") is True or assessment["outcome"] in {"needs_plan_review", "blocked"})
                    and previous["outcome"] not in {"needs_plan_review", "blocked"}):
                return self.error("assessment_conflict", "Keep the original assessment. Report the new contradiction "
                                  "through finish_task status=needs_plan_review/blocked with this execution; do not run new code.")
            if ("next_experiment" in assessment and assessment["next_experiment"] != previous.get("next_experiment")):
                return self.error("assessment_conflict", "The recorded next_experiment is immutable. Return a new subtask through finish_task with the confirmed local result.")
            # Consume the immutable host record, never replace its measurements,
            # objective, outcome or progress with redundant model wording.
            from copy import deepcopy
            result = {"ok": True, "observation": deepcopy(previous), "assessment_reused": True,
                      "next_stage": self.current, "next_action": previous["next_step"]}
        else:
            result = self.advance(evidence, "observe", **{k: v for k, v in assessment.items()
                if k not in {"command_id", "complete_stage", "structure_path", "script_version", "plan_revision"}})
        if result.get("ok") and assessment.get("complete_stage") and result.get("checkpoint_status") != "completed":
            return self.advance(evidence, self.current, assessment["sandbox"], assessment["script_path"],
                                structure_path=assessment.get("structure_path", ""), reason=result["observation"]["measurement"])
        return result

    def advance(self, evidence, stage, sandbox="", script_path="", structure_path="", target="", reason="",
                hypothesis="", measurement="", outcome="", next_step="", objective=None, assumption_invalid=False, next_experiment=None):
        import posixpath
        if not self.active:
            return self.error("workflow_not_active", "This operation has no staged custom-modelling workflow.")
        if stage == "observe":
            if not sandbox.strip() or not script_path.strip():
                # Same host-authoritative fill as assess_previous: the latest
                # finished execution is the only assessable one anyway.
                latest = evidence.latest_experiment()
                if latest is None:
                    return self.error("execution_required",
                                      "No finished experiment is awaiting assessment; run a saved script first.")
                sandbox = sandbox.strip() or latest.sandbox
                script_path = script_path.strip() or latest.path
            item = evidence.scripts.get((sandbox, posixpath.normpath(script_path)))
            latest = evidence.latest_experiment()
            if not item or item is not latest:
                return self.error("execution_required", "Assess the latest finished version in the current stage, "
                                  "not an unexecuted edit or an older command.")
            issues = []
            for field, value in (("hypothesis", hypothesis), ("measurement", measurement), ("next_step", next_step)):
                maximum = 12000
                if not isinstance(value, str) or not value.strip():
                    issues.append({"field": field, "message": "Supply a nonempty string.", "max_chars": maximum})
                elif len(value) > maximum:
                    issues.append({"field": field, "message": "Shorten this field only.",
                                   "actual_chars": len(value), "max_chars": maximum})
            if outcome not in {"continue", "ready", "needs_plan_review", "blocked"}:
                issues.append({"field": "outcome", "message": "Choose continue, ready, needs_plan_review or blocked."})
            if type(assumption_invalid) is not bool:
                issues.append({"field": "assumption_invalid", "message": "Use a boolean."})
            if objective is not None:
                if (not isinstance(objective, Mapping) or set(objective) - {"name", "value", "direction", "unit"}
                    or not isinstance(objective.get("name"), str) or not objective["name"].strip()
                    or len(objective["name"]) > 200 or objective.get("direction") not in ("minimize", "maximize")
                    or not finite_number(objective.get("value"))
                    or not isinstance(objective.get("unit", ""), str) or len(objective.get("unit", "")) > 200):
                    issues.append({"field": "objective", "message": "Use name, finite value, minimize/maximize and optional unit."})
            if issues:
                response = self.error("observation_required", "Fix only the listed assessment fields; retain actual measurements. "
                    "This is an execution assessment, not another derivation or independent validation.")
                response["error"]["issues"] = issues
                return response
            if (evidence.steps.available and item.exit_code != 0 and self.local_repair(evidence)
                    and (assumption_invalid or outcome == "needs_plan_review")):
                return self.error("execution_repair_required", "This command failed before a successful diagnostic. Record the actual error with continue or blocked, then repair it locally; a scientific review needs executed diagnostic evidence.")
            probe_completed = outcome in {"continue", "ready"} and not assumption_invalid and self.probe_ready(evidence)
            if probe_completed:
                # Complete the existing checkpoint before interpreting another
                # proposed probe. Its checks are already satisfied; the committed
                # build contract owns construction, not an invented extra input test.
                outcome, next_experiment = "ready", None
                next_step = "Input checks are satisfied. Construct the first candidate using the committed build experiment and retained artifacts."
            if next_experiment is not None:
                if outcome != "continue":
                    return self.error("subtask_contract", "next_experiment is for continuing this stage; use ready/review for a stage decision.")
                _, rejected = self.validate_next_experiment(next_experiment, evidence, sandbox,
                    assessing={"execution_sequence": item.execution_sequence, "sandbox": item.sandbox,
                               "exit_code": item.exit_code, "outcome": outcome})
                if rejected:
                    return rejected
            previous = evidence.experiment_observations.get(item.execution_sequence)
            if previous and previous["plan_revision"] == self.plan_revision:
                return self.error("observation_already_recorded", "Keep the recorded assessment; execute a new version "
                                  "for new evidence or return the measured contradiction to Planner.")
            # Signals guide replanning, never domain acceptance. New file names
            # and exit 0 do not reset stagnation of the same measured objective.
            review_reason = self.host_signal["reason"] if self.host_signal and self.host_signal["plan_revision"] == self.plan_revision else None
            if assumption_invalid:
                review_reason = "Executed evidence invalidates a planning assumption."
            if outcome == "needs_plan_review" and self.protocol_issues(evidence) and not review_reason:
                outcome = "continue"
                next_step = "Repair the measurement fields in HOST current_experiment.measurement_issues; retain the scientific plan and saved data."
            reported_objective = objective
            measured = evidence.measured_objectives.get(item.execution_sequence)
            # A model's placeholder/guess is not a measured value. Native
            # stdout carries the named objective; no task-specific oracle.
            objective = measured
            if review_reason and outcome in {"continue", "ready"}:
                # A "ready" claim after measured failures or stagnation is the
                # pathological case: the host forces the review path on real
                # receipts instead of trusting the self-report.
                outcome = "needs_plan_review"
            row = {"sandbox": sandbox, "script_path": item.path, "script_version": item.version,
                   "execution_sequence": item.execution_sequence, "exit_code": item.exit_code,
                   "command_id": item.command_id, "hypothesis": hypothesis, "measurement": measurement,
                   "outcome": outcome, "next_step": next_step, "source": "Builder assessment of host-observed execution"}
            row["plan_revision"] = self.plan_revision
            row["subtask_id"] = self.execution_subtask(evidence, item)
            row["stage"] = self.current
            row["assumption_invalid"] = assumption_invalid
            measurement_report = evidence.measurement_reports.get(item.execution_sequence)
            if measurement_report:
                from .measurements import constraint_failures
                row["candidate_eligible"] = self.current == "build" and not constraint_failures(measurement_report) and measurement_report.get("kind") not in {"diagnostic", "baseline"}
                if measurement_report.get("measurement_revision"):
                    row["measurement_revision"] = measurement_report["measurement_revision"]
            if evidence.measurement_issues.get(item.execution_sequence):
                row["measurement_issues"] = evidence.measurement_issues[item.execution_sequence]
            if objective is not None:
                row["objective"] = dict(objective)
            if reported_objective is not None:
                row["reported_objective"] = dict(reported_objective)
                row["objective_status"] = "native_stdout" if measured == reported_objective else "unavailable_or_mismatch"
            if review_reason:
                row["review_reason"] = review_reason
            if next_experiment is not None and outcome == "continue":
                row["next_experiment"] = deepcopy(next_experiment)
                self.schedule_experiment(next_experiment, origin="assessment", evidence=evidence,
                                         committed_sequence=item.execution_sequence)
            elif self.pending_experiment and self.pending_experiment.get("origin") == "stage":
                self.pending_experiment = None
            evidence.experiment_records[(item.execution_sequence, self.plan_revision)] = row
            evidence.notice("structure_builder", "Experiment assessed · " + outcome,
                workflow_event="modelling_observation", **row)
            result = {"ok": True, "observation": row, "next_stage": self.current,
                    "next_action": "Return needs_plan_review through finish_task" if outcome == "needs_plan_review" else next_step}
            if probe_completed:
                result["probe_completed"] = True
            if outcome == "ready" and self.current in {"probe", "build"}:
                checkpoint = self.advance(evidence, self.current, sandbox, script_path,
                                          structure_path=structure_path, reason=measurement)
                result["checkpoint_status"] = "completed" if checkpoint.get("ok") else "awaiting_evidence"
                result["checkpoint_result"] = checkpoint
                result["next_stage"] = self.current
                if not checkpoint.get("ok"):
                    result["next_action"] = checkpoint["error"]["message"]
            return result
        if self.review_request is not None or self._pending_review is not None:
            return self.error("plan_review_required", "Complete Planner review before advancing; execution receipts remain available.")
        if stage in STAGES and self.host_signal and self.host_signal["plan_revision"] == self.plan_revision:
            return self.error("plan_review_required", self.host_signal["reason"] + " Return the executed evidence to Planner before advancing.")
        if stage == "blocked" and reason.strip():
            self.blocked = reason[:800]
            return {"ok": True, "status": "blocked", "reason": self.blocked}
        # A ready assessment may already have advanced this exact receipt.
        # Repeated confirmation is idempotent, never a different execution.
        prior = next((p for p in self.checkpoints if p["stage"] == stage), None)
        if prior and stage in {"probe", "build"} and not self.blocked:
            item = evidence.scripts.get((sandbox, posixpath.normpath(script_path)))
            if item and all(prior.get(k) == v for k, v in {
                    "script_version": item.version, "command_id": item.command_id,
                    "execution_sequence": item.execution_sequence}.items()):
                return {"ok": True, "checkpoint": deepcopy(prior), "next_stage": self.current, "checkpoint_reused": True}
        if self.blocked or stage != self.current:
            return self.error("stage_order", "Advance only the current stage; blocked workflows cannot resume implicitly.")
        proof = {"stage": stage}
        if stage in {"probe", "build"}:
            item = evidence.scripts.get((sandbox, posixpath.normpath(script_path)))
            if (not item or item.phase != "executed" or type(item.exit_code) is not int or item.exit_code != 0
                    or item.execution_sequence <= self.anchor):
                return self.error("execution_required", "Save and successfully execute this stage's Python script. "
                    "An older stage's execution, failed command or unexecuted edit cannot advance it.")
            observation = evidence.experiment_observations.get(item.execution_sequence)
            if observation is None or observation["plan_revision"] != self.plan_revision:
                return self.error("experiment_observation_required", "Record this execution with stage='observe' "
                    "before advancing. Keep numeric measurement in code and the assessment concise.")
            if evidence.experiment_observations[item.execution_sequence]["outcome"] in {"needs_plan_review", "blocked"}:
                return self.error("plan_review_required", "Resolve the reported contradiction/blocker before advancing.")
            measured = evidence.measurement_reports.get(item.execution_sequence, {})
            from .measurements import constraint_failures
            if constraint_failures(measured):
                return self.error("candidate_constraints", "This executed candidate reports failed hard constraints. "
                    "Do not promote its objective/best state; repair it or request review, then validate the actual new candidate.")
            if evidence.measurement_issues.get(item.execution_sequence):
                return self.error("measurement_contract", "Repair the malformed measurement record before claiming this stage checkpoint.")
            # Versioned/typed evidence opts into requirement coverage. Retained
            # legacy unstructured Runs keep their independent native gates.
            typed = self.uses_measurement_contract(evidence)
            if stage == "probe" and typed:
                missing = [r["id"] for r in self.requirement_coverage(evidence, execution=item)
                           if r["stage"] == stage and r["status"] != "reported_pass"]
                if missing:
                    response = self.error("requirement_evidence", "The assessed PROBE execution has not covered every probe check. "
                        "Use its actual populations and HOST check_ids; a later build result cannot replace this evidence.")
                    response["error"]["missing_check_ids"] = missing
                    return response
            if stage == "build" and typed:
                missing = [r["id"] for r in self.requirement_coverage(evidence, execution=item)
                           if r["stage"] == "build" and r["status"] != "reported_pass"]
                if measured.get("kind") != "candidate" or missing:
                    response = self.error("requirement_evidence", "The current candidate has not covered every BUILD requirement. "
                        "Measure the unknown/failed checks on this candidate; emit constraints.check_ids from HOST requirements. "
                        "Local tests and a connected graph alone do not complete a task.")
                    response["error"].update(missing_check_ids=missing, expected_kind="candidate")
                    return response
                if self.delivery != "report" and not any(a["path"] == structure_path for a in measured.get("artifacts", [])):
                    return self.error("requirement_artifact", "Bind these checks to the actual structure_path in measurements.artifacts before validating/delivering.")
            proof.update(sandbox=sandbox, script_path=item.path, script_version=item.version,
                         command_id=item.command_id, execution_sequence=item.execution_sequence,
                         requirement_evidence=[r for r in self.requirement_coverage(evidence, execution=item) if r["stage"] == stage],
                         observations=evidence.execution_observations.get((sandbox, item.path), ""))
            if stage == "build" and self.delivery != "report":
                output = evidence.outputs.get((sandbox, posixpath.normpath(structure_path)))
                if (not output or output.get("phase") != "validated"
                        or output.get("execution_sequence", -1) < item.execution_sequence):
                    return self.error("validation_required", "Run the authorized structure-inspect validation "
                        "on this candidate file before advancing build. Executing your own script is not independent validation.")
                if typed and not any(a["path"] == output["path"]
                                     for a in measured.get("artifacts", [])):
                    return self.error("requirement_artifact", "Requirement evidence and independent validation must refer to the same structure file.")
                proof.update(structure_path=output["path"])
        elif stage == "deliver":
            if self.delivery == "document":
                if not target:
                    targets = list(evidence.imports)
                    if len(targets) == 1:
                        target = targets[0]  # Native identity only; never guess among targets.
                    else:
                        response = self.error("delivery_target_required", "Supply target from the native document import/confirmation records; no unique target can be inferred.")
                        response["error"].update(field="target", imported_targets=targets)
                        return response
                if target in evidence.delivery_uncertainty:
                    return self.error("delivery_import_unknown", "Inspect the unresolved native import receipt and target before closing delivery; an earlier confirmation cannot settle an unknown later write.")
                if target not in evidence.imports or evidence.confirmed.get(target) != evidence.imports[target]:
                    response = self.error("read_back_required", "Inspect this target after import and confirm its native revision before advancing deliver.")
                    response["error"].update(target=target, imported_revision=evidence.imports.get(target),
                                             confirmed_revision=evidence.confirmed.get(target))
                    return response
                proof.update(target=target, revision=evidence.imports[target])
            else:
                proof.update(delivery=self.delivery, build_checkpoint=self.checkpoints[-1])
        else:
            return self.error("stage_order", "The workflow is already complete.")
        if stage in EXPERIMENT_SCOPES:
            proof["subtask_id"] = self.execution_subtask(evidence, item)
        self.anchor = evidence.execution_sequence
        self.checkpoints.append(proof)
        self.index += 1
        if self.current in EXPERIMENT_SCOPES:
            self.schedule_experiment(self.plan[self.index]["experiment"], origin="stage", evidence=evidence)
        else:
            self.pending_experiment = None
        evidence.notice("structure_builder", f"Modelling stage completed · {stage} → {self.current}",
                        workflow_event="modelling_stage", stage=stage, next_stage=self.current)
        return {"ok": True, "checkpoint": proof, "next_stage": self.current}
