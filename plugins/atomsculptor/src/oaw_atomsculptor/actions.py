"""Bound model-only work and select executable next actions from real receipts."""
from __future__ import annotations

import json
import posixpath
from copy import deepcopy
from open_agent_world.plugin_api.tool_execution import collect_operation_result, OperationReceiptMismatch
from open_agent_world.plugin_api.sandbox import python_entrypoint


SERIAL_ACTION_INSTRUCTION = (
    "Each response may contain one tool call, or multiple read-only calls. "
    "A call that may change state must be the only tool call in its response, "
    "even when targets or output files differ. Starting or collecting Sandbox work "
    "requires a single-call response even if it preserves the input file. "
    "Unknown operations also require a single-call response."
)


READ_DEPENDENCIES = {"read_tool_result", "read_modelling_source", "read_skill", "read_skills", "preview_file", "inspect_artifacts", "list_resources",
                     "list_toolbox_members", "inspect_sandbox", "list_sandbox_files", "read_sandbox_file", "inspect_atom_structure", "observe_atom_structure"}


def inline_python(argv):
    """Recognize direct Python -c only; never infer computation from a shell."""
    entry = python_entrypoint(argv)
    if entry and entry[0] == "code" and entry[2] == len(argv):
        return entry[1]
    return None


class ExecutionActions:
    def __init__(self):
        self.sandbox_states = {}
        self.progress_key = None
        self.requests_without_progress = 0
        self.last_action = None
        self.allowed = None
        self.recoveries = 0
        self.successful_scripts = set()
        self.observed_executions = set()
        self.assessed_executions = set()
        self.inline_attempts = 0
        self.inline_measurements = []
        self.successful_measurements = set()
        self.validated_files = set()
        self.readbacks = set()
        self.output_token_ceiling = None
        self.response_rejections = []

    def enabled(self, evidence, role):
        return evidence.workflow.active and role == "structure_builder" and not evidence.workflow.blocked

    def environment_action(self, evidence):
        """Keep dependency failures out of scientific repair/replanning."""
        if evidence.workflow.current not in {"probe", "build"}:
            return None
        latest = evidence.latest_experiment()
        environments = evidence.sandbox_environments
        boxes = [latest.sandbox] if latest else list(environments)
        if not latest and any(environments[box].get("ready") for box in boxes):
            return None
        for box in boxes:
            status = environments.get(box, {})
            dependencies = status.get("dependencies") or {}
            if dependencies.get("state") in {"failed", "not_ready"} or status.get("warmup_error"):
                names = evidence.workflow.tool_names or frozenset()
                retry = ("Retry start_sandbox, then inspect_sandbox." if status.get("warmup_error") else
                    "Retry the native install_python_packages with the declared requirements " +
                    json.dumps(dependencies["requirements"]) + ", then inspect_sandbox." if dependencies.get("requirements") else
                    "Retry the declared Pack preparation in the application, or report blocked with the exact environment error.")
                return ("Infrastructure preparation failed for Sandbox " + box + ": " +
                    str(dependencies.get("error") or status.get("warmup_error") or "Declared Pack dependencies are not ready") +
                    ". " + retry + " If preparation cannot complete, report blocked with this receipt. "
                    "Preserve the saved modelling source and method; do not scan interpreters, create another environment, "
                    "or redesign the model to work around missing packages.",
                    {"finish_task"} | ({"install_python_packages", "inspect_sandbox", "start_sandbox", "read_tool_result"} & names))
        return None

    def next_action(self, evidence, *, structure_path=""):
        workflow = evidence.workflow
        names = workflow.tool_names or frozenset()
        exits = {"finish_task", "advance_modelling_stage"}
        if evidence.executions:
            return ("A script dispatch has no confirmed result. Inspect native Sandbox state/receipts; "
                    "do not edit or resubmit it. Report the outcome as unknown/blocked if it cannot be confirmed.",
                    exits | ({"inspect_sandbox"} & names))
        if evidence.pending or evidence.inline_pending or evidence.skill_operations:
            return ("Collect the running script result with wait_sandbox_operation; do not edit or restart it. "
                    "Use cancel_command for a confirmed non-converging command, then collect its termination receipt.",
                    exits | {"wait_sandbox_operation"} | ({"cancel_command"} & names))
        continuation = evidence.continuation or {}
        if continuation.get("script_restored") is False:
            return ("The saved failed execution could not yet be reconciled: " + str(continuation.get("repair_state")) +
                    ". Inspect the scoped native result/file references in HOST continuation or report blocked. "
                    "Do not overwrite the saved script or replay unresolved commands.",
                    {"finish_task"} | ((READ_DEPENDENCIES | {"wait_sandbox_operation"}) & names))
        if workflow.current == "complete":
            return "All workflow checkpoints are complete. Report the actual saved artifacts and confirmed revision with finish_task; do not execute or import again.", {"finish_task"}
        if evidence.closing_reason:
            return ("Run budget closeout: finish_task with actual status=blocked/failed, saved paths, last receipts "
                    "and unmet checks. Do not start another experiment or derivation.", {"finish_task"})
        if evidence.delivery_uncertainty:
            return ("An import outcome is unknown: " + json.dumps(evidence.delivery_uncertainty, ensure_ascii=False) +
                    ". Inspect native tool receipts and the live Structure; do not replay import or delivery. "
                    "If no receipt proves the outcome, finish_task status=blocked with these pending references.",
                    {"finish_task"} | ({"inspect_atom_structure", "inspect_sandbox", "read_tool_result"} & names))
        environment = self.environment_action(evidence)
        if environment:
            return environment
        unconfirmed = {target: revision for target, revision in evidence.imports.items()
                       if evidence.confirmed.get(target) != revision}
        if unconfirmed:
            return ("Import already returned these native revisions: " + json.dumps(unconfirmed) +
                    ". Call inspect_atom_structure to confirm their read-back; do not repeat conversion or import. "
                    "If the live document no longer matches, finish_task status=blocked with the changed revision.",
                    {"finish_task"} | ({"inspect_atom_structure", "read_tool_result"} & names))
        if workflow.current == "deliver" and workflow.delivery == "document":
            targets = [target for target, revision in evidence.imports.items()
                       if evidence.confirmed.get(target) == revision and target not in evidence.delivery_uncertainty]
            if targets:
                return ("Native import and read-back are already confirmed: " + json.dumps({t: evidence.imports[t] for t in targets}) +
                        ". Call advance_modelling_stage(stage='deliver', target=<the confirmed target>), then finish_task. Do not revalidate, regenerate or import again.", exits)
        issue = evidence.delivery_issue
        if issue:
            if issue.get("terminal"):
                return "The delivery Skill rejected the host CLI contract before computation. finish_task with status=blocked/failed and the exact interface error; preserve the validated file and native receipts.", {"finish_task"}
            return ("Correct only the delivery argument fields: " + json.dumps(issue.get("issues", [])) +
                    ". Use declared snake_case keys and JSON values. Omit validate to reuse the existing build validation; no new modelling script is needed.", exits | {"run_delivery_step"})
        latest = evidence.latest_experiment()
        blocker = evidence.steps.current_input_blocker(evidence)
        if blocker:
            return ("Native input dependency blocker (the modelling script was NOT saved or executed): " +
                json.dumps(blocker["diagnosis"], ensure_ascii=False) +
                ". The native guard receipt is the evidence for this blocker, not a new scientific experiment. "
                "Return finish_task status=blocked with the exact field, expected/observed layout and guard command receipt. "
                "Do not regenerate the consumer, repeat this unchanged guard, or review an old science execution.",
                {"finish_task"} | (READ_DEPENDENCIES & names))
        observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
        if observation and observation["plan_revision"] == workflow.plan_revision:
            if observation["outcome"] == "needs_plan_review":
                return "Your recorded assessment requests Planner review. Return its evidence through finish_task status=needs_plan_review.", {"finish_task"}
            if observation["outcome"] == "blocked":
                return "Your recorded assessment reports a blocker. Return it through finish_task status=blocked with retained artifacts.", {"finish_task"}
        protocol = workflow.protocol_issues(evidence)
        if protocol:
            return ("Measurement protocol repair in Builder: " + json.dumps(protocol, ensure_ascii=False) +
                    " Patch only the producer's output fields, reusing saved calculation results. If metadata.oaw_report_file "
                    "exists, load that native report in the producer's reporting branch and re-emit it; use argv to skip "
                    "the scientific calculation. Preserve measured values and failed checks; missing scientific facts need execution. "
                    "Use HOST measurement_contract, not guesses about wrappers or invented objectives. "
                    "This does not consume a scientific Planner review.",
                    exits | {"run_modelling_step", "write_sandbox_text_file", "execute_command"})
        feedback = evidence.correction_feedback
        if (feedback and feedback.get("role") == "structure_builder" and feedback.get("plan_revision") == workflow.plan_revision
                and feedback["kind"] in {"plan_review", "workflow_blocked"}):
            status = "needs_plan_review" if feedback["kind"] == "plan_review" else "blocked"
            return (feedback["message"] + f" Return finish_task status={status} with the current native execution. "
                    "Preserve the original assessment and saved source; do not dispatch another experiment.", {"finish_task"})
        recovery = evidence.steps.target_recovery(evidence)
        if recovery:
            return ("Retained execution-field correction only: " + json.dumps(recovery, ensure_ascii=False) +
                    ". Call resume_modelling_step with these IDs, a single corrected script_path and reason. "
                    "Supply corrections only for rejected execution fields or previous_assessment. "
                    "Use expected_script_path to correct the submitted action; an unexecuted LOCAL subtask's filename "
                    "may instead be explicitly corrected. The rejected action is retained in host memory; "
                    "it saved no changes and dispatched no command. "
                    "Do not resend source, change the scientific question, or guess execution_scope/receipt fields.",
                    {"resume_modelling_step", "finish_task"} | (READ_DEPENDENCIES & names))
        if (feedback and feedback.get("role") == "structure_builder" and feedback.get("plan_revision") == workflow.plan_revision
                and feedback.get("code") == "subtask_contract"):
            retry = feedback["tool"] if feedback["tool"] in {"advance_modelling_stage", "finish_task"} else "advance_modelling_stage"
            return ("Correct only next_experiment's rejected fields: " + feedback["message"] +
                    f" Retry {retry} with the same measured facts and one literal script target. "
                    "No new experiment was registered. Do not generate source or change the stage checks.",
                    {retry, "finish_task"} | (READ_DEPENDENCIES & names))
        issue = evidence.steps.argument_issue
        if issue:
            return (f"Tool argument correction only: {issue['retry_tool']} needs fields {issue['fields']}. "
                    f"Required fields: {issue['required_fields']}. Put actual source/edits in the tool JSON. "
                    "No new script ran; do not replan, derive numerical results, or repeat code in thoughts.",
                    exits | {"run_modelling_step"})
        if workflow.probe_ready(evidence):
            return ("The current input checks have reported pass evidence. This does not change your assessment. "
                    "Choose ready to advance the input checkpoint, continue with a justified input experiment, "
                    "or request review with the actual evidence. Existing artifacts and receipts remain available.", None)
        ready = workflow.ready_execution(evidence)
        if ready is not None and not structure_path:
            structure_path = evidence.experiment_observations[ready.execution_sequence].get("structure_path", "")
        complete = workflow.checkpoint_arguments(evidence, structure_path)
        if complete:
            return ("The current build checkpoint has execution, assessment and independent validation receipts. "
                    "Call advance_modelling_stage with arguments " + json.dumps(complete, ensure_ascii=False) +
                    ". This completes build, then opens delivery tools; stage is the current checkpoint, not the destination. "
                    "Reuse the recorded assessment and validation. No additional script or validation is needed "
                    "for these receipts; further scientific work needs a justified remaining question.", None)
        if ready is not None:
            measured = evidence.measurement_reports.get(ready.execution_sequence, {})
            paths = workflow.execution_artifact_paths(evidence, ready)
            if not paths and not workflow.uses_measurement_contract(evidence):
                paths = [path for (box, path), output in evidence.outputs.items()
                         if box == ready.sandbox and output.get("phase") == "validated"
                         and output.get("execution_sequence", -1) >= ready.execution_sequence]
            observation = evidence.experiment_observations[ready.execution_sequence]
            structure_path = structure_path or observation.get("structure_path", "") or (paths[0] if len(paths) == 1 else "")
            checked = workflow.experiment_checkpoint(evidence, workflow.current, ready.sandbox, ready.path, structure_path)
            arguments = {"stage": workflow.current, "sandbox": ready.sandbox, "script_path": ready.path}
            if structure_path:
                arguments["structure_path"] = structure_path
            advance = "advance_modelling_stage with arguments " + json.dumps(arguments, ensure_ascii=False)
            if checked.get("ok"):
                return ("The current assessment is already ready and its checkpoint evidence is present. Call " +
                        advance + ". Reuse the recorded assessment; do not return subtask_complete or repeat computation.", None)
            issue = checked["error"]
            if issue["code"] == "validation_required" and structure_path:
                return ("The current assessment is already ready. Independently validate the bound candidate " +
                        json.dumps(structure_path) + " with validate_modelling_artifact and task-appropriate checks, then call " +
                        advance + ". Do not resubmit the assessment or return subtask_complete.", None)
            return ("The current assessment is already ready, but its checkpoint needs this correction: " +
                    json.dumps(issue, ensure_ascii=False) + ". Correct only the indicated evidence or argument, then use " +
                    advance + ". Keep the recorded assessment; subtask_complete requires outcome=continue.", None)
        if workflow.current in {"probe", "build"}:
            if not self.sandbox_states and "inspect_sandbox" in names:
                return "Inspect the authorized Sandbox state before the current action.", exits | {"inspect_sandbox", "inspect_atom_structure"}
            unavailable = [(box, state) for box, state in self.sandbox_states.items() if state not in {"ready", "running"}]
            if unavailable and not any(state in {"ready", "running"} for state in self.sandbox_states.values()):
                box, state = unavailable[0]
                if "start_sandbox" not in names:
                    evidence.stop(f"Sandbox {box} is {state}; connect Execute + Start/Stop or start it from its card")
                return f"Start Sandbox {box} ({state}); obtain readiness before computing.", exits | {"start_sandbox"}
        planned = workflow.pending_experiment
        if (planned and planned["plan_revision"] == workflow.plan_revision and planned["stage"] == workflow.current
                and planned.get("origin", "planner_review") != "stage"):
            return ("Use HOST current_subtask target " + json.dumps(planned["artifact"], ensure_ascii=False) +
                    " for the next useful saved result, distinct from the final stage goal. "
                    "Reuse HOST artifact_handoffs; read only a specific missing field. "
                    "If the last execution needs reassessment under this plan, include previous_assessment with the code action. "
                    "The stage checks are final acceptance. If the method spans several computations, save and execute "
                    "the next useful intermediate at this target; compute the first unresolved quantity in code "
                    "and leave choices that depend on it until its result. A partial saved result need not pass all stage checks. "
                    "Before any source is saved, stage=refine with reason and next_experiment can narrow this action "
                    "without claiming a failed execution or changing acceptance. A finite construction may finish directly.", None)
        observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
        if latest and (observation is None or observation["plan_revision"] != workflow.plan_revision):
            from .measurements import constraint_failures
            measured = evidence.measurement_reports.get(latest.execution_sequence, {})
            checks = [row for row in workflow.requirement_coverage(evidence, execution=latest)
                      if row["stage"] == "build"]
            if (workflow.current == "build" and latest.exit_code == 0
                    and measured.get("kind") == "candidate" and not constraint_failures(measured)
                    and checks and all(row["status"] == "reported_pass" for row in checks)):
                paths = workflow.execution_artifact_paths(evidence, latest)
                return ("The execution returned a saved candidate with all current build checks reported pass. "
                        "Briefly assess the actual output with advance_modelling_stage(stage='observe', "
                        "hypothesis=..., measurement=..., outcome=..., next_step=...). "
                        "Choose ready only if justified; independent validation remains required. "
                        "Current artifact paths: " + json.dumps(paths) + ". Reuse this execution; "
                        "do not create another verifier or construction step merely to record the assessment. "
                        "If the reported result has a real unresolved failure, explain it and choose a repair or review.",
                        exits | {"run_modelling_step"})
            if evidence.steps.available:
                return (f"Use returned facts from {latest.path} (command_id={latest.command_id}) for ONE next code action, "
                        "combining its brief previous_assessment with the new/patch/rewrite/rerun tool. "
                        "Use an assessment-only advance_modelling_stage(stage='observe') only for a stage decision, review, "
                        "independent validation, or when no next code action is justified. "
                        "Authorized read tools remain available for missing information, but do not run an unrelated script to unlock them. "
                        "If one small next experiment/repair is justified, use run_modelling_step (new source), "
                        "patch_modelling_step (exact edits), rewrite_modelling_step (full source) or rerun_modelling_step "
                        "(saved script+inputs), with previous_assessment. Keep measurement to facts already in stdout/stderr; "
                        "do not derive the whole future algorithm before recording them. A sample before search is recommended. "
                        "Report an optimization objective only if the current build plan declares it; mark assumption_invalid "
                        "when measured evidence contradicts the method. To finish a stage or request review without "
                        "new code, use advance_modelling_stage or finish_task.", exits | {"run_modelling_step"})
            return (f"Assess the finished experiment {latest.path} with advance_modelling_stage(stage='observe', "
                    f"sandbox='{latest.sandbox}', script_path='{latest.path}', hypothesis=..., measurement=..., "
                    "outcome='continue'/'ready'/'needs_plan_review'/'blocked', next_step=...). "
                    "Use actual stdout/stderr; exit_code=0 alone does not prove the hypothesis. "
                    "Keep this assessment brief. Do no new topology derivation before recording it.", exits)
        if workflow.current == "deliver":
            return ("Deliver the validated candidate with ONE run_delivery_step action: it validates that exact file "
                    "with explicit acceptance parameters, converts it, imports with a fresh revision and reads back, "
                    "checking each native receipt. Use the ordinary tools only for a step the composite cannot express.",
                    None)
        # A successful probe is not a build result. Unexecuted versions still
        # need execution, including a reused artifact after a stage transition.
        scripts = [item for item in evidence.scripts.values()
                   if item.phase == "written" or item.execution_sequence > workflow.anchor]
        if scripts:
            item = scripts[-1]
            if item.phase == "written":
                return (f"Execute the saved file {item.path} in Sandbox {item.sandbox} with direct "
                        "python3 argv; do not derive more geometry or rewrite before seeing its result.", None)
            if workflow.current == "build":
                if item.exit_code != 0:
                    return (f"Inspect actual stderr from {item.path}; patch the failing part and rerun. "
                            "Request plan review with this execution if an assumption is contradicted.", None)
                return (f"Use the measured output of {item.path} to implement ONE remaining construction or "
                        "analysis step in saved Python, execute it, and inspect its result. If this measured subgoal is complete "
                        "and its recorded assessment is continue, use finish_task status=subtask_complete with one next_experiment for a fresh delegation; keep same-goal repairs here. Reuse saved arrays "
                        "and modules; do not restart the whole derivation. When all requested checks are met, "
                        "independently validate the saved structure before advancing build. A partial result "
                        "is useful evidence, not proof of a finished candidate.", None)
            if item.exit_code == 0:
                return ("Compare the actual probe output with its checks. If a check is unmet, patch only "
                        "the failing code using exact edits, then rerun; do not re-derive the "
                        "whole construction. Otherwise advance probe with that saved-script receipt.",
                        None)
            return "Inspect actual stderr, repair the saved probe or request plan review if the assumption is invalid.", None
        if workflow.current == "build":
            action = workflow.pending_experiment or workflow.plan[workflow.index]["experiment"]
            return (f"Implement the next executable result of the committed method in {action['artifact']}; generate known inputs "
                    "and measure derived values in that code. If a later choice depends on an unknown quantity, "
                    "compute it and save the useful intermediate first; decide the dependent code from that output. "
                    "Produce a candidate directly when the finite construction is already concrete. "
                    "The stage checks are final acceptance, not a requirement to solve every uncertainty before this step. "
                    f"Current question: {action['question']} Method: {action['method']} "
                    "Inspect actual candidate failures before choosing a repair. Keep final acceptance checks "
                    "unchanged; stop at the experiment's finite limit.", None)
        action = workflow.plan[0]["experiment"]
        save = ("Save the input-measurement script at " + action["artifact"] + "; answer: " + action["question"] +
                ". Scope=input_measurement: answer the missing input question using a method appropriate to it. "
                "Candidate acceptance belongs to build.")
        if self.inline_attempts < evidence.limits.inline_probe_limit and not self.successful_measurements:
            save = ("You may obtain a missing input using direct argv [python3, -c, short Python source] "
                    f"(prefer under {evidence.limits.inline_probe_chars} characters); print the result instead of "
                    "calculating or enumerating alternatives in thought. You may instead " + save[0].lower() + save[1:] +
                    " An inline measurement is exploratory evidence, not the saved-script stage checkpoint.")
        elif self.inline_measurements:
            save = "Use the actual inline stdout/stderr below to " + save[0].lower() + save[1:]
        return save, None

    def prepare(self, evidence, role, request, bindings):
        workflow = evidence.workflow
        # A blocked workflow has exactly one exit. Collapse the advertised
        # tools to it and say so, instead of letting the model spend turns
        # discovering that every other call now returns workflow_blocked.
        if (role == "structure_builder" and workflow.active
                and (workflow.blocked or evidence.block_reason)):
            reason = str(workflow.blocked or evidence.block_reason)[:400]
            if workflow.direct:
                self.last_action = ("This modelling workflow is blocked: " + reason +
                    ". Report the blocker and the saved artifact paths to the user now; further tool calls "
                    "will only be rejected.")
                self.allowed = None
            else:
                self.last_action = ("This modelling workflow is blocked: " + reason +
                    ". Every other tool call returns workflow_blocked. Use ONE finish_task with a compact JSON "
                    "result: status='blocked', limitations=[the measured blocker], files=[saved artifact paths], "
                    "measurements=[verified facts]. Saved sources, native receipts and Sandbox files remain "
                    "available for the next Run.")
                self.allowed = {"finish_task"}
            config = getattr(request, "config", None)
            if config is not None and bindings is not None:
                declarations = []
                for tool in getattr(config, "tools", None) or []:
                    if getattr(tool, "function_declarations", None) and self.allowed is not None:
                        tool.function_declarations = [d for d in tool.function_declarations
                                                      if evidence.semantic_tool(d.name) in self.allowed]
                    declarations.extend(getattr(tool, "function_declarations", None) or [])
                if declarations:
                    config.tool_config = bindings.types.ToolConfig(function_calling_config=
                        bindings.types.FunctionCallingConfig(mode="ANY"))
            evidence.notice(role, self.last_action, workflow_event="modelling_action", stage="blocked")
            return
        if not self.enabled(evidence, role):
            return
        # Reads, writes, failed or running commands are not successful computation.
        self.successful_scripts.update((i.sandbox, i.path, i.version, evidence.workflow.current)
            for i in evidence.scripts.values() if i.phase == "executed" and i.exit_code == 0
            and i.execution_sequence > evidence.workflow.anchor)
        self.validated_files.update((k, v.get("phase"), v.get("execution_sequence")) for k, v in evidence.outputs.items()
                                    if v.get("phase") in {"validated", "exported"})
        self.readbacks.update(evidence.confirmed.items())
        self.observed_executions.update((i.sandbox, i.path, i.version, i.exit_code, evidence.workflow.current,
            json.dumps(evidence.last_execution_inputs.get((i.sandbox, i.path), {}), sort_keys=True))
            for i in evidence.scripts.values() if i.phase == "executed"
            and i.execution_sequence > evidence.workflow.anchor)
        self.assessed_executions.update((i["sandbox"], i["script_path"], i["script_version"], i["outcome"], i["plan_revision"])
            for i in evidence.experiment_history)
        key = (evidence.workflow.plan_revision, evidence.workflow.current, len(self.observed_executions),
               len(self.assessed_executions), len(self.successful_scripts), len(self.successful_measurements),
               len(self.validated_files), len(self.readbacks))
        if key != self.progress_key:
            self.progress_key, self.requests_without_progress = key, 0
        # Observing host-owned running work is not model-only stagnation and
        # not successful computation either: freeze, never reset, this count.
        if not (evidence.pending or evidence.inline_pending or evidence.skill_operations):
            self.requests_without_progress += 1
        brake = evidence.limits.model_requests_without_progress
        if brake is not None and self.requests_without_progress > brake:
            confirmed = [t for t, revision in evidence.imports.items() if evidence.confirmed.get(t) == revision
                         and t not in evidence.delivery_uncertainty]
            if (evidence.workflow.current == "deliver" and evidence.workflow.delivery == "document"
                    and len(evidence.imports) == len(confirmed) == 1 and not evidence.delivery_uncertainty
                    and not (evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations)):
                advanced = evidence.workflow.advance(evidence, "deliver", target=confirmed[0])
                if not advanced.get("ok"):
                    evidence.stop("verified delivery could not close its native checkpoint")
            elif evidence.workflow.current == "complete":
                evidence.stop("native workflow checkpoints complete, but final reporting stalled")
            else:
                # Honor an explicitly configured legacy request brake. The
                # default count is telemetry, not a scientific failure.
                evidence.stop("no new execution/assessment/checkpoint within the explicitly configured model action budget")
        self.last_action, self.allowed = self.next_action(evidence)
        repair = evidence.workflow.local_repair(evidence) if evidence.steps.available else None
        feedback = evidence.correction_feedback
        requires_closeout = (feedback and feedback.get("plan_revision") == evidence.workflow.plan_revision
                             and feedback.get("kind") in {"plan_review", "workflow_blocked"})
        if (repair and not requires_closeout and not evidence.closing_reason
                and not self.environment_action(evidence)
                and evidence.time_budget()["phase"] != "closeout"
                and not evidence.steps.target_recovery(evidence)
                and not (evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations)):
            self.last_action = "Local execution/format repair: " + json.dumps(repair, ensure_ascii=False)
            self.allowed = {"advance_modelling_stage", "finish_task", "run_modelling_step"} | READ_DEPENDENCIES
        latest = evidence.latest_experiment()
        if (self.allowed is not None and evidence.scripts and evidence.workflow.current in {"probe", "build"}
                and evidence.time_budget()["phase"] != "closeout" and not evidence.closing_reason):
            self.allowed.add("read_modelling_source")
        if (self.allowed is not None and latest is not None and not
                (evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations)
                and evidence.workflow.current in {"probe", "build"}):
            self.allowed |= READ_DEPENDENCIES & (evidence.workflow.tool_names or frozenset())
        if evidence.steps.available and self.allowed is None and evidence.workflow.current in {"probe", "build"}:
            # Present only operations relevant to the current experiment. The
            # ordinary save/execute path remains for small exact text repairs and
            # compatibility; unrelated delivery/team tools wait for that phase.
            current_tools = {"run_modelling_step", "advance_modelling_stage", "finish_task",
                "write_sandbox_text_file", "execute_command", "wait_sandbox_operation", "cancel_command",
                "inspect_sandbox", "inspect_atom_structure", "observe_atom_structure", "read_skill", "read_skills",
                "install_python_packages", "copy_skill_resource"}
            current_tools |= READ_DEPENDENCIES
            if evidence.workflow.current == "build":
                current_tools.add("run_skill_script")
            self.allowed = current_tools
            if evidence.workflow.ready_execution(evidence) is None:
                self.last_action += (" Use run_modelling_step for new scripts (source required in arguments), "
                    "patch_modelling_step for repairs (exact edits required), rerun_modelling_step for saved inputs, "
                    "or rewrite_modelling_step for an explicit replacement. Answer ONE current question with code, measured expected_output "
                    "and a finite stop_condition. The host saves and executes it without another model turn in between. "
                    "Check current_experiment.assessment_status: pending requires one brief assessment, either separately with observe "
                    "or combined using previous_assessment. Recorded means OMIT previous_assessment and proceed using the immutable host record. "
                    "Do not solve later questions before this result. Reuse saved artifacts; patch one measured failure at a time.")
        if (self.allowed is not None and evidence.workflow.current in {"probe", "build"}
                and evidence.artifact_handoffs
                and not self.environment_action(evidence)
                and not evidence.workflow.review_request and not evidence.workflow.blocked and not evidence.closing_reason
                and evidence.time_budget()["phase"] != "closeout"
                and not (evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations)):
            self.allowed.add("validate_modelling_artifact")
            if evidence.workflow.ready_execution(evidence) is None:
                self.last_action += " Independent validation is available for exact HOST artifact paths; reuse validate_modelling_artifact before writing another verifier."
        feedback = evidence.correction_feedback
        if (feedback and feedback.get("role") == role and feedback.get("plan_revision") == evidence.workflow.plan_revision
                and feedback["kind"] in {"workflow_state", "step_contract"}):
            self.last_action = ("The previous action was rejected before command dispatch: " + feedback["message"] +
                               " Correct that protocol/receipt field first; no new experiment ran. " + self.last_action)
        if evidence.time_budget()["phase"] == "closeout":
            self.last_action += (" Run closeout reserve has begun. Validate and deliver an already finished candidate, "
                "or report the actual unfinished checkpoint and saved artifacts. Do not start a new redesign/scan.")
        config = getattr(request, "config", None)
        if self.allowed is not None and any(d.name == "read_tool_result"
                for tool in getattr(config, "tools", None) or []
                for d in getattr(tool, "function_declarations", None) or []):
            # Archive read-back is local and read-only, including while an
            # assessment or plan correction is pending.
            self.allowed.add("read_tool_result")
        self.output_token_ceiling = None
        if (config is not None and evidence.output_recoveries.get(role, 0)
                and evidence.limits.recovered_action_output_tokens is not None):
            # Apply only an explicitly configured recovery cap. Action-focused
            # guidance must not silently shrink OAW's chosen reasoning budget.
            ceiling = evidence.limits.recovered_action_output_tokens
            config.max_output_tokens = min(config.max_output_tokens or ceiling, ceiling)
            self.output_token_ceiling = config.max_output_tokens
        if evidence.output_recoveries.get(role, 0):
            self.last_action += (" Output-limit recovery has been used: follow the current next action "
                                 "using saved facts and native receipts; do not repeat the discarded derivation.")
        if config is not None and bindings is not None:
            declarations = []
            for tool in getattr(config, "tools", None) or []:
                if getattr(tool, "function_declarations", None):
                    if self.allowed is not None:
                        tool.function_declarations = [d for d in tool.function_declarations
                                                      if evidence.semantic_tool(d.name) in self.allowed]
                    # Execution guards group the step tools by their native
                    # effect. Advertisement must retain their distinct modes:
                    # a repair/reuse requires an actual host-confirmed saved source.
                    from .execution_steps import STEP_TOOL_MODES
                    if repair:
                        tool.function_declarations = [d for d in tool.function_declarations
                            if d.name not in STEP_TOOL_MODES or STEP_TOOL_MODES[d.name] in {"patch", "rewrite", "rerun"}]
                    if not evidence.scripts:
                        tool.function_declarations = [d for d in tool.function_declarations
                            if d.name not in STEP_TOOL_MODES or STEP_TOOL_MODES[d.name] == "new"]
                    if not evidence.script_sources:
                        tool.function_declarations = [d for d in tool.function_declarations if d.name != "read_modelling_source"]
                    if evidence.steps.argument_issue:
                        from .execution_steps import STEP_TOOL_MODES
                        tool.function_declarations = [d for d in tool.function_declarations
                            if d.name not in STEP_TOOL_MODES or d.name == evidence.steps.argument_issue["retry_tool"]]
                    declarations.extend(tool.function_declarations)
            if declarations:
                # LiteLLM maps ANY to tool_choice=required. Narrow declarations too,
                # since its adapter does not honor allowed_function_names.
                config.tool_config = bindings.types.ToolConfig(function_calling_config=
                    bindings.types.FunctionCallingConfig(mode="ANY"))
        evidence.notice(role, self.last_action, workflow_event="modelling_action",
                        action_request=self.requests_without_progress, stage=evidence.workflow.current,
                        recovered_output_token_ceiling=self.output_token_ceiling)

    def snapshot(self):
        return {"next_action": self.last_action, "requests_without_progress": self.requests_without_progress,
                "action_recoveries": self.recoveries,
                "action_recovery_policy": "Correct the current rejected action; the cumulative count is telemetry, not a Run limit.",
                "sandbox_states": self.sandbox_states,
                "inline_attempts": self.inline_attempts, "inline_measurements": self.inline_measurements[-2:],
                "recovered_output_token_ceiling": self.output_token_ceiling}

    def deduplicate_starts(self, evidence, role, responses):
        """Collapse identical starts in one accepted response, never across retries.

        Different arguments/targets, other mutations and signed parts stay intact.
        """
        if not self.enabled(evidence, role):
            return
        seen = set()
        omitted = 0
        for response in responses:
            content = getattr(response, "content", None)
            if content is None:
                continue
            retained = []
            for part in content.parts or []:
                call = getattr(part, "function_call", None)
                if (call is not None and evidence.semantic_tool(call.name) == "start_sandbox"
                        and not getattr(part, "thought_signature", None)):
                    key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))
                    if key in seen:
                        omitted += 1
                        continue
                    seen.add(key)
                retained.append(part)
            content.parts = retained
        if omitted:
            evidence.notice(role, f"Coalesced {omitted} duplicate Sandbox start call(s) before dispatch",
                            workflow_event="sandbox_start_coalesced", duplicate_calls=omitted)

    def dependent_batch(self, evidence, parts):
        """Identify mixed/non-read-only batches without guessing dependencies."""
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        readonly = READ_DEPENDENCIES
        return len(calls) > 1 and not all(evidence.semantic_tool(c.name) in readonly for c in calls)

    def prepare_response(self, evidence, parts, request=None, role="structure_builder"):
        """Keep the response/call IDs intact; reject unsafe calls at dispatch.

        Read-only calls in a mixed batch can run. Writes and unknown effects
        still need a single-call response; no dependencies are guessed from argv.
        """
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        mixed = self.dependent_batch(evidence, parts)
        advertised = {d.name for t in getattr(getattr(request, "config", None), "tools", None) or []
                      for d in getattr(t, "function_declarations", None) or []} if request is not None else None
        for call in calls:
            semantic = evidence.semantic_tool(call.name)
            if (self.allowed is not None and semantic not in self.allowed
                    or advertised is not None and call.name not in advertised):
                result = evidence.workflow.error("disallowed_tool", "This tool is unavailable in the current host state.")
                result["error"]["allowed_tools"] = sorted(advertised if advertised is not None else self.allowed)
            elif mixed and semantic not in READ_DEPENDENCIES:
                result = evidence.workflow.error("non_readonly_tool_batch",
                    "This call was not dispatched: state-changing or unknown effects require a single-call response. "
                    "Other read-only calls in this response may return their own results. Use the modelling/delivery composite for dependent steps.")
            else:
                continue
            result["error"].update(call_id=call.id, tool=call.name)
            self.response_rejections.append((role, call.id, call.name, result))

    def rejected_call(self, role, name, call_id):
        for index, (expected_role, expected_id, tool, result) in enumerate(self.response_rejections):
            # SDKs can fill defaults or assign IDs after generation. Every call
            # with this name has the same batch/availability restriction.
            if expected_role == role and tool == name and (not expected_id or expected_id == call_id):
                self.response_rejections.pop(index)
                return deepcopy(result)
        return None

    def recovery_instruction(self, error=None):
        details = " Rejected response: " + json.dumps(error, ensure_ascii=False) + "." if error else ""
        return ("HOST EXECUTION ACTION RECOVERY: " + (self.last_action or "Take the next executable step.") + details +
            " If observed execution contradicts a planning assumption, finish_task with JSON "
            "status=needs_plan_review, reason, sandbox and script_path of that execution. "
            "Otherwise use an authorized tool or report blocked. " + SERIAL_ACTION_INSTRUCTION +
            " Do not repeat derivations. No tools from the discarded response ran. Existing receipts remain authoritative.")


async def collect_script_operation(evidence, definitions, invoke, semantic_name, arguments, result, stopped):
    """Await existing OAW receipts inside one tool action, never rerun code.

    Saved scripts, admitted inline measurements and dependency preparations
    have no intervening model decision. Each observation uses
    the live capability provider; missing permission and errors return normally.
    Run cancellation/deadline remain owned by the runtime and native worker.
    """
    if not evidence.actions.enabled(evidence, "structure_builder") or not isinstance(result, dict):
        return result
    operation = result.get("operation_id")
    if result.get("status") != "running" or not isinstance(operation, str) or not operation:
        return result
    sandbox = str(arguments.get("sandbox", ""))
    argv = arguments.get("argv", [])
    entry = python_entrypoint(argv)
    key = (sandbox, posixpath.normpath(entry[1])) if entry and entry[0] == "script" else None
    saved = semantic_name == "execute_command" and key in evidence.scripts
    inline = semantic_name == "execute_command" and inline_python(argv) is not None
    preparation = semantic_name == "install_python_packages"
    pending = semantic_name == "wait_sandbox_operation" and (operation in evidence.pending or operation in evidence.inline_pending)
    if not (saved or inline or preparation or pending):
        return result
    waiter = next((d for d in definitions if evidence.semantic_tool(d.name) == "wait_sandbox_operation"), None)
    if waiter is None:
        return result
    if saved:
        evidence.pending.setdefault(operation, [(key, evidence.scripts[key].version)])
    def before_wait(operation_id):
        remaining = evidence.remaining_seconds
        if remaining is not None and remaining <= .01:
            evidence.stop("run time budget reached while collecting a Sandbox command")
        return 30 if remaining is None else min(30, remaining)
    try:
        return await collect_operation_result(result, lambda op, seconds: invoke(waiter.capability_id,
            {"sandbox": sandbox, "operation_id": op, "wait_seconds": seconds}), stopped, before_wait=before_wait,
            on_progress=lambda payload: evidence.sandbox_wait_progress(payload, sandbox=sandbox,
                workflow_event="modelling_command_wait"))
    except OperationReceiptMismatch as exc:
        return exc.pending
