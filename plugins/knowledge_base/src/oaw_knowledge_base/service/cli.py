"""``kb``: drive the knowledge base from a terminal, with or without a service.

Every command resolves to one operation from :mod:`oaw_knowledge_base.operations`, so
the CLI, the HTTP service, MCP and the OAW card all execute the same code. Two modes:

* **In process** (default): open the store here. Refuses a store a running service
  holds, because two MKB job threads on one SQLite file race the same job rows.
* **Against a service** (``--service URL`` or ``KB_SERVICE_URL``): post the same
  arguments over HTTP.

``kb project`` is the piece that makes the whole loop testable with no OAW: it builds
the same prompt OAW's host bridge builds, calls an OpenAI-compatible endpoint named by
the environment, and saves the answer. Model credentials stay in the operator's shell
and never enter the store or the service.
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
from urllib.parse import quote

from ..errors import KnowledgeError, operator_message
from .store import DEFAULT_COLLECTION, Store, StoreBusy, hold_lock, require_free, resolve_store

ACTIVE_JOBS = {"QUEUED", "RUNNING", "PENDING", "RETRYING"}
FAILED_JOBS = {"FAILED", "CANCELLED", "CANCELED"}
DEFAULT_MODEL_URL = "https://api.openai.com/v1"


class CliError(RuntimeError):
    """Anything the operator can fix, printed without a traceback."""


# ---------------------------------------------------------------- backends


class Local:
    """The store opened in this process; the job thread lives here too."""

    remote = False

    def __init__(self, path, collection):
        require_free(path)
        self.store = Store(path)
        self.collection = collection

    def call(self, operation, arguments=None, *, confirm=False):
        return self.store.run(operation, arguments or {}, collection=self.collection,
                              confirmed=confirm)

    def close(self):
        self.store.close()


class Remote:
    """A running service, reached with the same arguments over HTTP."""

    remote = True

    def __init__(self, url, token, collection):
        self.url = url.rstrip("/")
        self.token = token
        self.collection = collection

    def call(self, operation, arguments=None, *, confirm=False):
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = httpx.post(
                f"{self.url}/v1/collections/{quote(self.collection, safe='')}/{operation}",
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
        return Remote(url, args.token or os.environ.get("KB_SERVICE_TOKEN"), args.collection)
    return Local(resolve_store(args.store), args.collection)


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
        serve_stdio(store, collection=args.collection)
    finally:
        store.close()
    return 0


def cmd_overview(args, backend):
    show(backend.call("overview"))
    return 0


def cmd_settings(args, backend):
    patch = {key: value for key, value in (
        ("collection_name", args.collection_name), ("pdf_engine", args.pdf_engine),
        ("mineru_base_url", args.mineru_base_url)) if value is not None}
    show(backend.call("settings", patch))
    return 0


def cmd_ingest(args, backend):
    results = []
    for name in args.files:
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CliError(f"Could not read {path}: {error}") from None
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        result = backend.call("ingest", {
            "filename": path.name, "media_type": media_type,
            "content_base64": base64.b64encode(data).decode("ascii")})
        results.append(result)
        print(f"{path.name}: source {result['source']['id']}  job {result['job']['id']}",
              file=sys.stderr)
    # In process the job thread dies with this command, so wait for the conversion.
    if not backend.remote and not args.no_wait:
        for result in results:
            _wait(backend, result["job"]["id"], args.timeout)
    show({"ingested": results})
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
    while True:
        payload = backend.call("jobs", {"limit": args.limit})
        show(payload)
        active = [job for job in payload["jobs"] if job["status"] in ACTIVE_JOBS]
        if not args.watch or not active:
            return 0
        time.sleep(2.0)


def cmd_sources(args, backend):
    show(backend.call("sources", {"limit": args.limit, "offset": args.offset}))
    return 0


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
        show(backend.call("schemas", {"operation": "create", **body}))
        return 0
    show(backend.call("schemas", {"operation": "update", "schema_id": args.schema_id, **body}))
    return 0


def _prompt(backend, args):
    arguments = {"schema_id": args.schema, "limit": args.limit}
    arguments.update({"source_id": args.source} if args.source else {"record_id": args.record})
    return backend.call("projection_prompt", arguments)


def cmd_prompt(args, backend):
    show(_prompt(backend, args))
    return 0


def cmd_project(args, backend):
    from .. import projection

    prompt = _prompt(backend, args)
    answer = _complete(projection.build_messages(prompt),
                       base_url=args.model_url or os.environ.get("KB_MODEL_BASE_URL")
                       or DEFAULT_MODEL_URL,
                       api_key=os.environ.get("KB_MODEL_API_KEY"),
                       model=args.model or os.environ.get("KB_MODEL") or "gpt-4o-mini",
                       json_mode=not args.no_json_mode)
    data = projection.parse_projection(answer)
    if args.dry_run:
        show(data)
        return 0
    show(backend.call("save_projection", {
        "schema_id": prompt["schema_id"], "record_id": prompt["record_id"],
        "data": data, "model": args.model or os.environ.get("KB_MODEL"),
        "notes": args.notes}))
    return 0


def _complete(messages, *, base_url, api_key, model, json_mode):
    import httpx

    if not api_key:
        raise CliError("Set KB_MODEL_API_KEY (and KB_MODEL_BASE_URL / KB_MODEL) to project.")
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


def cmd_save_projection(args, backend):
    show(backend.call("save_projection", {
        "schema_id": args.schema, "record_id": args.record, "data": _read_json(args.file),
        "model": args.model, "notes": args.notes}))
    return 0


def cmd_projections(args, backend):
    if args.projection_id:
        show(backend.call("projections", {"projection_id": args.projection_id}))
        return 0
    arguments = {"limit": args.limit}
    if args.record:
        arguments["record_id"] = args.record
    if args.schema:
        arguments["schema_id"] = args.schema
    show(backend.call("projections", arguments))
    return 0


def cmd_draft(args, backend):
    if args.operation == "list":
        show(backend.call("draft", {"operation": "list", "limit": args.limit}))
        return 0
    if args.operation == "show":
        show(backend.call("draft", {"operation": "get", "draft_id": args.draft_id}))
        return 0
    if not args.projection:
        raise CliError("Pass at least one --projection ID to create a draft")
    show(backend.call("draft", {"operation": "create", "projection_ids": args.projection}))
    return 0


def _review(args, backend, operation, *, confirm=False):
    show(backend.call("review", {"operation": operation, "draft_id": args.draft_id,
                                 "expected_revision": args.revision, "notes": args.notes},
                      confirm=confirm))
    return 0


def cmd_submit(args, backend):
    return _review(args, backend, "submit")


def cmd_reject(args, backend):
    return _review(args, backend, "reject")


def cmd_approve(args, backend):
    # Publishing is a deliberate act: it is confirmed here, and over HTTP it needs the
    # separate admin token the service only has when someone sets KB_ADMIN_TOKEN.
    return _review(args, backend, "approve", confirm=True)


def cmd_graph(args, backend):
    if args.traverse:
        show(backend.call("graph", {"operation": "traverse", "entity_id": args.traverse,
                                    "max_depth": args.depth, "direction": args.direction,
                                    "limit": args.limit}))
        return 0
    arguments = {"operation": "query", "limit": args.limit}
    if args.type:
        arguments["entity_type"] = args.type
    if args.relation:
        arguments["relation_type"] = args.relation
    if args.name:
        arguments["name_contains"] = args.name
    show(backend.call("graph", arguments))
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
    parser.add_argument("--collection", default=default(DEFAULT_COLLECTION), help=hide or
                        f"Collection inside the store (default {DEFAULT_COLLECTION})")
    return parser


def build_parser():
    parser = argparse.ArgumentParser(prog="kb", description=__doc__.splitlines()[0])
    _shared(parser, sub=False)
    common = _shared(argparse.ArgumentParser(add_help=False), sub=True)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, function, *, help, parents=(common,), backend=True):
        item = sub.add_parser(name, help=help, parents=list(parents))
        item.set_defaults(handler=function, needs_backend=backend)
        return item

    serve = add("serve", cmd_serve, help="Run the HTTP service", backend=False)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8931)
    serve.add_argument("--log-level", default="info")

    add("mcp", cmd_mcp, help="Run the MCP server on stdio, for an agent harness",
        backend=False)
    add("tools", cmd_tools, help="Print the agent tool manifest", backend=False)

    add("overview", cmd_overview, help="Counts, settings and available PDF engines")

    settings = add("settings", cmd_settings, help="Show or change this collection's settings")
    settings.add_argument("--collection-name")
    settings.add_argument("--pdf-engine", choices=("auto", "pymupdf4llm", "mineru", "text"))
    settings.add_argument("--mineru-base-url")

    ingest = add("ingest", cmd_ingest, help="Upload files and convert them to markdown")
    ingest.add_argument("files", nargs="+")
    ingest.add_argument("--no-wait", action="store_true",
                        help="Return as soon as the job is queued (in-process runs will "
                             "abandon it)")
    ingest.add_argument("--timeout", type=float, default=900.0)

    jobs = add("jobs", cmd_jobs, help="List conversion jobs, or follow one")
    jobs.add_argument("--job")
    jobs.add_argument("--watch", action="store_true")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument("--timeout", type=float, default=900.0)

    sources = add("sources", cmd_sources, help="List uploaded documents")
    sources.add_argument("--limit", type=int, default=50)
    sources.add_argument("--offset", type=int, default=0)

    markdown = add("markdown", cmd_markdown, help="Print the extracted markdown")
    markdown.add_argument("--source")
    markdown.add_argument("--record")
    markdown.add_argument("--limit", type=int, default=40_000)
    markdown.add_argument("--offset", type=int, default=0)
    markdown.add_argument("--out", help="Write to a file instead of stdout")

    schema = add("schema", cmd_schema, help="List, show, add or update extraction schemas")
    schema.add_argument("operation", choices=("list", "show", "add", "update"))
    schema.add_argument("--file", help="JSON with name, definition, system_prompt, ...")
    schema.add_argument("--schema-id")

    prompt = add("prompt", cmd_prompt, help="The projection prompt for a schema and document")
    project = add("project", cmd_project,
                  help="Build the prompt, call your model, save the projection")
    for item in (prompt, project):
        item.add_argument("--schema", required=True)
        item.add_argument("--source")
        item.add_argument("--record")
        item.add_argument("--limit", type=int, default=60_000)
    project.add_argument("--model", help="Model name (default $KB_MODEL)")
    project.add_argument("--model-url", help="OpenAI-compatible base URL "
                                             "(default $KB_MODEL_BASE_URL)")
    project.add_argument("--no-json-mode", action="store_true",
                         help="Skip response_format=json_object for endpoints without it")
    project.add_argument("--notes")
    project.add_argument("--dry-run", action="store_true",
                         help="Print the model's JSON without saving it")

    save = add("save-projection", cmd_save_projection, help="Save JSON you produced elsewhere")
    save.add_argument("--schema", required=True)
    save.add_argument("--record", required=True)
    save.add_argument("--file", required=True)
    save.add_argument("--model")
    save.add_argument("--notes")

    projections = add("projections", cmd_projections, help="List or show projections")
    projections.add_argument("projection_id", nargs="?")
    projections.add_argument("--record")
    projections.add_argument("--schema")
    projections.add_argument("--limit", type=int, default=50)

    draft = add("draft", cmd_draft, help="Build a graph draft from projections")
    draft.add_argument("operation", choices=("list", "show", "create"))
    draft.add_argument("--projection", action="append", default=[])
    draft.add_argument("--draft-id")
    draft.add_argument("--limit", type=int, default=50)

    for name, function, help_text in (
        ("submit", cmd_submit, "Send a draft for review"),
        ("reject", cmd_reject, "Reject a draft"),
        ("approve", cmd_approve, "Publish a draft as a fact and write it into the graph"),
    ):
        item = add(name, function, help=help_text)
        item.add_argument("draft_id")
        item.add_argument("--revision", type=int, required=True)
        item.add_argument("--notes")

    graph = add("graph", cmd_graph, help="Query the published knowledge graph")
    graph.add_argument("--traverse", help="Entity id to traverse from")
    graph.add_argument("--depth", type=int, default=1)
    graph.add_argument("--direction", choices=("both", "out", "in"), default="both")
    graph.add_argument("--type")
    graph.add_argument("--relation")
    graph.add_argument("--name")
    graph.add_argument("--limit", type=int, default=200)
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
