"""Durable external work, driven by the host's existing continuation loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from pydantic import ValidationError

from backend.agents import ScopedToolDefinition
from backend.errors import ConflictError, NotFoundError, PermissionDeniedError, ResourceValidationError, RuntimeUnavailableError, PluginUnavailableError
from backend.plugins.background_jobs import JobAttach, JobControl, JobInspect, JobObservation, JobSubmit, JobWait
from backend.plugins.failures import raise_if_unsafe
from backend.runs.models import RunStatus, TERMINAL_RUN_STATUSES
from backend.sandbox.models import SandboxStateError

ACTIVE = {"submitting", "queued", "running"}
TOOLS = {
    "submit_background_job": (JobSubmit, "Submit through a durable provider exactly once per request_key. Discover provider schemas with inspect_background_jobs. Reuse the same key after uncertainty; never blindly resubmit. Credentials belong to the connected Sandbox."),
    "attach_background_job": (JobAttach, "Track existing work by external_id without submitting it. Supply job_id to reconcile an uncertain submission. Only attach work belonging to this task."),
    "inspect_background_jobs": (JobInspect, "Read this Agent/session's durable job records and provider schemas. No model polling is needed while jobs run. Unknown status never proves completion or absence."),
    "wait_background_jobs": (JobWait, "Persist automatic continuation for existing jobs, then end this Agent turn. The host monitors without model calls and wakes this session when results need inspection. Waiting is not failure or scientific acceptance."),
    "control_background_job": (JobControl, "Pause/resume monitoring, acknowledge a processed outcome, or explicitly cancel external work. Stop/pause does not cancel remote compute. Detach explicitly relinquishes monitoring and permits resource deletion; the external job may still run and incur charges. Cancellation requires provider confirmation."),
}


@dataclass
class JobContext:
    service: object
    job: dict

    @property
    def agent_id(self):
        return self.job["agent_id"]

    @property
    def sandbox_id(self):
        return self.job["sandbox_id"]

    async def execute(self, argv, *, timeout_seconds=60):
        self.service.authorize(self.job)
        # Monitoring outlives its submitting Run and local runtime. Restore
        # prerequisites through the owning Agent's authority, never as user.
        try:
            await self.service.services.ensure_sandbox_running(self.sandbox_id, agent_id=self.agent_id)
        except PermissionDeniedError as error:
            raise SandboxStateError("Background monitoring requires a running Sandbox or Start permission") from error
        self.service.authorize(self.job)
        result = await self.service.services.execute_sandbox(self.sandbox_id, argv,
            agent_id=self.agent_id, timeout_seconds=timeout_seconds, _background_job_id=self.job['id'])
        self.service.authorize(self.job)
        return asdict(result) if hasattr(result, "__dataclass_fields__") else result

    async def inspect_operation(self, operation_id):
        from backend.sandbox import history
        self.service.authorize(self.job)
        entry = next((x for x in history.read(self.service.services, self.sandbox_id) if x["id"] == operation_id), None)
        if entry is None or entry.get("caller") != self.agent_id:
            raise PermissionDeniedError("Operation is not owned by this Agent")
        run = self.service.services.run_manager.get_run(entry["run_id"])
        if (run.context_id or "") != self.job["context_id"]:
            raise PermissionDeniedError("Operation belongs to another session")
        return entry


class BackgroundJobs:
    def __init__(self, services):
        self.services = services
        self.probes = {}
        self.submissions = set()

    def records(self):
        with self.services.database.locked() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record_json FROM background_jobs")]

    def get(self, job_id):
        with self.services.database.locked() as db:
            row = db.execute("SELECT record_json FROM background_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError("Background job not found")
        return json.loads(row[0])

    def update(self, job_id, *, expected_revision=None, **changes):
        with self.services.database.transaction(immediate=True) as db:
            row = db.execute("SELECT record_json FROM background_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError("Background job not found")
            job = json.loads(row[0])
            if expected_revision is not None and job["revision"] != expected_revision:
                return job
            job.update(changes, revision=job["revision"] + 1, updated_at=time.time())
            db.execute("UPDATE background_jobs SET record_json=? WHERE id=?", (json.dumps(job), job_id))
        return job

    def current(self, agent_id):
        ctx = self.services.run_manager.current_context
        if ctx is None or ctx.agent_id != agent_id or self.services.run_manager.get_run(ctx.run_id).status in TERMINAL_RUN_STATUSES:
            raise PermissionDeniedError("Background job tools require this Agent's active Run")
        return ctx

    def authorize(self, job):
        self.services.world.require_available_card(self.services.world.get_card(job["agent_id"]))
        self.services.capabilities.require_sandbox_execute(job["agent_id"], job["sandbox_id"])
        self.services.plugins.background_job_provider(job["provider"])
        source = self.services.run_manager.get_run(job["source_run_id"])
        conversation, session_id = self.services.run_manager._conversation_scope(source)
        if conversation:
            session = self.services.conversations.get_session(conversation, session_id)
            self.services._require_session_participant(session, job["agent_id"])
            self.services._require_conversation_connection(job["agent_id"], conversation)

    def owned(self, agent_id, job_id):
        ctx = self.current(agent_id)
        job = self.get(job_id)
        if job["agent_id"] != agent_id or job["context_id"] != (ctx.context_id or ""):
            raise PermissionDeniedError("Background job belongs to another Agent or session")
        self.authorize(job)
        return job

    def definitions(self, agent_id):
        if not any(c.kind == "sandbox.execute" for c in self.services.capabilities.derive(agent_id).capabilities):
            return []
        return [ScopedToolDefinition(capability_id="host:" + name, name=name,
                    description=description, input_schema=model.model_json_schema())
                for name, (model, description) in TOOLS.items()]

    async def invoke(self, agent_id, name, arguments):
        self.current(agent_id)
        try:
            request = TOOLS[name][0].model_validate(arguments)
        except ValidationError as exc:
            raise ResourceValidationError(str(exc.errors(include_input=False, include_url=False))) from exc
        return await getattr(self, name.removesuffix("_background_job").removesuffix("_background_jobs"))(agent_id, request)

    def public(self, job):
        return {k: job.get(k) for k in ("id", "provider", "sandbox_id", "request_key", "external_id", "status", "detail",
            "paused", "wake_requested", "notification_run_id", "last_observed_at", "next_check_at", "observation_error")}

    def reserve(self, agent_id, request, fingerprint):
        ctx = self.current(agent_id)
        job = dict(id=str(uuid4()), agent_id=agent_id, context_id=ctx.context_id or "", source_run_id=ctx.run_id,
            wake_run_id=ctx.run_id, provider=request.provider, sandbox_id=request.sandbox_id, request_key=request.request_key,
            fingerprint=fingerprint, external_id=None, status="submitting", detail="Submission reserved; never replay automatically.",
            paused=False, wake_requested=True, notification_run_id=None, next_check_at=0, failures=0,
            revision=1, created_at=time.time(), updated_at=time.time())
        self.authorize(job)
        with self.services.database.transaction(immediate=True) as db:
            row = db.execute("SELECT record_json FROM background_jobs WHERE agent_id=? AND context_id=? AND request_key=?",
                (agent_id, ctx.context_id or "", request.request_key)).fetchone()
            if row:
                old = json.loads(row[0])
                if old["fingerprint"] != fingerprint:
                    raise ConflictError("request_key already identifies different work")
                self.authorize(old)
                return old, False
            db.execute("INSERT INTO background_jobs VALUES (?,?,?,?,?)",
                (job["id"], agent_id, job["context_id"], request.request_key, json.dumps(job)))
        return job, True

    async def submit(self, agent_id, request):
        provider = self.services.plugins.background_job_provider(request.provider)
        if provider.submit is None:
            raise ResourceValidationError("This provider only supports attaching existing work")
        try:
            spec = provider.specification.model_validate(request.specification)
        except ValidationError as exc:
            raise ResourceValidationError(str(exc.errors(include_input=False, include_url=False))) from exc
        canonical = {**request.model_dump(), "specification": spec.model_dump(mode="json")}
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        job, created = self.reserve(agent_id, request, fingerprint)
        if not created:
            return self.public(job)
        context = JobContext(self, job)
        self.submissions.add(job["id"])
        try:
            if provider.prepare:
                try:
                    async with asyncio.timeout(90):
                        await provider.prepare(context, spec)
                except asyncio.CancelledError:
                    self.update(job["id"], status="interrupted", detail="Preflight interrupted before submission.")
                    raise
                except Exception as error:
                    raise_if_unsafe(error, allow_revocation=True)
                    job = self.update(job["id"], status="failed", detail="Preflight failed before submission; inspect provider requirements.")
                    return self.public(job)
            self.authorize(job)
            try:
                async with asyncio.timeout(180):
                    external_id = await provider.submit(context, spec, job["id"])
                if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 512:
                    raise ValueError("Provider returned an invalid external ID")
                # Persist the ID before probing, notifying, or returning to a model.
                job = self.update(job["id"], external_id=external_id, status="queued", detail="Submitted; awaiting provider observation.")
            except asyncio.CancelledError:
                self.update(job["id"], status="unknown", detail="Submission outcome is uncertain. Reconcile existing work; do not resubmit.")
                raise
            except Exception as error:
                raise_if_unsafe(error, allow_revocation=True)
                job = self.update(job["id"], status="unknown", detail="Submission outcome is uncertain. Reconcile existing work; do not resubmit.")
            self.authorize(job)
            return self.public(job)
        finally:
            self.submissions.discard(job["id"])
            self.services.node_execution.signal_continuations()

    async def attach(self, agent_id, request):
        ctx = self.current(agent_id)
        for other in self.records():
            if (other["provider"], other["sandbox_id"], other["external_id"]) == (request.provider, request.sandbox_id, request.external_id):
                if (other["agent_id"], other["context_id"], other["request_key"]) != (agent_id, ctx.context_id or "", request.request_key):
                    raise ConflictError("External work is already tracked; use its existing job record")
        if request.job_id:
            job = self.owned(agent_id, request.job_id)
            if (job["provider"], job["sandbox_id"], job["request_key"]) != (request.provider, request.sandbox_id, request.request_key):
                raise ConflictError("Attachment must match the reserved job")
            if job["external_id"] and job["external_id"] != request.external_id:
                raise ConflictError("An existing external identity cannot be replaced")
            if job["id"] in self.submissions:
                raise ConflictError("Submission is still in progress")
        else:
            fingerprint = hashlib.sha256(json.dumps(request.model_dump(exclude={"job_id"}), sort_keys=True).encode()).hexdigest()
            job, _ = self.reserve(agent_id, request, fingerprint)
        for other in self.records():
            if other["id"] != job["id"] and (other["provider"], other["sandbox_id"], other["external_id"]) == (request.provider, request.sandbox_id, request.external_id):
                raise ConflictError("External work is already tracked; use its existing job record")
        provider = self.services.plugins.background_job_provider(job["provider"])
        try:
            async with asyncio.timeout(90):
                observation = JobObservation.model_validate(await provider.inspect(JobContext(self, job), request.external_id))
            self.authorize(job)
            fields = observation.model_dump()
            if observation.status == "unknown":
                fields["status"] = "queued"  # Keep observing an existing, unrecognized provider state.
            job = self.update(job["id"], external_id=request.external_id, **fields, last_observed_at=time.time())
        except BaseException:
            if not job["external_id"]:
                self.update(job["id"], status="unknown", detail="Attachment was not verified. Reconcile this record; do not submit new work.")
            raise
        finally:
            self.services.node_execution.signal_continuations()
        return self.public(job)

    async def inspect(self, agent_id, request):
        ctx = self.current(agent_id)
        if request.job_ids:
            records = [self.owned(agent_id, ident) for ident in dict.fromkeys(request.job_ids)]
        else:
            records = []
            for job in self.records():
                if job["agent_id"] == agent_id and job["context_id"] == (ctx.context_id or ""):
                    try:
                        self.authorize(job)
                    except (PermissionDeniedError, NotFoundError, RuntimeUnavailableError, PluginUnavailableError):
                        continue  # One revoked resource must not hide other authorized work.
                    records.append(job)
        return {"jobs": [self.public(job) for job in records], "providers": {
            name: {"specification": provider.specification.model_json_schema(), "can_submit": provider.submit is not None,
                   "can_cancel": provider.cancel is not None}
            for name, provider in self.services.plugins.background_job_providers().items()}}

    async def wait(self, agent_id, request):
        ctx = self.current(agent_id)
        jobs = [self.owned(agent_id, ident) for ident in dict.fromkeys(request.job_ids)]
        if any(job["paused"] for job in jobs):
            raise ConflictError("Automatic monitoring is paused; resume explicitly before waiting")
        for job in jobs:
            # A repeated wait from the same turn cannot erase a delivery claim.
            if job["wake_run_id"] != ctx.run_id:
                self.update(job["id"], wake_run_id=ctx.run_id, wake_requested=True, notification_run_id=None)
            else:
                self.update(job["id"], wake_requested=True)
        self.services.run_manager.store.update_lifecycle(ctx.run_id, awaiting_jobs=[j["id"] for j in jobs])
        self.services.node_execution.signal_continuations()
        return {"status": "waiting", "job_ids": [j["id"] for j in jobs], "durable": True}

    async def control(self, agent_id, request):
        job = self.owned(agent_id, request.job_id)
        return await self.control_record(job, request,
            resume_run_id=self.current(agent_id).run_id,
            reauthorize=lambda: self.owned(agent_id, request.job_id))

    async def control_record(self, job, request, *, resume_run_id=None, reauthorize=None):
        """Shared controls; callers establish either Run or connected board authority."""
        self.authorize(job)
        if reauthorize:
            reauthorize()
        if request.action == "cancel":
            provider = self.services.plugins.background_job_provider(job["provider"])
            if provider.cancel is None or not job["external_id"]:
                raise ResourceValidationError("Provider cannot cancel this job; inspect its external identity")
            job = self.update(job["id"], paused=True, wake_requested=False, detail="External cancellation requested; termination unconfirmed.")
            async with asyncio.timeout(90):
                await provider.cancel(JobContext(self, job), job["external_id"])
                if reauthorize:
                    reauthorize()
                observation = JobObservation.model_validate(await provider.inspect(JobContext(self, job), job["external_id"]))
            self.authorize(job)
            if reauthorize:
                reauthorize()
            job = self.update(job["id"], expected_revision=job['revision'], **observation.model_dump(), last_observed_at=time.time())
        elif request.action == "acknowledge":
            job = self.update(job["id"], wake_requested=False)
        elif request.action == "detach":
            job = self.update(job["id"], paused=True, wake_requested=False, tracking_released=True,
                detail="Monitoring explicitly relinquished. External work may still run; termination is unconfirmed.")
        else:
            paused = request.action == "pause"
            changes = {"paused": paused}
            if not paused:
                changes.update(next_check_at=0, tracking_released=False)
                if resume_run_id:
                    changes.update(wake_run_id=resume_run_id, wake_requested=True, notification_run_id=None, monitor_after_stop=False)
                elif self.stopped(job):
                    # A user may monitor surviving compute without reviving a stopped Agent turn.
                    changes.update(monitor_after_stop=True, wake_requested=False)
            job = self.update(job["id"], **changes)
        self.services.node_execution.signal_continuations()
        return self.public(job)

    def has_pending(self):
        return any(not j["paused"] and (j["status"] in ACTIVE or j["wake_requested"] and not j["notification_run_id"]) for j in self.records())

    def assert_idle(self, *, card_id=None, session_id=None):
        for job in self.records():
            matches = card_id in {job["agent_id"], job["sandbox_id"]} if card_id else job["context_id"] == session_id
            if matches and not job.get("tracking_released") and (job["status"] in ACTIVE | {"unknown"} or job["wake_requested"]):
                raise ConflictError("Background work is still tracked. Collect/cancel it or explicitly detach monitoring before deleting its owner.")

    def stopped(self, job):
        run = self.services.run_manager.get_run(job["wake_run_id"])
        while run:
            if run.lifecycle.get("cancellation_requested") and run.lifecycle.get("cancellation_origin") != "shutdown":
                return True
            run = self.services.run_manager.get_run(run.parent_run_id) if run.parent_run_id else None
        return False

    async def tick(self):
        for task in self.probes.values():
            if task.done() and not task.cancelled():
                task.result()  # Critical errors must stop this monitoring pass.
        self.probes = {key: task for key, task in self.probes.items() if not task.done()}
        for job in self.records():
            if job["paused"]:
                continue
            try:
                self.authorize(job)
                if self.stopped(job) and not job.get('monitor_after_stop'):
                    self.update(job["id"], paused=True, wake_requested=False, detail="Automatic monitoring stopped; external termination is unconfirmed.")
                    continue
            except (PermissionDeniedError, NotFoundError, RuntimeUnavailableError, PluginUnavailableError):
                self.update(job["id"], paused=True, detail="Monitoring paused because access or its owning resource is unavailable.")
                continue
            if job["external_id"] and job["status"] in ACTIVE and job["next_check_at"] <= time.time() and job["id"] not in self.probes and len(self.probes) < 4:
                self.probes[job["id"]] = asyncio.create_task(self.probe(job))
            if job["status"] not in ACTIVE and job["wake_requested"] and not job["notification_run_id"]:
                await self.deliver(job)

    async def probe(self, job):
        interval = 60
        try:
            provider = self.services.plugins.background_job_provider(job["provider"])
            interval = provider.poll_interval_seconds
            async with asyncio.timeout(90):
                observation = await provider.inspect(JobContext(self, job), job["external_id"])
            self.authorize(job)
            observation = JobObservation.model_validate(observation)
            if observation.status == "unknown":
                raise RuntimeError("Provider status is unknown")
            self.update(job["id"], expected_revision=job["revision"], **observation.model_dump(), failures=0,
                last_observed_at=time.time(), observation_error=None, next_check_at=time.time() + provider.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Revocation is an expected monitor outcome: retain the job without
            # publishing completion. Persistence/security faults still escape.
            raise_if_unsafe(error, allow_revocation=True)
            failures = job["failures"] + 1
            detail = (
                "Sandbox is unavailable for background checks. Start the connected Sandbox or grant the Agent Start permission."
                if isinstance(error, SandboxStateError) else "status unavailable; existing work was not resubmitted."
            )
            self.update(job["id"], expected_revision=job["revision"], failures=failures,
                observation_error=f"{type(error).__name__}: {detail}",
                next_check_at=time.time() + min(3600, interval * 2 ** min(failures, 8)))
        finally:
            self.services.node_execution.signal_continuations()

    async def deliver(self, job):
        manager = self.services.run_manager
        async with self.services._node_mutation():
            job = self.get(job["id"])
            if job["paused"] or not job["wake_requested"] or job["notification_run_id"] or self.stopped(job):
                return
            self.authorize(job)
            parent = manager.get_run(job["wake_run_id"])
            if parent.status not in TERMINAL_RUN_STATUSES or parent.lifecycle.get("execution") in {"running", "closing"}:
                return
            try:
                run = await self.services.node_execution.admit_continuation(parent,
                    "OAW background job observation. Inspect this existing job and its actual outputs. "
                    "Continue the existing approved task in this session. Do not resubmit or assume scientific acceptance.\n"
                    + json.dumps(self.public(job)),
                    reserve=lambda run_id: self.update(job["id"], notification_run_id=run_id),
                    release=lambda: self.update(job["id"], notification_run_id=None),
                    lifecycle={"background_job_notification": job["id"]})
                if run:
                    self.update(job["id"], wake_requested=False)
            except (PermissionDeniedError, NotFoundError):
                self.update(job["id"], paused=True, detail="Continuation requires renewed access or an explicit new turn.")

    async def startup(self):
        for job in self.records():
            if job["status"] in ACTIVE and not job["paused"]:
                # A fresh host can restore local prerequisites. Do not retain
                # an hour-long retry delay from the previous host process.
                job = self.update(job["id"], next_check_at=0)
            if job["status"] == "submitting" and not job["external_id"]:
                self.update(job["id"], status="unknown", detail="Host restarted during submission. Reconcile existing work; never replay.")
            if job["notification_run_id"]:
                try:
                    run = self.services.run_manager.get_run(job["notification_run_id"])
                except NotFoundError:
                    self.update(job["id"], notification_run_id=None)
                else:
                    if run.status in {RunStatus.INTERRUPTED, RunStatus.CANCELLED, RunStatus.FAILED}:
                        self.update(job["id"], paused=True,
                            detail="The admitted continuation did not complete. Inspect its effects and explicitly resume; it was not replayed.")

    async def shutdown(self):
        for task in self.probes.values():
            task.cancel()
        results = await asyncio.gather(*self.probes.values(), return_exceptions=True)
        self.probes.clear()
        for result in results:
            if isinstance(result, Exception):
                raise result
