"""``kb``: drive the knowledge base from a terminal, with or without a service.

Every command resolves to one operation from :mod:`oaw_knowledge_base.operations`, so
the CLI, the HTTP service, MCP and the OAW card all execute the same code. Two modes:

* **In process** (default): open the store here. Refuses a store a running service
  holds, because two MKB job threads on one SQLite file race the same job rows.
* **Against a service** (``--service URL`` or ``KB_SERVICE_URL``): post the same
  arguments over HTTP.

``kb knowledge extract``, ``kb records extract`` and ``kb insights extract`` are what
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
    arguments = {"all_groups": args.all_groups}
    if args.group:
        arguments["group_id"] = args.group
    show(backend.call("overview", arguments))
    return 0


def cmd_settings(args, backend):
    patch = {key: value for key, value in (
        ("collection_name", args.collection_name), ("pdf_engine", args.pdf_engine),
        ("mineru_base_url", args.mineru_base_url)) if value is not None}
    show(backend.call("settings", patch))
    return 0


def cmd_ingest(args, backend):
    uploaded = []
    for name in args.files:
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CliError(f"Could not read {path}: {error}") from None
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        arguments = {"filename": path.name, "media_type": media_type,
                    "content_base64": base64.b64encode(data).decode("ascii")}
        if args.group:
            arguments["group_id"] = args.group
        result = backend.call("ingest", arguments)
        uploaded.append(result)
        print(f"{path.name}: source {result['source']['id']}", file=sys.stderr)
    # A batch upload converts as one batch too, so the promise "ingest converts" holds
    # for the CLI even though the plugin's own ingest action no longer does it alone.
    processed = backend.call("process", {
        "source_ids": [item["source"]["id"] for item in uploaded]})
    jobs_by_source = {item["source_id"]: item.get("job") for item in processed["jobs"]}
    for item in uploaded:
        item["job"] = jobs_by_source.get(item["source"]["id"])
        if item["job"]:
            print(f"{item['source']['filename']}: job {item['job']['id']}", file=sys.stderr)
    if not backend.remote and not args.no_wait:
        for item in uploaded:
            if item["job"]:
                _wait(backend, item["job"]["id"], args.timeout)
    show({"ingested": uploaded})
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


def cmd_process(args, backend):
    arguments = {"source_ids": args.source or []}
    if args.group:
        arguments["group_id"] = args.group
    show(backend.call("process", arguments))
    return 0


def cmd_groups(args, backend):
    if args.operation == "list":
        show(backend.call("groups", {"operation": "list"}))
        return 0
    if args.operation == "create":
        if not args.name:
            raise CliError("Pass --name to create a group")
        show(backend.call("groups", {"operation": "create", "name": args.name}))
        return 0
    if not args.group_id:
        raise CliError(f"Pass the group id to {args.operation}")
    if args.operation == "rename":
        if not args.name:
            raise CliError("Pass --name to rename a group")
        show(backend.call("groups", {"operation": "rename", "group_id": args.group_id,
                                     "name": args.name}))
        return 0
    show(backend.call("groups", {"operation": "delete", "group_id": args.group_id}))
    return 0


def cmd_sources(args, backend):
    arguments = {"limit": args.limit, "offset": args.offset, "all_groups": args.all_groups}
    if args.group:
        arguments["group_id"] = args.group
    show(backend.call("sources", arguments))
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


def cmd_knowledge(args, backend):
    """``kb knowledge`` — list, show, open, extract, publish, unpublish, delete drafts."""
    operation = args.operation
    if operation == "list":
        arguments = {"operation": "list", "limit": args.limit, "all_groups": args.all_groups}
        for key, value in (("group_id", args.group), ("status", args.status)):
            if value:
                arguments[key] = value
        show(backend.call("knowledge_drafts", arguments))
        return 0
    if operation == "open":
        if not args.source:
            raise CliError("Pass at least one --source id to open a draft over")
        arguments = {"operation": "create", "source_ids": args.source}
        if args.title:
            arguments["title"] = args.title
        if args.group:
            arguments["group_id"] = args.group
        show(backend.call("knowledge_draft_manage", arguments))
        return 0
    if not args.draft_id:
        raise CliError(f"Pass the draft id to {operation}")
    if operation == "show":
        show(backend.call("knowledge_drafts", {"operation": "get", "draft_id": args.draft_id}))
        return 0
    if operation == "delete":
        show(backend.call("knowledge_draft_manage", {"operation": "delete", "draft_id": args.draft_id}))
        return 0
    if operation == "extract":
        return _extract_knowledge(args, backend)
    # Publishing is a deliberate act: confirmed here, and over HTTP it needs the
    # separate admin token the service only has when someone sets KB_ADMIN_TOKEN.
    show(backend.call("knowledge_review", {"operation": operation, "draft_id": args.draft_id},
                      confirm=operation == "publish"))
    return 0


def _extract_knowledge(args, backend):
    from .. import knowledge_prompt

    draft = backend.call("knowledge_drafts", {"operation": "get", "draft_id": args.draft_id})["draft"]
    pending = [item["source_id"] for item in draft["sources"]
               if args.replace or item["extracted_at"] is None]
    if not pending:
        raise CliError("Every source of this draft is extracted already; pass --replace to redo them")
    results = []
    for index, source_id in enumerate(pending):
        prompt, text = _paged(backend, "knowledge_extract_prompt",
                              {"source_id": source_id, "draft_id": args.draft_id})
        answer = _complete(knowledge_prompt.build_messages(prompt, markdown=text), **_model_options(args))
        entities, relations = knowledge_prompt.parse_graph(answer)
        if args.dry_run:
            results.append({"source_id": source_id, "entities": entities, "relations": relations})
            continue
        saved = backend.call("knowledge_save", {
            "draft_id": args.draft_id, "source_id": source_id, "entities": entities,
            "relations": relations, "replace": args.replace and index == 0})
        results.append({"source_id": source_id, **saved["added"]})
    show({"draft_id": args.draft_id, "sources": results})
    return 0


def cmd_records(args, backend):
    """``kb records`` — list, show, extract, publish, unpublish, delete structured records."""
    operation = args.operation
    if operation == "list":
        arguments = {"operation": "list", "limit": args.limit, "all_groups": args.all_groups}
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

    if not (args.schema and args.source):
        raise CliError("Pass --schema and --source to extract records from a source")
    prompt, text = _paged(backend, "record_extract_prompt",
                          {"schema_id": args.schema, "source_id": args.source})
    answer = _complete(experiment_prompt.build_messages(prompt, markdown=text), **_model_options(args))
    records = [{"name": item["name"], "data": item["data"], "note": item["note"]}
               for item in experiment_prompt.parse_extract(answer, allow_empty=True)]
    if args.dry_run:
        show({"records": records})
        return 0
    show(backend.call("record_save", {"schema_id": args.schema, "source_id": args.source,
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
    scope = {"schema_ids": args.schema or [], "all_groups": args.all_groups,
             "schema_kind": args.schema_kind}
    if args.group:
        scope["group_id"] = args.group
    return scope


def cmd_insights(args, backend):
    """``kb insights`` — list, show, stats, extract, publish, unpublish, delete."""
    operation = args.operation
    if operation == "list":
        arguments = {"operation": "list", "limit": args.limit, "all_groups": args.all_groups,
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

    overview = add("overview", cmd_overview, help="Counts, settings and available PDF engines")
    overview.add_argument("--group", help="Narrow to one group (collection id)")
    overview.add_argument("--all-groups", dest="all_groups", action="store_true",
                          help="Total across every group instead of just the default one")

    settings = add("settings", cmd_settings, help="Show or change this collection's settings")
    settings.add_argument("--collection-name")
    settings.add_argument("--pdf-engine", choices=("auto", "pymupdf4llm", "mineru", "text"))
    settings.add_argument("--mineru-base-url")

    ingest = add("ingest", cmd_ingest, help="Upload files and convert them to markdown")
    ingest.add_argument("files", nargs="+")
    ingest.add_argument("--group", help="Group (collection id) to file the sources into")
    ingest.add_argument("--no-wait", action="store_true",
                        help="Return as soon as the job is queued (in-process runs will "
                             "abandon it)")
    ingest.add_argument("--timeout", type=float, default=900.0)

    process = add("process", cmd_process, help="Convert uploaded sources to markdown in a batch")
    process.add_argument("source", nargs="*", help="Source ids; omit to process every "
                         "unconverted source")
    process.add_argument("--group", help="Narrow to sources in this group when no ids are given")

    groups = add("groups", cmd_groups, help="List, create, rename or delete groups")
    groups.add_argument("operation", choices=("list", "create", "rename", "delete"))
    groups.add_argument("group_id", nargs="?")
    groups.add_argument("--name")

    jobs = add("jobs", cmd_jobs, help="List conversion jobs, or follow one")
    jobs.add_argument("--job")
    jobs.add_argument("--watch", action="store_true")
    jobs.add_argument("--limit", type=int, default=20)
    jobs.add_argument("--timeout", type=float, default=900.0)
    jobs.add_argument("--group", help="Narrow to one group (collection id)")

    sources = add("sources", cmd_sources, help="List uploaded documents")
    sources.add_argument("--limit", type=int, default=50)
    sources.add_argument("--offset", type=int, default=0)
    sources.add_argument("--group", help="Narrow to one group (collection id)")
    sources.add_argument("--all-groups", dest="all_groups", action="store_true",
                         help="List across every group instead of just the default one")

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

    def model_flags(item, verb):
        item.add_argument("--model", help=f"{verb}: model name (default $KB_MODEL)")
        item.add_argument("--model-url", help=f"{verb}: OpenAI-compatible base URL (default $KB_MODEL_BASE_URL)")
        item.add_argument("--no-json-mode", action="store_true",
                          help=f"{verb}: skip response_format=json_object for endpoints without it")
        item.add_argument("--dry-run", action="store_true",
                          help=f"{verb}: print the model's JSON without saving it")

    knowledge = add("knowledge", cmd_knowledge,
                    help="Knowledge drafts — the concept graph read from sources: list, show, "
                         "open, extract, publish, unpublish, delete")
    knowledge.add_argument("operation", choices=("list", "show", "open", "extract", "publish",
                                                 "unpublish", "delete"))
    knowledge.add_argument("draft_id", nargs="?", help="Draft id for show/extract/publish/unpublish/delete")
    knowledge.add_argument("--source", action="append", help="open: a converted source id; repeat for several")
    knowledge.add_argument("--title", help="open: the draft's title (defaults to the first file's name)")
    knowledge.add_argument("--status", choices=("draft", "published"))
    knowledge.add_argument("--limit", type=int, default=50)
    knowledge.add_argument("--group", help="Narrow to one group (collection id)")
    knowledge.add_argument("--all-groups", dest="all_groups", action="store_true",
                           help="Across every group instead of just the default one")
    knowledge.add_argument("--replace", action="store_true",
                           help="extract: empty the draft and extract every source again")
    model_flags(knowledge, "extract")

    records = add("records", cmd_records,
                  help="Structured records read from a source with a schema: list, show, "
                       "extract, publish, unpublish, delete")
    records.add_argument("operation", choices=("list", "show", "extract", "publish", "unpublish", "delete"))
    records.add_argument("record_id", nargs="?", help="Record id for show/publish/unpublish/delete")
    records.add_argument("--schema", help="Schema id (required to extract)")
    records.add_argument("--source", help="Source id (required to extract)")
    records.add_argument("--status", choices=("draft", "published"))
    records.add_argument("--limit", type=int, default=50)
    records.add_argument("--group", help="Narrow to one group (collection id)")
    records.add_argument("--all-groups", dest="all_groups", action="store_true",
                         help="Across every group instead of just the default one")
    records.add_argument("--replace", action="store_true",
                         help="extract: replace this source's earlier draft records for the schema")
    model_flags(records, "extract")

    graph = add("graph", cmd_graph, help="Query the published knowledge graph")
    graph.add_argument("--traverse", help="Entity id to traverse from")
    graph.add_argument("--depth", type=int, default=1)
    graph.add_argument("--direction", choices=("both", "out", "in"), default="both")
    graph.add_argument("--type")
    graph.add_argument("--relation")
    graph.add_argument("--name")
    graph.add_argument("--limit", type=int, default=200)
    graph.add_argument("--group", help="Narrow to entities/relations tagged with one group")
    graph.add_argument("--layer", choices=("literature", "experiment"),
                       help="Only published knowledge (literature) or published insights (experiment)")

    insights = add("insights", cmd_insights,
                   help="Insights over published records: list, show, stats, "
                        "extract, publish, unpublish, delete")
    insights.add_argument("operation", choices=("list", "show", "stats", "extract", "publish",
                                                "unpublish", "delete"))
    insights.add_argument("insight_ids", nargs="*", help="Insight id(s) for show/publish/unpublish/delete")
    insights.add_argument("--schema", action="append",
                          help="Schema id; repeat for a cross-schema scope, omit for "
                               "every schema of --schema-kind with published records")
    insights.add_argument("--schema-kind", dest="schema_kind", choices=("literature", "experiment"),
                          default="experiment", help="Which schemas an open scope takes")
    insights.add_argument("--query", help="list: free text over titles, statements, fields and notes")
    insights.add_argument("--kind", choices=("anomaly", "rule", "trend", "correlation", "threshold", "note"))
    insights.add_argument("--status", choices=("draft", "published"))
    insights.add_argument("--limit", type=int, default=50)
    insights.add_argument("--group", help="Narrow to one group (collection id)")
    insights.add_argument("--all-groups", dest="all_groups", action="store_true",
                          help="Across every group instead of just the default one")
    insights.add_argument("--replace", action="store_true",
                          help="extract: replace this scope's earlier draft insights")
    insights.add_argument("--model", help="extract: model name (default $KB_MODEL)")
    insights.add_argument("--model-url", help="extract: OpenAI-compatible base URL "
                                              "(default $KB_MODEL_BASE_URL)")
    insights.add_argument("--no-json-mode", action="store_true",
                          help="extract: skip response_format=json_object")
    insights.add_argument("--dry-run", action="store_true",
                          help="extract: print the model's insights without saving them")
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
