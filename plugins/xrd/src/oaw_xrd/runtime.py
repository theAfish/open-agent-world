import asyncio
import base64
import io
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path
from open_agent_world.plugin_api import RuntimeProvider, AgentInfo, AgentStatus, AgentEvent, AgentEventType, AgentNotFoundError

class XRDRuntime(RuntimeProvider):
    def __init__(self, capability_provider):
        self.records = {}
        self.processes = {}
        self.root = Path(os.environ.get("OAW_XRD_ROOT", str(Path(__file__).resolve().parents[5] / "XRD")))

    async def create_agent(self, config):
        self.records[config.agent_id] = AgentInfo(config, AgentStatus.IDLE, f"xrd-{config.agent_id}", details={"engine":"PyWPEM", "project":str(self.root)})
        return self.records[config.agent_id]

    async def update_agent(self, config):
        return await self.create_agent(config)

    async def get_agent(self, agent_id):
        if agent_id not in self.records:
            raise AgentNotFoundError(agent_id)
        info = self.records[agent_id]
        runs = sorted((self.root / "runs").glob("oaw-*/oaw.json"), key=lambda p:p.stat().st_mtime, reverse=True)
        for path in runs:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["agent_id"] != agent_id: continue
                result_path = path.parent / "result.json"
                if not result_path.exists():
                    return replace(info, details={**info.details, "last_run":record})
                result = json.loads(result_path.read_text(encoding="utf-8"))
                outputs = [p for p in path.parent.rglob("*") if p.is_file() and p.name != "input.json"]
                if sum(p.stat().st_size for p in outputs) > 30*1024*1024:
                    return replace(info, details={**info.details, "last_run":record, "result":result,
                        "archive_note":f"Results exceed 30 MiB. Open local folder: {path.parent}"})
                data = io.BytesIO()
                with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as archive:
                    for output in outputs:
                        archive.write(output, str(output.relative_to(path.parent)))
                return replace(info, details={**info.details,"last_run":record,"result":result,
                    "archive":base64.b64encode(data.getvalue()).decode()})
            except (OSError, ValueError): continue
        return info

    async def delete_agent(self, agent_id):
        self.records.pop(agent_id, None)

    async def stop(self, run_id):
        process = self.processes.get(run_id)
        if process and process.returncode is None:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill.exe", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                process.terminate()
            await process.wait()

    async def execute(self, config, context, runtime_input):
        from . import XRDConfig
        options = XRDConfig.model_validate(dict(config.provider_config))
        if options.low_angle >= options.high_angle:
            raise ValueError("Low angle must be below high angle")
        if not options.demo and (not options.cif.strip() or not options.intensity_csv.strip()):
            raise ValueError("Upload intensity CSV and candidate CIF")
        python = self.root / ".venv-xrd" / "Scripts" / "python.exe"
        if not python.is_file():
            raise ValueError(f"PyWPEM interpreter not found: {python}; configure OAW_XRD_ROOT")
        run = self.root / "runs" / f"oaw-{context.run_id}"
        run.mkdir(parents=True, exist_ok=False)
        (run / "input.json").write_text(options.model_dump_json(), encoding="utf-8")
        manifest = {"run_id":context.run_id,"agent_id":config.agent_id,"engine_root":str(self.root),"status":"running"}
        (run / "oaw.json").write_text(json.dumps(manifest), encoding="utf-8")
        def event(kind, payload, status=None):
            return AgentEvent(config.agent_id,context.run_id,kind,payload,run_status=status)
        yield event(AgentEventType.MESSAGE,{"text":f"PyWPEM started. Results: {run}"})
        try:
            with (run / "console.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(str(python),"-u",str(Path(__file__).with_name("worker.py")),str(self.root),str(run),stdout=log,stderr=log)
                self.processes[context.run_id] = process
                while process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(),timeout=10)
                    except asyncio.TimeoutError:
                        yield event(AgentEventType.MESSAGE,{"text":"PyWPEM fitting…"})
            if process.returncode != 0:
                tail=(run / "console.log").read_text(encoding="utf-8",errors="replace")[-5000:]
                raise RuntimeError(f"PyWPEM exited {process.returncode}: {tail}")
            result=json.loads((run / "result.json").read_text(encoding="utf-8"))
            manifest["status"]="completed"
            text=json.dumps({"result_directory":str(run),**result},ensure_ascii=False,indent=2)
            yield event(AgentEventType.MESSAGE,{"text":text,"final":True})
            yield event(AgentEventType.COMPLETED,{"text":text},"succeeded")
        except asyncio.CancelledError:
            manifest["status"]="cancelled"
            raise
        except Exception:
            manifest["status"]="failed"
            raise
        finally:
            await self.stop(context.run_id)
            self.processes.pop(context.run_id,None)
            (run / "oaw.json").write_text(json.dumps(manifest),encoding="utf-8")
