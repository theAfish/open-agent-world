"""``kb``: drive the knowledge base from a terminal, with or without a service.

Every command resolves to one operation from :mod:`oaw_knowledge_base.operations`, so
the CLI, the HTTP service, MCP and the OAW card all execute the same code. Two modes:

* **In process** (default): open the store here. Refuses a store a running service
  holds, because two MKB job threads on one SQLite file race the same job rows.
* **Against a service** (``--service URL`` or ``KB_SERVICE_URL``): post the same
  arguments over HTTP.

``kb project``, ``kb records extract`` and ``kb insights extract`` are what
make the whole loop testable with no OAW: each builds the same prompt OAW's host bridge
builds, calls an OpenAI-compatible endpoint named by the environment, and saves the
answer. Model credentials stay in the operator's shell and never enter the store or
the service.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import signal
import sys
import time
from pathlib import Path

from ..errors import KnowledgeError, operator_message
from .store import Store, StoreBusy, hold_lock, require_free, resolve_store

ACTIVE_JOBS = {"QUEUED", "RUNNING", "PENDING", "RETRYING"}
FAILED_JOBS = {"FAILED", "CANCELLED", "CANCELED"}
DEFAULT_MODEL_URL = "https://api.openai.com/v1"


class CliError(RuntimeError):
    """Anything the operator can fix, printed without a traceback."""


# ---------------------------------------------------------------- backends


class Local:
    """The store opened in this process; the job thread lives here too."""

    remote = False

    def __init__(self, path):
        require_free(path)
        self.store = Store(path)

    def call(self, operation, arguments=None, *, confirm=False):
        return self.store.run(operation, arguments or {}, confirmed=confirm)

    def close(self):
        self.store.close()


class Remote:
    """A running service, reached with the same arguments over HTTP."""

    remote = True

    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def call(self, operation, arguments=None, *, confirm=False):
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = httpx.post(
                f"{self.url}/v1/{operation}",
                json=arguments or {}, params={"confirm": "true"} if confirm else None,
                headers=headers, timeout=300)
        except httpx.HTTPError as error:
            raise CliError(f"Could not reach {self.url}: {error}") from None
        if response.status_code >= 400:
            raise CliError(f"{operation} failed ({response.status_code}): "
                           f"{_detail(response)}")
        return response.json()

    def close(self):
        pass


def _detail(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    return payload.get("detail") if isinstance(payload, dict) else str(payload)[:500]


def backend_for(args):
    url = args.service or os.environ.get("KB_SERVICE_URL")
    if url:
        return Remote(url, args.token or os.environ.get("KB_SERVICE_TOKEN"))
    return Local(resolve_store(args.store))


# ---------------------------------------------------------------- output


def show(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _read_json(path):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except OSError as error:
        raise CliError(f"Could not read {path}: {error}") from None
    except ValueError as error:
        raise CliError(f"{path} is not valid JSON: {error}") from None


# ---------------------------------------------------------------- commands


def cmd_serve(args, _backend):
    import uvicorn

    from .app import create_app

    token = args.token or os.environ.get("KB_SERVICE_TOKEN")
    admin = os.environ.get("KB_ADMIN_TOKEN")
    if args.host not in ("127.0.0.1", "::1", "localhost") and not token:
        raise CliError("Refusing to serve on a non-loopback address without a token. "
                       "Set KB_SERVICE_TOKEN, or bind 127.0.0.1.")
    path = resolve_store(args.store)
    url = f"http://{args.host}:{args.port}"
    with hold_lock(path, url):
        store = Store(path)
        store.open()  # Fail now, loudly, rather than on the first request.
        print(f"knowledge base: {path}", file=sys.stderr)
        print(f"listening on {url}  (token: {'yes' if token else 'no'}, "
              f"approval over HTTP: {'yes' if admin else 'no'})", file=sys.stderr)
        # Once uvicorn has shut down it re-raises the signal that stopped it, with the
        # original handlers restored — so the default SIGTERM action would kill us
        # before the lock is released. Turn that second delivery into a normal exit.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            uvicorn.run(create_app(store, token=token, admin_token=admin),
                        host=args.host, port=args.port, log_level=args.log_level)
        finally:
            store.close()
    return 0


def cmd_mcp(args, _backend):
    from .mcp import serve_stdio

    path = resolve_store(args.store)
    require_free(path)
    store = Store(path)
    try:
        serve_stdio(store)
    finally:
        store.close()
    return 0


def cmd_overview(args, backend):
    arguments = {"all_groups": not args.group}
    if args.group:
        arguments["group_id"] = args.group
    show(backend.call("overview", arguments))
    return 0


def cmd_settings(args, backend):
    patch = {key: value for key, value in (
        ("pdf_engine", args.pdf_engine),
        ("mineru_base_url", args.mineru_base_url)) if value is not None}
    show(backend.call("settings", patch))
    return 0


def _wait(backend, job_id, timeout):
    deadline = time.monotonic() + timeout
    printed = 0
    while True:
        payload = backend.call("jobs", {"job_id": job_id})
        job, events = payload["job"], payload.get("events", [])
        for event in events[printed:]:
            print(f"  {event.get('step') or job['kind']}: {event.get('message') or event.get('event')}",
                  file=sys.stderr)
        printed = len(events)
        if job["status"] not in ACTIVE_JOBS:
            print(f"  job {job['status']}" + (f": {job['error']}" if job.get("error") else ""),
                  file=sys.stderr)
            return job
        if time.monotonic() > deadline:
            raise CliError(f"Job {job_id} is still {job['status']} after {timeout}s")
        time.sleep(1.0)


def cmd_jobs(args, backend):
    if args.job and args.watch:
        return 0 if _wait(backend, args.job, args.timeout)["status"] not in FAILED_JOBS else 1
    if args.job:
        show(backend.call("jobs", {"job_id": args.job}))
        return 0
    arguments = {"limit": args.limit}
    if args.group:
        arguments["group_id"] = args.group
    while True:
        payload = backend.call("jobs", arguments)
        show(payload)
        active = [job for job in payload["jobs"] if job["status"] in ACTIVE_JOBS]
        if not args.watch or not active:
            return 0
        time.sleep(2.0)


def cmd_markdown(args, backend):
    arguments = {"limit": args.limit, "offset": args.offset}
    arguments.update({"source_id": args.source} if args.source else {"record_id": args.record})
    payload = backend.call("markdown", arguments)
    if args.out:
        Path(args.out).write_text(payload["markdown"], "utf-8")
        print(f"{args.out}: {len(payload['markdown'])} of {payload['total_characters']} characters",
              file=sys.stderr)
        return 0
    print(payload["markdown"])
    return 0


def cmd_schema(args, backend):
    if args.operation == "list":
        show(backend.call("schemas", {"operation": "list"}))
        return 0
    if args.operation == "show":
        show(backend.call("schemas", {"operation": "get", "schema_id": args.schema_id}))
        return 0
    body = _read_json(args.file)
    if args.operation == "add":
        required = {"name", "definition", "system_prompt"}
        missing = sorted(required - set(body))
        if missing:
            raise CliError(f"{args.file} is missing: {', '.join(missing)}")
        show(backend.call("schema_manage", {"operation": "create", "kind": "experiment", **body}))
        return 0
    show(backend.call("schema_manage", {"operation": "update", "schema_id": args.schema_id, **body}))
    return 0


def _complete(messages, *, base_url, api_key, model, json_mode):
    import httpx

    if not api_key:
        raise CliError("Set KB_MODEL_API_KEY (and KB_MODEL_BASE_URL / KB_MODEL) to extract.")
    payload = {"model": model, "messages": messages, "temperature": 0}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {api_key}"}, timeout=600)
    except httpx.HTTPError as error:
        raise CliError(f"Could not reach the model endpoint: {error}") from None
    if response.status_code >= 400:
        # Never echo a provider body: it can quote the request, including the key.
        raise CliError(f"The model endpoint returned {response.status_code}. "
                       "Check KB_MODEL, KB_MODEL_BASE_URL and KB_MODEL_API_KEY.")
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise CliError("The model endpoint returned an unexpected response shape") from None


def _model_options(args):
    return {"base_url": args.model_url or os.environ.get("KB_MODEL_BASE_URL") or DEFAULT_MODEL_URL,
            "api_key": os.environ.get("KB_MODEL_API_KEY"),
            "model": args.model or os.environ.get("KB_MODEL") or "gpt-4o-mini",
            "json_mode": not args.no_json_mode}


def _paged(backend, operation, arguments):
    """One prompt's whole document, following ``has_more`` from the first window."""
    prompt = backend.call(operation, {**arguments, "limit": 200_000})
    text = prompt["markdown"]
    while prompt["has_more"]:
        prompt = backend.call(operation, {**arguments, "limit": 200_000, "offset": len(text)})
        text += prompt["markdown"]
    return prompt, text


def cmd_records(args, backend):
    """``kb records`` — list, show, extract, publish, unpublish, delete structured records."""
    operation = args.operation
    if operation == "list":
        arguments = {"operation": "list", "limit": args.limit, "all_groups": not args.group}
        for key, value in (("group_id", args.group), ("schema_id", args.schema),
                           ("source_id", args.source), ("status", args.status)):
            if value:
                arguments[key] = value
        show(backend.call("experiments", arguments))
        return 0
    if operation == "extract":
        return _extract_records(args, backend)
    if not args.record_id:
        raise CliError(f"Pass the record id to {operation}")
    if operation == "show":
        show(backend.call("experiments", {"operation": "get", "record_id": args.record_id}))
        return 0
    show(backend.call("experiment_review", {"operation": operation, "record_id": args.record_id},
                      confirm=operation == "publish"))
    return 0


def _extract_records(args, backend):
    from .. import experiment_prompt

    if not args.bundle:
        raise CliError("Pass --bundle to extract structured records from an experiment")
    prompt, text = _paged(backend, "experiment_extract_prompt", {"bundle_id": args.bundle})
    answer = _complete(experiment_prompt.build_messages(prompt, markdown=text), **_model_options(args))
    records = [{"name": item["name"], "data": item["data"], "note": item["note"], "source_ids": item.get("source_ids", [])}
               for item in experiment_prompt.parse_extract(answer)]
    if args.dry_run:
        show({"records": records})
        return 0
    show(backend.call("experiment_save", {"bundle_id": args.bundle,
                                          "records": records, "replace_drafts": args.replace}))
    return 0


def cmd_graph(args, backend):
    if args.traverse:
        arguments = {"operation": "traverse", "entity_id": args.traverse,
                    "max_depth": args.depth, "direction": args.direction, "limit": args.limit}
        if args.group:
            arguments["group_id"] = args.group
        show(backend.call("graph", arguments))
        return 0
    arguments = {"operation": "query", "limit": args.limit}
    if args.type:
        arguments["entity_type"] = args.type
    if args.relation:
        arguments["relation_type"] = args.relation
    if args.name:
        arguments["name_contains"] = args.name
    if args.group:
        arguments["group_id"] = args.group
    if args.layer:
        arguments["layer"] = args.layer
    show(backend.call("graph", arguments))
    return 0


def _insight_scope(args):
    scope = {"schema_ids": args.schema or [], "all_groups": not args.group,
             "schema_kind": args.schema_kind}
    if args.group:
        scope["group_id"] = args.group
    return scope


def cmd_insights(args, backend):
    """``kb insights`` — list, show, stats, extract, publish, unpublish, delete."""
    operation = args.operation
    if operation == "list":
        arguments = {"operation": "list", "limit": args.limit, "all_groups": not args.group,
                     "schema_ids": args.schema or []}
        for key, value in (("group_id", args.group), ("kind", args.kind), ("status", args.status),
                           ("query", args.query)):
            if value:
                arguments[key] = value
        show(backend.call("insights", arguments))
        return 0
    if operation == "show":
        if not args.insight_ids:
            raise CliError("Pass the insight id to show")
        show(backend.call("insights", {"operation": "get", "insight_id": args.insight_ids[0]}))
        return 0
    if operation == "stats":
        show(backend.call("insights", {"operation": "stats", **_insight_scope(args)}))
        return 0
    if operation == "extract":
        return _extract_insights(args, backend)
    if not args.insight_ids:
        raise CliError(f"Pass at least one insight id to {operation}")
    # Publishing is a deliberate act: confirmed here, and over HTTP it needs the
    # separate admin token the service only has when someone sets KB_ADMIN_TOKEN.
    show(backend.call("insight_review", {"operation": operation, "insight_ids": args.insight_ids},
                      confirm=operation == "publish"))
    return 0


def _extract_insights(args, backend):
    from .. import insight_prompt

    scope = _insight_scope(args)
    prompt, table = _paged(backend, "insight_prompt", scope)
    answer = _complete(insight_prompt.build_messages(prompt, table=table), **_model_options(args))
    insights = insight_prompt.parse_insights(answer)
    if args.dry_run:
        show({"insights": insights})
        return 0
    show(backend.call("insight_save", {**scope, "insights": insights,
                                       "replace_drafts": args.replace}))
    return 0


def cmd_tools(args, _backend):
    from ..operations import tool_manifest

    show({"tools": tool_manifest()})
    return 0


# ---------------------------------------------------------------- parser


def _shared(parser, *, sub):
    """The options that make sense before or after the command name.

    The copies on each subcommand default to ``SUPPRESS`` so that omitting one leaves
    whatever was given before the command name intact — ``kb --store X graph`` and
    ``kb graph --store X`` both work.
    """
    def default(value):
        return argparse.SUPPRESS if sub else value

    hide = argparse.SUPPRESS if sub else None
    parser.add_argument("--store", default=default(None), help=hide or
                        "Store directory (default $KB_SERVICE_STORE or "
                        "~/.local/share/oaw-knowledge)")
    parser.add_argument("--service", default=default(None), help=hide or
                        "Talk to a running service instead of opening the store here "
                        "(default $KB_SERVICE_URL)")
    parser.add_argument("--token", default=default(None), help=hide or
                        "Bearer token for --service (default $KB_SERVICE_TOKEN)")
    return parser


def cmd_projects(args, backend):
    show(backend.call("projects", {"project_id": args.project_id} if args.project_id else {}))
    return 0


def cmd_import(args, backend):
    options = {"grouping": args.grouping, "depth": args.depth}
    if args.preview_token:
        if args.files:
            raise CliError("Use files or --preview-token, not both")
        token = args.preview_token
        if not args.commit:
            show(backend.call("project_import_preview", {"token": token, **options}))
            return 0
    else:
        if not args.files:
            raise CliError("Pass files, a directory or an archive to import")
        files = []
        total = 0
        for name in args.files:
            root = Path(name)
            paths = sorted(root.rglob("*")) if root.is_dir() else [root]
            for path in paths:
                if path.is_symlink():
                    raise CliError(f"Symbolic links are not imported: {path}")
                if not path.is_file():
                    continue
                size = path.stat().st_size
                total += size
                if total > 32 * 1024 * 1024 or len(files) >= 1000:
                    raise CliError("One import is limited to 32 MiB and 1000 files")
                relative = (root.name + "/" + path.relative_to(root).as_posix()) if root.is_dir() else path.name
                files.append({"filename": path.name, "relative_path": relative,
                              "content_base64": base64.b64encode(path.read_bytes()).decode(),
                              "media_type": mimetypes.guess_type(path.name)[0]})
        preview = backend.call("project_import_preview", {"files": files, **options})
        token = preview["token"]
        if not args.commit:
            show(preview)
            return 0
    arguments = {"token": token, **options, "auto_process": not args.no_convert}
    if args.project_id:
        if args.grouping != "single":
            raise CliError("Adding files to a project requires --grouping single")
        arguments["projects"] = [{"key": "__all__", "name": "Existing project", "project_id": args.project_id}]
    result = backend.call("project_import_commit", arguments)
    if not backend.remote:
        for job in result["jobs"]:
            if job.get("job"):
                completed = _wait(backend, job["job"]["id"], args.timeout)
                if completed["status"] in FAILED_JOBS:
                    show(result)
                    return 1
    show(result)
    return 0


def cmd_convert(args, backend):
    result = backend.call("project_process", {"project_ids": args.project_ids})
    if not backend.remote:
        for item in result["jobs"]:
            if item.get("job") and _wait(backend, item["job"]["id"], args.timeout)["status"] in FAILED_JOBS:
                show(result)
                return 1
    show(result)
    return 0


def cmd_spaces(args, backend):
    if args.operation in {"list", "show"}:
        arguments = {"operation": "list"} if args.operation == "list" else {"operation": "get", "space_id": args.space}
        show(backend.call("spaces", arguments))
        return 0
    if not args.file:
        raise CliError("Pass --file with the Space's name, kind, definition and system_prompt")
    arguments = {**_read_json(args.file), "operation": "create" if args.operation == "add" else "update"}
    if args.operation == "update":
        arguments["space_id"] = args.space
    show(backend.call("space_manage", arguments))
    return 0


def cmd_project(args, backend):
    from .. import spaces

    prepared = backend.call("projection_prepare", {"project_ids": args.project_ids,
        "space_id": args.space, "rerun": args.rerun})
    results = []
    for projection in prepared["projections"]:
        if projection.get("disposition") != "created":
            results.append(projection)
            continue
        try:
            prompt, markdown = _paged(backend, "projection_prompt", {"projection_id": projection["id"]})
            answer = _complete(spaces.build_messages(prompt, markdown=markdown), **_model_options(args))
            saved = backend.call("projection_save", {"projection_id": projection["id"],
                "expected_revision": projection["revision"], "data": spaces.parse_output(answer)})
            results.append(saved["projection"])
        except Exception as error:
            backend.call("projection_fail", {"projection_id": projection["id"],
                "expected_revision": projection["revision"], "error": str(error)[:8000]})
            raise
    show({"projections": results})
    return 0


def cmd_review(args, backend):
    from .. import spaces

    projection = backend.call("projections", {"operation": "get", "projection_id": args.projection_id})["projection"]
    prompt, markdown = _paged(backend, "projection_review_prompt", {"projection_id": args.projection_id})
    answer = _complete(spaces.build_messages(prompt, markdown=markdown, review=True), **_model_options(args))
    show(backend.call("projection_revise", {"projection_id": args.projection_id,
        "expected_revision": projection["revision"], "note": args.note, "data": spaces.parse_output(answer)}))
    return 0


def cmd_call(args, backend):
    show(backend.call(args.operation, _read_json(args.file) if args.file else {}, confirm=args.confirm))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="kb", description=__doc__.splitlines()[0])
    _shared(parser, sub=False)
    common = _shared(argparse.ArgumentParser(add_help=False), sub=True)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, function, help, *, backend=True):
        item = sub.add_parser(name, help=help, parents=[common])
        item.set_defaults(handler=function, needs_backend=backend)
        return item

    def scope(item):
        item.add_argument("--project", dest="group", help="Restrict to one project id")
        item.set_defaults(all_groups=True)

    def model_flags(item, *, dry_run=False):
        item.add_argument("--model", help="Model name (default $KB_MODEL)")
        item.add_argument("--model-url", help="OpenAI-compatible endpoint (default $KB_MODEL_BASE_URL)")
        item.add_argument("--no-json-mode", action="store_true")
        if dry_run:
            item.add_argument("--dry-run", action="store_true")

    serve = add("serve", cmd_serve, "Run the HTTP service", backend=False)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8931)
    serve.add_argument("--log-level", default="info")
    add("mcp", cmd_mcp, "Run MCP over stdio", backend=False)
    add("tools", cmd_tools, "Print available agent tools", backend=False)
    scope(add("overview", cmd_overview, "Knowledge base counts and conversion engines"))
    settings = add("settings", cmd_settings, "Configure conversion")
    settings.add_argument("--pdf-engine", choices=("auto", "pymupdf4llm", "mineru", "text"))
    settings.add_argument("--mineru-base-url")
    project_list = add("projects", cmd_projects, "List projects or open one project's files")
    project_list.add_argument("project_id", nargs="?")
    importer = add("import", cmd_import, "Preview file, folder or archive project boundaries")
    importer.add_argument("files", nargs="*")
    importer.add_argument("--preview-token")
    importer.add_argument("--grouping", choices=("top", "leaf", "single", "depth", "files"), default="top")
    importer.add_argument("--depth", type=int, default=1)
    importer.add_argument("--commit", action="store_true")
    importer.add_argument("--project", dest="project_id")
    importer.add_argument("--no-convert", action="store_true")
    importer.add_argument("--timeout", type=float, default=900)
    converter = add("convert", cmd_convert, "Convert current files in selected projects")
    converter.add_argument("project_ids", nargs="+")
    converter.add_argument("--timeout", type=float, default=900)
    space_list = add("spaces", cmd_spaces, "List, inspect or define projection Spaces")
    space_list.add_argument("operation", choices=("list", "show", "add", "update"))
    space_list.add_argument("--space")
    space_list.add_argument("--file")
    project = add("project", cmd_project, "Project complete project Markdown through a Space")
    project.add_argument("project_ids", nargs="+")
    project.add_argument("--space", required=True)
    project.add_argument("--rerun", action="store_true")
    model_flags(project)
    review = add("review", cmd_review, "Optionally review a projection and retain its history")
    review.add_argument("projection_id")
    review.add_argument("--note", default="Reviewed against source materials")
    model_flags(review)
    jobs = add("jobs", cmd_jobs, "List or follow conversion jobs")
    jobs.add_argument("--job")
    jobs.add_argument("--watch", action="store_true")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument("--timeout", type=float, default=900)
    scope(jobs)
    markdown = add("markdown", cmd_markdown, "Read converted source Markdown")
    selected = markdown.add_mutually_exclusive_group(required=True)
    selected.add_argument("--source")
    selected.add_argument("--record")
    markdown.add_argument("--limit", type=int, default=40000)
    markdown.add_argument("--offset", type=int, default=0)
    markdown.add_argument("--out")
    schema = add("schema", cmd_schema, "Manage experimental record schemas")
    schema.add_argument("operation", choices=("list", "show", "add", "update"))
    schema.add_argument("--file")
    schema.add_argument("--schema-id")
    records = add("records", cmd_records, "Extract experiment records and inspect or publish them")
    records.add_argument("operation", choices=("list", "show", "extract", "publish", "unpublish", "delete"))
    records.add_argument("record_id", nargs="?")
    records.add_argument("--schema")
    records.add_argument("--bundle")
    records.add_argument("--source", help="Filter record listings by evidence source")
    records.add_argument("--status", choices=("draft", "published"))
    records.add_argument("--limit", type=int, default=50)
    records.add_argument("--replace", action="store_true")
    scope(records)
    model_flags(records, dry_run=True)
    graph = add("graph", cmd_graph, "Query the published insight graph")
    graph.add_argument("--traverse")
    graph.add_argument("--depth", type=int, default=1)
    graph.add_argument("--direction", choices=("both", "out", "in"), default="both")
    graph.add_argument("--type")
    graph.add_argument("--relation")
    graph.add_argument("--name")
    graph.add_argument("--limit", type=int, default=200)
    graph.set_defaults(layer="experiment")
    scope(graph)
    insights = add("insights", cmd_insights, "Build insights from published experimental JSON records")
    insights.add_argument("operation", choices=("list", "show", "stats", "extract", "publish", "unpublish", "delete"))
    insights.add_argument("insight_ids", nargs="*")
    insights.add_argument("--schema", action="append")
    insights.set_defaults(schema_kind="experiment")
    insights.add_argument("--query")
    insights.add_argument("--kind", choices=("anomaly", "rule", "trend", "correlation", "threshold", "note"))
    insights.add_argument("--status", choices=("draft", "published"))
    insights.add_argument("--limit", type=int, default=50)
    insights.add_argument("--replace", action="store_true")
    scope(insights)
    model_flags(insights, dry_run=True)
    generic = add("call", cmd_call, "Call an operation using a JSON request file")
    generic.add_argument("operation")
    generic.add_argument("--file")
    generic.add_argument("--confirm", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    backend = None
    try:
        if args.needs_backend:
            backend = backend_for(args)
        return args.handler(args, backend)
    except (CliError, KnowledgeError, StoreBusy) as error:
        print(f"kb: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # MKB's and pydantic's own refusals read like ours; anything else is a bug
        # and keeps its traceback.
        message = operator_message(error)
        if message is None:
            raise
        print(f"kb: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
