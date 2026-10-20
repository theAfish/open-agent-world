"""Bound model-only work and select executable next actions from real receipts."""
from __future__ import annotations

import json
import posixpath
from open_agent_world.plugin_api.tool_execution import collect_operation_result, OperationReceiptMismatch
from open_agent_world.plugin_api.sandbox import python_entrypoint


READ_DEPENDENCIES = {"read_tool_result", "read_modelling_source", "read_skill", "read_skills", "preview_file", "inspect_artifacts", "list_resources",
                     "list_toolbox_members", "inspect_sandbox", "inspect_atom_structure", "observe_atom_structure"}


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

    def enabled(self, evidence, role):
        return evidence.workflow.active and role == "structure_builder" and not evidence.workflow.blocked

    def next_action(self, evidence):
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
        signal = workflow.host_signal
        if signal and signal["plan_revision"] == workflow.plan_revision:
            return (f"Native execution signal: {signal['reason']} Return finish_task status={signal['outcome']} "
                    "with reason, sandbox and script_path of this execution; retain measurements for Planner.", {"finish_task"})
        protocol = workflow.protocol_issues(evidence)
        if protocol:
            return ("Measurement protocol repair in Builder: " + json.dumps(protocol, ensure_ascii=False) +
                    " Patch only the producer's output fields, reusing saved calculation results. Preserve failed checks. "
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
            return ("All committed input checks have measured pass evidence. Complete this input checkpoint now with "
                    "advance_modelling_stage(stage='observe', outcome='ready', hypothesis=..., measurement=..., next_step=...). "
                    "A new script matching the committed build artifact may instead combine previous_assessment and construction "
                    "in run_modelling_step; the host advances the checkpoint before executing it. "
                    "Use the existing receipt; construction is the next stage. Do not add another input verifier or remeasure these facts.",
                    exits | {"run_modelling_step"})
        planned = workflow.pending_experiment
        if (planned and planned["plan_revision"] == workflow.plan_revision and planned["stage"] == workflow.current
                and planned.get("origin", "planner_review") != "stage"):
            return (("Planner review retained the native facts and rejected method. Execute the next minimal experiment: "
                    if planned.get("origin", "planner_review") == "planner_review" else "Execute the current HOST subtask contract: ")
                    + json.dumps(planned, ensure_ascii=False) + ". Reuse HOST artifact_handoffs; read only a specific missing field. "
                    "If the last execution needs reassessment under this plan, include previous_assessment with the code action. "
                    "Do not repeat a full inventory or design later stages before this result.",
                    exits | {"run_modelling_step", "patch_modelling_step", "rewrite_modelling_step", "rerun_modelling_step"}
                    | (READ_DEPENDENCIES & names))
        observation = evidence.experiment_observations.get(latest.execution_sequence) if latest else None
        if latest and (observation is None or observation["plan_revision"] != workflow.plan_revision):
            if evidence.steps.available:
                return (f"Use returned facts from {latest.path} (command_id={latest.command_id}) for ONE next code action, "
                        "combining its brief previous_assessment with the new/patch/rewrite/rerun tool. "
                        "Use an assessment-only advance_modelling_stage(stage='observe') only for a stage decision, review, "
                        "independent validation, or when no next code action is justified. "
                        "Authorized read tools remain available for missing information, but do not run an unrelated script to unlock them. "
                        "If one small next experiment/repair is justified, use run_modelling_step (new source), "
                        "patch_modelling_step (exact edits), rewrite_modelling_step (full source) or rerun_modelling_step "
                        "(saved script+inputs), with previous_assessment. Keep measurement to facts already in stdout/stderr; "
                        "do not derive the whole future algorithm before recording them. Use one sample before search. "
                        "Report an optimization objective only if the current build plan declares it; mark assumption_invalid "
                        "when measured evidence contradicts the method. To finish a stage or request review without "
                        "new code, use advance_modelling_stage or finish_task.", exits | {"run_modelling_step"})
            return (f"Assess the finished experiment {latest.path} with advance_modelling_stage(stage='observe', "
                    f"sandbox='{latest.sandbox}', script_path='{latest.path}', hypothesis=..., measurement=..., "
                    "outcome='continue'/'ready'/'needs_plan_review'/'blocked', next_step=...). "
                    "Use actual stdout/stderr; exit_code=0 alone does not prove the hypothesis. "
                    "Keep this assessment brief. Do no new topology derivation before recording it.", exits)
        if observation and observation["outcome"] == "needs_plan_review" and observation["plan_revision"] == workflow.plan_revision:
            return ("Return this measured contradiction via finish_task with status=needs_plan_review, reason, "
                    "sandbox and script_path; the coordinator must obtain Planner review before new construction.", {"finish_task"})
        if observation and observation["outcome"] == "blocked":
            return "Report this measured blocker through finish_task status=blocked and limitations; preserve saved artifacts.", {"finish_task"}
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
                        "analysis step in saved Python, execute it, and inspect its result. If this measured subgoal is complete, "
                        "finish_task status=subtask_complete with one next_experiment for a fresh delegation; keep same-goal repairs here. Reuse saved arrays "
                        "and modules; do not restart the whole derivation. When all requested checks are met, "
                        "independently validate the saved structure before advancing build. A partial result "
                        "is useful evidence, not proof of a finished candidate.", None)
            if item.exit_code == 0:
                return ("Compare the actual probe output with its checks. If a check is unmet, patch only "
                        "the failing code using exact edits, then rerun; do not re-derive the "
                        "whole construction. Otherwise advance probe with that saved-script receipt.",
                        None)
            return "Inspect actual stderr, repair the saved probe or request plan review if the assumption is invalid.", None
        if not self.sandbox_states and "inspect_sandbox" in names:
            return "Inspect the authorized Sandbox state; do no construction reasoning before it is ready.", exits | {"inspect_sandbox", "inspect_atom_structure"}
        unavailable = [(box, state) for box, state in self.sandbox_states.items() if state not in {"ready", "running"}]
        if unavailable and not any(state in {"ready", "running"} for state in self.sandbox_states.values()):
            box, state = unavailable[0]
            if "start_sandbox" not in names:
                evidence.stop(f"Sandbox {box} is {state}; connect Execute + Start/Stop or start it from its card")
            return f"Start Sandbox {box} ({state}); obtain readiness before computing.", exits | {"start_sandbox"}
        if workflow.current == "build":
            action = workflow.plan[workflow.index]["experiment"]
            return (f"Implement the committed construction method in {action['artifact']}; generate known inputs "
                    "and measure derived values in that code. Produce the first candidate now when the construction "
                    "fits this finite action; otherwise save the necessary construction intermediate. "
                    f"Current question: {action['question']} Method: {action['method']} "
                    "Inspect actual candidate failures before choosing a repair. Keep final acceptance checks "
                    "unchanged; stop at the experiment's finite limit.", None)
        action = workflow.plan[0]["experiment"]
        save = ("Save the input-measurement script at " + action["artifact"] + "; answer: " + action["question"] +
                ". Scope=input_measurement: choose the smallest primitive fact first; do not design, "
                "fit or search for the final candidate. Candidate work belongs to build, after this checkpoint.")
        if self.inline_attempts < evidence.limits.inline_probe_limit and not self.successful_measurements:
            save = ("Compute ONE primitive input now using direct argv [python3, -c, short Python source] "
                    f"(at most {evidence.limits.inline_probe_chars} characters); print the result instead of "
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
            self.last_action += (" Use run_modelling_step for new scripts (source required in arguments), "
                "patch_modelling_step for repairs (exact edits required), rerun_modelling_step for saved inputs, "
                "or rewrite_modelling_step for an explicit replacement. Answer ONE current question with code, measured expected_output "
                "and a finite stop_condition. The host saves and executes it without another model turn in between. "
                "Check current_experiment.assessment_status: pending requires one brief assessment, either separately with observe "
                "or combined using previous_assessment. Recorded means OMIT previous_assessment and proceed using the immutable host record. "
                "Do not solve later questions before this result. Reuse saved artifacts; patch one measured failure at a time.")
        if (self.allowed is not None and evidence.workflow.current in {"probe", "build"}
                and evidence.artifact_handoffs and not evidence.workflow.host_signal
                and not evidence.workflow.review_request and not evidence.workflow.blocked and not evidence.closing_reason
                and evidence.time_budget()["phase"] != "closeout"
                and not (evidence.executions or evidence.pending or evidence.inline_pending or evidence.skill_operations)):
            self.allowed.add("validate_modelling_artifact")
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
            self.last_action += (" Output-limit recovery has been used: continue with a saved, executable "
                                 "step based on actual evidence; do not repeat the discarded derivation.")
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
                        for d in tool.function_declarations:
                            if d.name == "rewrite_modelling_step":
                                d.parameters_json_schema["required"].append("rewrite_reason")
                    if not evidence.scripts:
                        tool.function_declarations = [d for d in tool.function_declarations
                            if d.name not in STEP_TOOL_MODES or STEP_TOOL_MODES[d.name] == "new"]
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
                "action_recoveries": self.recoveries, "sandbox_states": self.sandbox_states,
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
        """Allow parallel independent reads; keep state-changing steps serial.

        Unknown operations are serialized too. Never partially dispatch a batch
        and pretend the withheld half has a successful function response.
        """
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        readonly = READ_DEPENDENCIES
        return len(calls) > 1 and not all(evidence.semantic_tool(c.name) in readonly for c in calls)

    def recovery_instruction(self):
        return "HOST EXECUTION ACTION RECOVERY: " + (self.last_action or "Take the next executable step.") + \
            " If observed execution contradicts a planning assumption, finish_task with JSON " \
            "status=needs_plan_review, reason, sandbox and script_path of that execution. " \
            "Otherwise use an authorized tool or report blocked. " \
            "Return one dependent action per response; independent reads may be parallel. " \
            "Do not repeat derivations. No tools from the discarded response ran. Existing receipts remain authoritative."


async def collect_script_operation(evidence, definitions, invoke, semantic_name, arguments, result, stopped):
    """Await existing OAW receipts inside one tool action, never rerun code.

    Only a tracked custom script's command is collected. Each observation uses
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
    pending = semantic_name == "wait_sandbox_operation" and operation in evidence.pending
    if not (saved or pending):
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
        evidence.notice("structure_builder", "Waiting for saved script operation; no additional model request",
            workflow_event="modelling_command_wait", operation_id=operation, sandbox=sandbox)
        return 30 if remaining is None else min(30, remaining)
    try:
        return await collect_operation_result(result, lambda op, seconds: invoke(waiter.capability_id,
            {"sandbox": sandbox, "operation_id": op, "wait_seconds": seconds}), stopped, before_wait=before_wait)
    except OperationReceiptMismatch as exc:
        return exc.pending
