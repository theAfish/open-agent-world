"""Read-only reconciliation of durable tool receipts against live resources."""

from __future__ import annotations

import asyncio
from typing import Any

from .tool_receipts import RunToolReceipts


async def assess_run(services: Any, agent_id: str, run_id: str) -> list[dict[str, Any]]:
    """Return evidence for each effect; never infer success from a tool start.

    The result is intentionally not an authorization to replay a command. A
    confirmed prior effect may still need a fresh model decision about what to
    do next, and an unverified effect must pause automatic continuation.
    """
    assessments: list[dict[str, Any]] = []
    for receipt in RunToolReceipts(services.database).list_run(run_id):
        evidence = "read_only" if receipt["read_only"] else "unknown"
        hints = receipt["result_hints"]
        kind, target_id = receipt["capability_kind"], receipt["target_id"]
        operation_facts = None
        if not receipt["read_only"]:
            try:
                # An earlier capability must still be authorized before this
                # Run's recovery logic reads the associated resource.
                services.capabilities.capability_for_id(agent_id, f"{kind}:{target_id}")
                operation_id = hints.get("operation_id") or hints.get("command_id")
                if (isinstance(operation_id, str) and operation_id) or kind in {
                    "sandbox.execute", "sandbox.run_skill_script", "sandbox.install_python_packages",
                }:
                    from backend.sandbox import history
                    sandbox_id = hints.get("sandbox_id") or target_id
                    services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
                    operation = next((item for item in history.read(services, sandbox_id)
                                      if item.get("run_id") == run_id
                                      and ((operation_id is not None and item.get("id") == operation_id)
                                           or item.get("tool_receipt_id") == receipt["receipt_id"])), None)
                    if operation is not None:
                        argv = operation.get("argv", [])
                        argv = argv if isinstance(argv, list) else []
                        result = operation.get("tool_result")
                        result = result if isinstance(result, dict) else {}
                        output = {name: operation.get(name, result.get(name, "")) for name in ("stdout", "stderr")}
                        operation_facts = {"command_id": operation.get("id"),
                            "state": operation.get("state"), "exit_code": result.get("exit_code", operation.get("exit_code")),
                            "argv": [str(arg)[:512] for arg in argv[:8]],
                            "argv_truncated": len(argv) > 8 or any(len(str(arg)) > 512 for arg in argv[:8]),
                            "output_reference": {"sandbox_id": sandbox_id, "command_id": operation.get("id"),
                                "run_id": run_id, "reader": "wait_sandbox_operation",
                                "arguments": {"sandbox": sandbox_id, "operation_id": operation.get("id"),
                                              "wait_seconds": 1}},
                            "output_preview": {name: str(output[name])[:1000]
                                               for name in ("stdout", "stderr")},
                            "output_truncated": any(len(str(output[name])) > 1000
                                                    for name in ("stdout", "stderr"))}
                        state = operation.get("state")
                        if state == "running":
                            evidence = "pending"
                        elif state == "finished":
                            exit_code = result.get("exit_code", operation.get("exit_code"))
                            if result.get("ok") is not False and exit_code in (None, 0):
                                evidence = "confirmed"
                elif kind in {"sandbox.start", "sandbox.stop"}:
                    info = await services.get_sandbox(target_id)
                    expected = {"ready", "running"} if kind == "sandbox.start" else {"stopped"}
                    if info.state.value in expected:
                        evidence = "confirmed"
                elif receipt["state"] == "finished" and isinstance(hints.get("revision"), int):
                    from backend.node_documents import read_document
                    current = read_document(services, target_id)
                    evidence = ("confirmed" if current["revision"] == hints["revision"]
                                else "changed" if current["revision"] > hints["revision"]
                                else "unknown")
            except (KeyError, TypeError, ValueError, RuntimeError, OSError):
                evidence = "unknown"
            except Exception:
                # Revoked access, deleted resources and backend-specific
                # validation errors all require a human-visible inspection.
                evidence = "unknown"
        assessments.append({
            "receipt_id": receipt["receipt_id"], "capability_kind": kind,
            "target_id": target_id, "state": receipt["state"],
            "read_only": receipt["read_only"], "evidence": evidence,
            "result_hints": hints,
            **({"operation": operation_facts} if operation_facts is not None else {}),
        })
    return assessments


async def recovery_state(database: Any, services: Any, agent_id: str, run_id: str,
                         *, receipt_baseline: int, capability_invocations: int
                         ) -> tuple[str, list[dict[str, Any]]]:
    """Classify a broken model stream without treating tool replies as proof.

    Every invocation must have crossed the durable OAW receipt boundary before
    we can call an attempt read-only. Resource checks are read-only and bounded;
    a missing or inaccessible resource remains unknown.
    """
    if database is None:
        return "unknown", []
    journal = RunToolReceipts(database)
    receipts = journal.list_run(run_id)
    visible = [{key: item[key] for key in (
        "capability_kind", "target_id", "state", "read_only", "result_hints",
    )} for item in receipts[-20:]]
    classification = journal.recovery_classification(run_id)
    if services is not None and classification != "read_only":
        try:
            assessed = await asyncio.wait_for(assess_run(services, agent_id, run_id), timeout=30)
        except Exception:
            classification = "unknown"
        else:
            visible = [{key: item[key] for key in (
                "capability_kind", "target_id", "state", "read_only",
                "evidence", "result_hints", "operation",
            ) if key in item} for item in assessed[-20:]]
            classification = ("completed_effects" if assessed and all(
                item["read_only"] or item["evidence"] == "confirmed" for item in assessed
            ) else "reconcile_required")
    if len(receipts) - receipt_baseline < capability_invocations:
        classification = "unknown"
    return classification, visible
