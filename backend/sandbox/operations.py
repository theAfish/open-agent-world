"""Agent observation of host-owned Sandbox work, using the command journal.

Tool wait deadlines never cancel execution. The existing command cancellation
and Run cleanup paths retain ownership of the actual worker.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
import math
import json
from uuid import uuid4

from backend.errors import DomainError, ResourceValidationError
from . import history
from .models import (SandboxOperationError, SandboxSecurityError,
                     SandboxStateError, SandboxValidationError)


def wait_budget(value, maximum=60):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ResourceValidationError(f"wait_seconds must be finite and between 0 and {maximum}")
    return float(value)


def failure_result(error):
    if isinstance(error, SandboxOperationError):
        return error.feedback()
    if isinstance(error, DomainError):
        return {"ok": False, "error": {"code": error.code, "type": type(error).__name__, "message": error.message}}
    if isinstance(error, (SandboxStateError, SandboxValidationError)):
        return {"ok": False, "error": {
            "code": "conflict" if isinstance(error, SandboxStateError) else "invalid_resource",
            "type": type(error).__name__, "message": str(error),
            "retryable": isinstance(error, SandboxStateError)}}
    return None


@dataclass
class SandboxOperations:
    services: object
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    def authorize(self, agent_id, sandbox_id):
        self.services._require_card_type(sandbox_id, "sandbox")
        if agent_id is not None:
            self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)

    async def submit(self, agent_id, sandbox_id, kind, invoke, *, wait_seconds=1):
        budget = wait_budget(wait_seconds) if wait_seconds is not None else None
        async with self.services._node_mutation():
            self.authorize(agent_id, sandbox_id)
            self.services.summoning.assert_admission(sandbox_id)
            if sandbox_id in self.services._sandbox_stopping:
                raise SandboxStateError("Sandbox cleanup is pending")
            operation_id = uuid4().hex
            context = self.services.run_manager.current_context
            receipt = {"id": operation_id, "sandbox_id": sandbox_id,
                "history_key": history.key(self.services, sandbox_id),
                "caller": agent_id or "user", "run_id": context.run_id if context else None,
                "operation_kind": kind, "state": "running", "argv": [],
                "started_at": datetime.now(UTC).isoformat()}
            self.services._sandbox_commands[operation_id] = receipt
            try:
                history.save(self.services, sandbox_id, receipt)
            except BaseException:
                self.services._sandbox_commands.pop(operation_id, None)
                raise
            task = asyncio.create_task(self._run(receipt, invoke), name=f"sandbox-operation:{operation_id}")
            self.tasks[operation_id] = task
            self.services._sandbox_tasks[operation_id] = task
            # Retrieve failures even if the submitting tool has already yielded.
            def settled(done):
                if not done.cancelled():
                    done.exception()
                # Cancellation before the coroutine's first instruction has
                # no finally block. Close its reserved journal entry here.
                if operation_id in self.tasks and done.cancelled():
                    receipt.update(state="cancelled", cancelled=True,
                        error="Operation cancelled before admission")
                    self._finish(receipt)
            task.add_done_callback(settled)
        try:
            if budget is None:
                try:
                    result = await asyncio.shield(task)
                    self.authorize(agent_id, sandbox_id)
                    return result
                finally:
                    if task.done() and not task.cancelled():
                        self._observe(sandbox_id, operation_id)
            return await self.wait(agent_id, sandbox_id, operation_id, budget)
        except asyncio.CancelledError:
            # Cancelling initial submission retains existing Stop semantics.
            # Cancelling an independent wait below never cancels the operation.
            if not task.done() and not task.cancelling():
                task.cancel()
            # The worker owns native termination, ACL revocation and journal
            # cleanup. Do not report cancellation until that cleanup completes,
            # even if the caller cancels again while it is being drained.
            async def finish():
                await asyncio.gather(task, return_exceptions=True)
            await self.services._complete_committed(finish())
            raise

    async def _run(self, receipt, invoke):
        operation_id, sandbox_id = receipt["id"], receipt["sandbox_id"]
        try:
            value = await invoke(operation_id)
            value = json.loads(json.dumps(asdict(value) if is_dataclass(value) else value))
            # Keep command output bounded in durable receipts as in history.py.
            stored = dict(value) if isinstance(value, dict) else value
            if isinstance(stored, dict):
                for key in ("stdout", "stderr"):
                    if isinstance(stored.get(key), str):
                        stored[key] = stored[key][-65536:]
            receipt["tool_result"] = stored
            if receipt["state"] == "running":
                receipt["state"] = "finished"
            return value
        except asyncio.CancelledError:
            receipt.update(state="cancelled", cancelled=True,
                error="Operation cancelled; inspect its effects before submitting another operation.")
            raise
        except Exception as error:
            result = failure_result(error)
            receipt.update(state="error", error=str(error)[:4096],
                failure_type="security" if isinstance(error, SandboxSecurityError) else "internal")
            if result is not None:
                receipt["tool_result"] = result
                receipt.pop("failure_type", None)
                if isinstance(error, SandboxOperationError):
                    return result
            raise
        finally:
            self._finish(receipt)

    def _finish(self, receipt):
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        try:
            history.save(self.services, receipt["sandbox_id"], receipt)
        finally:
            self.tasks.pop(receipt["id"], None)
            self.services._sandbox_commands.pop(receipt["id"], None)
            self.services._sandbox_tasks.pop(receipt["id"], None)

    def _observe(self, sandbox_id, operation_id):
        receipt = next((item for item in history.read(self.services, sandbox_id) if item["id"] == operation_id), None)
        if receipt is not None and receipt["state"] != "running":
            receipt["result_observed"] = True
            history.save(self.services, sandbox_id, receipt)

    async def wait(self, agent_id, sandbox_id, operation_id=None, wait_seconds=30):
        budget = wait_budget(wait_seconds)
        self.authorize(agent_id, sandbox_id)
        if operation_id is None:
            await asyncio.sleep(budget)
            self.authorize(agent_id, sandbox_id)
            return {"ok": True, "status": "waited", "wait_seconds": budget,
                "next_step": "Inspect the resource or retry the previously rejected operation. Elapsed time does not prove resource readiness."}
        receipt = next((item for item in history.read(self.services, sandbox_id) if item["id"] == operation_id), None)
        if receipt is None:
            raise ResourceValidationError("Unknown operation_id for this Sandbox; inspect its activity")
        task = self.tasks.get(operation_id) or self.services._sandbox_tasks.get(operation_id)
        if task is not None and not task.done() and budget:
            # asyncio.wait leaves the worker alive on timeout or waiter cancellation.
            await asyncio.wait({task}, timeout=budget)
        self.authorize(agent_id, sandbox_id)
        if task is not None and task.done() and not task.cancelled():
            try:
                value = task.result()
            finally:
                self._observe(sandbox_id, operation_id)
            if value is not None:
                return asdict(value) if is_dataclass(value) else value
        receipt = next((item for item in history.read(self.services, sandbox_id) if item["id"] == operation_id), receipt)
        if receipt["state"] != "running":
            self._observe(sandbox_id, operation_id)
        if receipt.get("failure_type") == "security":
            raise SandboxSecurityError(receipt["error"])
        if receipt.get("failure_type") == "internal":
            raise RuntimeError(receipt["error"])
        if "tool_result" in receipt:
            return receipt["tool_result"]
        if receipt.get("exit_code") is not None:
            return {key: receipt.get(key) for key in ("sandbox_id", "argv", "exit_code", "stdout", "stderr",
                "duration_seconds", "timed_out", "cancelled")} | {"command_id": operation_id}
        if receipt["state"] == "running":
            progress = {}
            if receipt.get("operation_kind") == "python_install":
                from .manager import SandboxManager
                backend = self.services.sandbox_backend
                if isinstance(backend, SandboxManager):
                    progress["shared_python"] = await backend.python_status(sandbox_id)
                    self.authorize(agent_id, sandbox_id)
            return {"ok": True, "status": "running", "operation_id": operation_id,
                "command_id": operation_id, "operation_kind": receipt.get("operation_kind", "command"),
                "started_at": receipt["started_at"],
                "stdout": receipt.get("stdout", "")[-8192:], "stderr": receipt.get("stderr", "")[-8192:],
                "next_step": "Use wait_sandbox_operation with this operation_id, or do independent work. Do not resubmit this operation.", **progress}
        return {"ok": False, "status": receipt["state"], "operation_id": operation_id,
            "error": {"code": f"operation_{receipt['state']}", "message": receipt.get("error", "Inspect the command receipt before retrying"), "retryable": False}}

    async def cancel_run(self, run_id):
        from .history import stop
        targets = [(item["sandbox_id"], item["id"]) for item in self.services._sandbox_commands.values()
                   if item.get("run_id") == run_id and item["id"] in self.tasks]
        async def cancel(sandbox_id, operation_id):
            if operation_id in self.services._sandbox_commands:
                await stop(self.services, sandbox_id, command_id=operation_id)
        outcomes = await asyncio.gather(*(cancel(*target) for target in targets), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
