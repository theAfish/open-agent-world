"""Durable work-source notifications, dispatched through the existing Run host.

The scoped execution ledger is the queue. Events only wake its reader; neither
UI polling nor a process-local callback owns delivery or research acceptance.
"""
from __future__ import annotations

import asyncio
import contextvars
import copy
from datetime import UTC, datetime
import json
import logging
import time
from uuid import uuid4

from backend.card_state import state_session
from backend.errors import DomainError, NotFoundError, PermissionDeniedError, RuntimeUnavailableError
from backend.runs.models import RunStatus, TERMINAL_RUN_STATUSES

logger = logging.getLogger(__name__)
MAX_CONTINUATION_TURNS = 64


class NodeContinuationMixin:
    def watch_continuations(self, node_id):
        kind, namespace = self.services.card_state.identity(node_id)
        key = (node_id, namespace if kind == "session" else None)
        self.continuation_keys.add(key)
        self.continuation_versions[key] = self.continuation_versions.get(key, 0) + 1
        self.signal_continuations()

    def signal_continuations(self):
        if self.continuation_closed or not self.continuation_keys:
            return
        self.continuation_event.set()
        if self.continuation_task is None or self.continuation_task.done():
            # Never inherit an Agent invocation or an in-progress graph transaction.
            self.continuation_task = asyncio.create_task(
                self._continue_work(), context=contextvars.Context(), name="work-continuations")

    async def _continue_work(self):
        while self.continuation_keys and not self.continuation_closed:
            self.continuation_event.clear()
            self.continuation_delay = 30
            for node_id, namespace in tuple(self.continuation_keys):
                version = self.continuation_versions[(node_id, namespace)]
                try:
                    with state_session(namespace):
                        async with self.services._node_mutation():
                            keep = await self._continue_board(node_id)
                    if not keep and self.continuation_versions[(node_id, namespace)] == version:
                        self.continuation_keys.discard((node_id, namespace))
                except (NotFoundError, PermissionDeniedError):
                    self.continuation_keys.discard((node_id, namespace))
                except Exception:
                    # Retain the durable intent on transient failure. No re-dispatch
                    # occurs if its reserved Run already exists.
                    logger.exception("Could not deliver work notification for %s", node_id)
            if self.continuation_keys:
                try:
                    await asyncio.wait_for(self.continuation_event.wait(), timeout=self.continuation_delay)
                except TimeoutError:
                    pass

    def _continuation_parent(self, run_id):
        manager = self.services.run_manager
        parent = manager.get_run(run_id)
        current = parent
        while current:
            if current.lifecycle.get("cancellation_requested") or current.status in {
                RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.INTERRUPTED,
            }:
                return None
            current = manager.get_run(current.parent_run_id) if current.parent_run_id else None
        return parent

    async def _continue_board(self, node_id):
        self.collect_delegations(node_id)
        state = self.state(node_id)
        before = copy.deepcopy(state)
        manager = self.services.run_manager
        groups = {}
        for entry in [*state["attempts"], *state.get("wakeups", [])]:
            # One durable owner is chosen at dispatch. A state-machine action
            # must never also produce a legacy notification Run.
            if entry.get("continuation_owner") == "state_machine":
                continue
            if not entry.get("auto_continue") or entry.get("notification_suppressed") or entry.get("observed_by_run"):
                continue
            if entry.get("notification_run_id"):
                try:
                    manager.get_run(entry["notification_run_id"])
                    continue  # Admission is the durable deduplication boundary.
                except NotFoundError:
                    entry.pop("notification_run_id")  # Crash between claim and admission.
            parent = self._continuation_parent(entry["coordinator_run_id"])
            if parent is None:
                entry["notification_suppressed"] = True
                continue
            if entry.get("delegated") and (not entry.get("applied") or entry.get("status") not in TERMINAL_RUN_STATUSES):
                continue
            if entry.get("item_id") and entry.get("due_at"):
                item = next((i for i in self.items(node_id, self._document_value(node_id)) if i.id == entry["item_id"]), None)
                newer = any(a.get("item_id") == entry["item_id"] and a.get("run_id") != entry.get("source_run_id")
                            and a.get("created_at", 0) > entry.get("created_at", 0) for a in state["attempts"])
                if item is None or item.completed or newer:
                    entry["notification_suppressed"] = True
                    continue
            if parent.status != RunStatus.SUCCEEDED or parent.lifecycle.get("execution") == "running":
                continue
            if entry.get("due_at", 0) > time.time():
                self.continuation_delay = min(self.continuation_delay, max(.01, entry["due_at"] - time.time()))
                continue
            groups.setdefault(parent.run_id, (parent, []))[1].append(entry)
        if state != before:
            self.save(node_id, state)
        for parent, entries in groups.values():
            entries = entries[:16]
            try:
                capabilities = self.services.capabilities.derive(parent.agent_id).capabilities
                grant = next((c for c in capabilities if c.target_id == node_id
                              and c.kind == self.spec(node_id).control_capability_kind), None)
                if grant is None:
                    raise PermissionDeniedError("Coordinator no longer has work-source control")
                self.delegation_authorize(node_id, grant)
                for entry in entries:
                    if entry.get("library_id"):
                        self.summoning_capability(parent.agent_id, entry["library_id"])
                conversation, session_id = manager._conversation_scope(parent)
                session = None
                if conversation:
                    session = self.services.conversations.get_session(conversation, session_id)
                    self.services._require_session_participant(session, parent.agent_id)
                    self.services._require_conversation_connection(parent.agent_id, conversation)
                runs = manager.list_runs(agent_id=parent.agent_id)
                # Yield to user messages and existing provider tails, even if the
                # Agent happens to allow multiple concurrent Runs.
                if any(r.status not in TERMINAL_RUN_STATUSES or r.lifecycle.get("execution") == "running" for r in runs):
                    continue
                if conversation and self.services.deliveries.next_batch(parent.agent_id) is not None:
                    continue
                if sum(r.root_run_id == parent.root_run_id and bool(r.lifecycle.get("work_continuation")) for r in runs) >= MAX_CONTINUATION_TURNS:
                    raise PermissionDeniedError("Automatic continuation limit reached; collect results and continue explicitly")
                manager.assert_can_start(parent.agent_id)
                notices = [self._work_notice(e) for e in entries]
                prompt = ("OAW work notification for board " + node_id + ".\n"
                    "Continue the existing task in this session. Collect current state; inspect reports and actual outputs, "
                    "verify acceptance, then dispatch ready downstream work. Run success is not task acceptance. "
                    "For a scheduled check, inspect the real external job; elapsed time never proves completion. "
                    "Use defer again only while the external job is still pending. Do not resubmit existing jobs.\n"
                    + json.dumps(notices, ensure_ascii=False))
                if session:
                    prompt = self.services._conversation_prompt(conversation, session, parent.agent_id, prompt)
                run_id = str(uuid4())
                for entry in entries:
                    entry["notification_run_id"] = run_id
                    entry.pop("notification_error", None)
                self.save(node_id, state)
                try:
                    run = await manager.start_run(parent.agent_id, prompt, caller_kind="delegation", caller_id=node_id,
                        parent_run_id=parent.run_id, context_id=parent.context_id, task_id=parent.task_id, run_id=run_id,
                        initial_lifecycle={"work_continuation": True})
                except Exception:
                    # Do not replay an admitted Run, including an interrupted one.
                    try:
                        manager.get_run(run_id)
                    except NotFoundError:
                        for entry in entries:
                            entry.pop("notification_run_id", None)
                        self.save(node_id, state)
                    raise
                if session:
                    task = asyncio.create_task(self.services._persist_conversation_run(
                        parent.agent_id, run.run_id, conversation, session_id))
                    self.continuation_outputs.add(task)
                    task.add_done_callback(self.continuation_outputs.discard)
                    task.add_done_callback(self.services._consume_background_task)
            except RuntimeUnavailableError:
                continue  # Capacity, cleanup or paused Legion; preserve pending work.
            except DomainError as error:
                for entry in entries:
                    entry.update(notification_suppressed=True, notification_error=str(error))
                self.save(node_id, state)
        return any(e.get("auto_continue") and not e.get("notification_suppressed")
                   and e.get("continuation_owner") != "state_machine"
                   and not e.get("observed_by_run") and not e.get("notification_run_id")
                   for e in [*state["attempts"], *state.get("wakeups", [])])

    def _document_value(self, node_id):
        from backend.node_documents import read_document
        return read_document(self.services, node_id)["value"]

    @staticmethod
    def _work_notice(entry):
        fields = ("item_id", "instance_id", "run_id", "status", "output_directory", "request_id", "reason", "external_jobs")
        notice = {k: entry[k] for k in fields if k in entry}
        if entry.get("report"):
            notice["report"] = {k: entry["report"][k][:600] for k in ("outcome", "summary", "next_step")}
        return notice

    def record_report_check(self, state, entry, record):
        report = entry.get("report") or {}
        if (report.get("outcome") != "waiting" or record.status != RunStatus.SUCCEEDED
                or entry.get("notification_suppressed") or record.lifecycle.get("cancellation_requested")):
            return
        key = "report:" + record.run_id
        wakeups = state.setdefault("wakeups", [])
        if any(w["request_id"] == key for w in wakeups):
            return
        created = (record.finished_at or datetime.now(UTC)).timestamp()
        wakeups.append(dict(request_id=key, source_run_id=record.run_id, item_id=entry["item_id"],
            coordinator_run_id=entry["coordinator_run_id"], auto_continue=entry.get("auto_continue", False),
            continuation_owner=entry.get("continuation_owner", "legacy"),
            created_at=created, due_at=created + report["check_after_seconds"],
            reason=report["next_step"] or report["summary"], external_jobs=report["external_jobs"]))
