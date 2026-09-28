"""One optional local scorer process; no remote inference or runtime downloads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from backend.errors import ResourceValidationError, RuntimeUnavailableError


class ReadingScorer:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.process = None
        self.reader = None
        self.stderr = None
        self.gate = asyncio.Lock()
        self.start_gate = asyncio.Lock()
        self.pending = {}
        self.model = None
        self.ready = False
        self.error = None

    def configuration(self):
        path = self.data_root / "reading-scorer.json"
        if not path.is_file():
            raise RuntimeUnavailableError("Local reading scorer is not configured")
        value = json.loads(path.read_text(encoding="utf-8"))
        for key in ("python", "model_dir"):
            if not isinstance(value.get(key), str) or not Path(value[key]).is_absolute() or not Path(value[key]).exists():
                raise RuntimeUnavailableError(f"Local scorer {key} is unavailable")
        return value

    def status(self):
        try:
            config = self.configuration()
            manifest = json.loads((Path(config["model_dir"]) / "scorer-manifest.json").read_text(encoding="utf-8"))
            configured = True
        except (OSError, ValueError, RuntimeUnavailableError):
            manifest, configured = None, False
        return {"configured": configured, "ready": self.ready, "execution": "local-cpu",
                "busy": self.gate.locked(), "model": self.model or manifest, "error": self.error}

    async def start(self):
        async with self.start_gate:
            if self.process is not None and self.process.returncode is None:
                return
            from oaw_library import scoring_worker
            config = self.configuration()
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            self.process = await asyncio.create_subprocess_exec(config["python"], "-u", str(Path(scoring_worker.__file__).resolve()),
                "--model-dir", config["model_dir"], "--threads", "1", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16*1024*1024, **options)
            self.ready, self.error = False, None
            self.reader = asyncio.create_task(self._read())
            self.stderr = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self):
        # Consume library diagnostics without retaining source text or unbounded logs.
        while self.process is not None and await self.process.stderr.read(4096):
            pass

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                pending = self.pending.get(message.get("id"))
                if pending is None:
                    continue
                future, progress = pending
                if message.get("type") == "progress" and progress:
                    progress(message)
                elif message.get("type") in {"result", "error", "cancelled"} and not future.done():
                    future.set_result(message)
        except (ValueError, OSError) as exc:
            self.error = type(exc).__name__
        finally:
            self.ready = False
            for future, _ in self.pending.values():
                if not future.done():
                    future.set_result({"type": "error", "message": "Local scorer process stopped"})

    async def _send(self, value):
        if self.process is None or self.process.returncode is not None:
            raise RuntimeUnavailableError("Local scorer is unavailable")
        self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def request(self, op, arguments=None, progress=None):
        await self.start()
        # Waiting callers are cancellable and never cause parallel model loads.
        async with self.gate:
            identity = str(uuid4())
            future = asyncio.get_running_loop().create_future()
            self.pending[identity] = (future, progress)
            try:
                await self._send({"id": identity, "op": op, **(arguments or {})})
                message = await asyncio.wait_for(asyncio.shield(future), 180)
                if message["type"] == "cancelled":
                    raise asyncio.CancelledError()
                if message["type"] == "error":
                    self.error = message.get("message", "Local scoring failed")
                    raise RuntimeUnavailableError(self.error)
                if op == "probe":
                    self.ready = message["result"].get("ready") is True
                    self.model = message["result"].get("sample", {}).get("model")
                self.error = None
                return message["result"]
            except (asyncio.CancelledError, TimeoutError):
                await self._send({"id": str(uuid4()), "op": "cancel", "target_id": identity})
                # Acknowledge cancellation before letting the next queued job run.
                try:
                    await asyncio.wait_for(asyncio.shield(future), 15)
                except TimeoutError:
                    await self.close()
                raise
            finally:
                self.pending.pop(identity, None)

    async def score(self, arguments, progress=None):
        if not self.ready:
            await self.request("probe")
        return await self.request("score", arguments, progress)

    async def close(self):
        process = self.process
        if process is not None and process.returncode is None:
            try:
                await self._send({"id": str(uuid4()), "op": "shutdown"})
                await asyncio.wait_for(process.wait(), 5)
            except (OSError, TimeoutError, RuntimeUnavailableError):
                if os.name == "nt":
                    # Windows venv launchers may own a separate Python child.
                    killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    await killer.wait()
                elif process.returncode is None:
                    process.kill()
                await process.wait()
        for task in (self.reader, self.stderr):
            if task is not None and not task.done():
                task.cancel()
        self.process, self.ready = None, False


def result_key(model, document_sha256, page, parser_version, text, context_tokens, stride):
    value = {"model": model["manifest_sha256"], "algorithm": model["algorithm_version"],
             "runtime": model.get("versions"), "dtype": model.get("dtype"), "device": model.get("device"),
             "special_tokens": model.get("special_tokens"), "normalization": model.get("normalization"),
             "document": document_sha256, "page": page, "parser": parser_version,
             "text": hashlib.sha256(text.encode("utf-8")).hexdigest(), "context": context_tokens, "stride": stride}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ReadingScoreJobs:
    """Short-lived requests with cancellation; finished scores live in a local cache."""
    def __init__(self, services):
        self.services = services
        self.scorer = ReadingScorer(services.settings.data_root)
        self.jobs = {}
        self.cache = services.settings.data_root / "cache" / "reading-scores"

    def _paper(self, node_id, digest, page):
        from backend.node_documents import read_document
        from backend.document_blobs import read_blob
        if self.services.world.get_card(node_id).type != "library.paper":
            raise ResourceValidationError("Reading scores require a Paper")
        value = read_document(self.services, node_id)["value"]
        if value.get("current_document_version_id") != digest:
            raise ResourceValidationError("The PDF changed. Reload before scoring this page.")
        if page < 1 or page > value["pages"]:
            raise ResourceValidationError("Page is outside the PDF")
        return value

    async def create(self, node_id, request):
        async with self.services._node_mutation(read_only=True):
            self._paper(node_id, request.document_sha256, request.page)
        active = [job for job in self.jobs.values() if job["status"] in {"queued", "running"}]
        if len(active) >= 3:
            raise ResourceValidationError("Local scoring queue is full; cancel an earlier request")
        for key, job in list(self.jobs.items()):
            if job["status"] not in {"queued", "running"} and time.monotonic() - job["created"] > 60:
                self.jobs.pop(key)
        identity = str(uuid4())
        self.jobs[identity] = {"id": identity, "paper_id": node_id, "status": "queued", "created": time.monotonic(),
                               "page": request.page, "document_sha256": request.document_sha256, "progress": None}
        task = asyncio.create_task(self._run(identity, request))
        self.jobs[identity]["task"] = task
        return self.read(node_id, identity)

    async def _run(self, identity, request):
        job = self.jobs[identity]
        try:
            job["status"] = "running"
            if not self.scorer.ready:
                await self.scorer.request("probe")
            key = result_key(self.scorer.model, request.document_sha256, request.page, request.text_parser_version,
                             request.text, request.context_tokens, request.stride)
            path = self.cache / (key + ".json")
            if path.is_file():
                result = json.loads(path.read_text(encoding="utf-8"))
                cached = True
            else:
                result = await self.scorer.score({"text": request.text, "context_tokens": request.context_tokens, "stride": request.stride},
                    lambda progress: job.update(progress={"scored_tokens": progress["scored_tokens"], "total_tokens": progress["total_tokens"]}))
                cached = False
                self.cache.mkdir(parents=True, exist_ok=True)
                # The cache contains score metadata and source snippets, entirely local.
                temporary = path.with_suffix("." + identity + ".tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                temporary.replace(path)
                # Bounded cache, never remove user document/history storage.
                files = sorted(self.cache.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
                for expired in files[100:]:
                    expired.unlink()
            async with self.services._node_mutation(read_only=True):
                self._paper(job["paper_id"], request.document_sha256, request.page)
            if job["status"] == "cancelled":
                return
            job.update(status="complete", result=result, cached=cached)
        except asyncio.CancelledError:
            job.update(status="cancelled")
        except Exception as exc:
            job.update(status="failed", error=str(exc))

    def read(self, node_id, identity):
        job = self.jobs.get(identity)
        if job is None or job["paper_id"] != node_id:
            from backend.errors import NotFoundError
            raise NotFoundError("Reading score request not found")
        self.services.world.get_card(node_id)
        return {key: value for key, value in job.items() if key not in {"task", "created"}}

    async def cancel(self, node_id, identity):
        self.read(node_id, identity)
        job = self.jobs[identity]
        if job["status"] in {"queued", "running"}:
            job["status"] = "cancelled"
            job["task"].cancel()
        return self.read(node_id, identity)

    async def close(self):
        tasks = [job["task"] for job in self.jobs.values() if not job["task"].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.scorer.close()
