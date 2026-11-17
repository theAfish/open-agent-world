"""Human-friendly card commands with an explicit JSON interface for agents."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote

from .client import Client, CliError, choose_instance, resolve_one


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def version():
    from backend import __version__
    return __version__


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("invalid_arguments", message, hint=f"Use {self.prog} --help.", exit_code=2)


def positive(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use a positive number of seconds") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("use a positive number of seconds")
    return number


def coordinate(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use a finite coordinate") from None
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("use a finite coordinate")
    return number


def build_parser():
    shared = Parser(add_help=False, argument_default=argparse.SUPPRESS)
    shared.add_argument("--json", action="store_true", help="Output JSON, including errors")
    shared.add_argument("--plain", action="store_true", help="Use plain text without card frames or color")
    shared.add_argument("--endpoint", metavar="URL", help="Connect to an OAW server")
    shared.add_argument("--instance", metavar="ID", help="Select a running instance")
    shared.add_argument("--timeout", type=positive, metavar="SECONDS", help="Request timeout; wait commands use it as the waiting limit")
    shared.add_argument("--fields", metavar="FIELD,...", help="Select JSON fields")
    parser = Parser(prog="oaw", parents=[shared],
                    description="Open Agent World · Your cards, connected.",
                    epilog='Start with oaw, then oaw cards. Use names or unique ID prefixes.\nExample: oaw create text --name Notes --text "Hello, world"',
                    formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="OAW " + version())
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name, help):
        return commands.add_parser(name, help=help, description=help, parents=[shared])

    cards = command("cards", "List cards")
    cards.add_argument("--type", help="Filter by card type")
    cards.add_argument("--search", help="Filter by name")
    cards.add_argument("--limit", type=int, default=30, help="Maximum cards (default: 30; 0: all)")
    show = command("show", "Open a card's details and connections")
    show.add_argument("card", metavar="CARD")
    create = command("create", "Create a card")
    create.add_argument("type", metavar="TYPE", help="Use oaw types to see available cards")
    create.add_argument("name_argument", nargs="?", metavar="NAME", help="Card name (defaults to its type)")
    create.add_argument("--name")
    create.add_argument("--input", metavar="FILE", help="Card fields as JSON; - reads stdin")
    create.add_argument("--at", nargs=2, type=coordinate, metavar=("X", "Y"), help="Canvas position")
    text = create.add_mutually_exclusive_group()
    text.add_argument("--text", help="Initial text card content")
    text.add_argument("--text-file", metavar="FILE", help="UTF-8 content; - reads stdin")
    edit = command("edit", "Rename a card, change settings, or replace text")
    edit.add_argument("card", metavar="CARD")
    edit.add_argument("--name")
    edit.add_argument("--input", metavar="FILE", help="Card changes as JSON; - reads stdin")
    edit.add_argument("--revision", type=int, help="Expected card revision, or text revision with --text")
    text = edit.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", metavar="FILE")
    remove = command("remove", "Delete a card")
    remove.add_argument("card", metavar="CARD")
    remove.add_argument("--yes", action="store_true", help="Confirm deletion without a prompt")
    connect = command("connect", "Connect two cards")
    connect.add_argument("source", metavar="SOURCE")
    connect.add_argument("target", metavar="TARGET")
    connect.add_argument("relation", nargs="?", metavar="RELATION", help="For example read or execute_manage")
    connect.add_argument("--relationship", metavar="RELATION", help="Permission or relationship; oaw schema TYPE lists choices")
    connect.add_argument("--direction", choices=("forward", "bidirectional"), default="forward")
    command("connections", "List card connections")
    disconnect = command("disconnect", "Remove a connection")
    disconnect.add_argument("edge", metavar="CONNECTION")
    disconnect.add_argument("--yes", action="store_true")
    types = command("types", "Browse card types by group")
    types.add_argument("group", nargs="?", metavar="GROUP", help="Card group ID or name")
    types.add_argument("--all", action="store_true", help="Show every available card type")
    schema = command("schema", "Show a card type's settings and relationships")
    schema.add_argument("type", metavar="TYPE")
    run = command("run", "Send a task to an Agent card")
    run.add_argument("agent", metavar="AGENT")
    run.add_argument("prompt", nargs="?", metavar="PROMPT")
    run.add_argument("--prompt-file", metavar="FILE", help="UTF-8 task; - reads stdin")
    run.add_argument("--wait", action="store_true", help="Wait and print the result")
    run.add_argument("--fresh-context", action="store_true", help="Use a new Agent context")
    chat = command("chat", "Talk to an Agent or a shared Conversation")
    chat.add_argument("card", metavar="CARD")
    chat.add_argument("message", nargs="?", metavar="MESSAGE", help="Omit to chat interactively")
    chat.add_argument("--message-file", metavar="FILE", help="UTF-8 message; - reads stdin")
    chat.add_argument("--session", metavar="SESSION", help="Use an existing conversation session")
    chat.add_argument("--agent", metavar="AGENT", help="Choose a participant")
    chat.add_argument("--new", action="store_true", help="Start a new session")
    chat.add_argument("--history", action="store_true", help="Read conversation history without sending")
    command("models", "Check available models and the default")
    command("start", "Open OAW using its normal application data")
    runs = command("runs", "List recent runs")
    runs.add_argument("--agent", metavar="CARD")
    runs.add_argument("--limit", type=int, default=20)
    for name, help in (("result", "Read a run's result"), ("wait", "Wait for a run to finish"),
                       ("cancel", "Cancel a run")):
        item = command(name, help)
        item.add_argument("run", metavar="RUN")
    document = command("document", "Read a card's document")
    document.add_argument("card", metavar="CARD")
    action = command("action", "Invoke a plugin document action")
    action.add_argument("card", metavar="CARD")
    action.add_argument("action", metavar="ACTION")
    action.add_argument("--input", metavar="FILE", help="Action arguments as JSON; - reads stdin")
    action.add_argument("--revision", type=int)
    command("status", "Show the connected OAW instance")
    command("doctor", "Check the CLI and server connection")
    command("instances", "List running OAW instances")
    pack = command("pack", "Build and inspect card packs")
    pack.add_argument("arguments", nargs=argparse.REMAINDER, help="Use oaw pack --help")
    return parser


def read_text(filename):
    try:
        if filename == "-":
            if hasattr(sys.stdin, "buffer"):
                return sys.stdin.buffer.read().decode("utf-8-sig")
            return sys.stdin.read()
        return Path(filename).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CliError("invalid_input", f"Cannot read UTF-8 file '{filename}'.", exit_code=2) from exc


def read_input(filename):
    if filename is None:
        return {}
    try:
        def reject_constant(value):
            raise ValueError(f"{value} is not a JSON value")
        data = json.loads(read_text(filename), parse_constant=reject_constant)
    except ValueError as exc:
        raise CliError("invalid_json", f"Invalid JSON in '{filename}': {exc}.", exit_code=2) from exc
    if not isinstance(data, dict):
        raise CliError("invalid_input", "The input must be a JSON object.", exit_code=2)
    return data


def text_input(args):
    if args.text_file:
        if args.text_file == "-" and args.input == "-":
            raise CliError("invalid_arguments", "Only one option can read stdin.", exit_code=2)
        return read_text(args.text_file)
    return args.text


def confirm(args, label):
    if args.yes:
        return
    if args.json or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise CliError("confirmation_required", f"Confirm removal of {label} with --yes.", exit_code=2)
    from .render import clean
    reply = input(f"Remove {clean(label)}? [y/N] ")
    if reply.casefold() not in {"y", "yes"}:
        raise CliError("cancelled", "Removal cancelled.", exit_code=130)


def matching_relationships(client, source, target):
    types = {item["id"]: item for item in client.catalog()["node_types"]}

    def endpoint(card, definition, prefix):
        return (card["type"] in definition.get(prefix + "_types", [])
                or bool(set(types[card["type"]].get("traits", [])) & set(definition.get(prefix + "_traits", []))))

    return [item for item in client.catalog()["relationships"] if
            endpoint(source, item, "source") and endpoint(target, item, "target") or
            endpoint(target, item, "source") and endpoint(source, item, "target")]


def wait_result(client, record, timeout):
    deadline = time.monotonic() + timeout
    try:
        while record["status"] not in TERMINAL:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CliError("wait_timeout", "The run is still active; waiting stopped.", exit_code=6,
                               run_id=record["run_id"], status=record["status"],
                               hint=f"Use oaw result {record['run_id']} or oaw wait {record['run_id']}.")
            time.sleep(min(.5, remaining))
            client.timeout = min(30, max(.01, deadline - time.monotonic()))
            record = client.request("GET", "/runs/" + quote(record["run_id"], safe=""))
        return client.request("GET", "/runs/" + quote(record["run_id"], safe="") + "/result")
    except CliError as exc:
        exc.error.setdefault("run_id", record["run_id"])
        if exc.error["code"] != "wait_timeout":
            exc.error["hint"] = f"Check the submitted run with oaw result {record['run_id']} before submitting again."
        raise
    except KeyboardInterrupt:
        raise CliError("interrupted", "Waiting stopped; the run remains active.", exit_code=130,
                       run_id=record["run_id"], hint=f"Use oaw result {record['run_id']}.") from None


def execute(args):
    from open_agent_world.instances import list_instances
    if args.command == "instances":
        return list_instances(), "instances", 0
    if args.command == "start":
        if args.json:
            raise CliError("invalid_arguments", "oaw start opens the application; use oaw status --json to inspect it.", exit_code=2)
        production = [item for item in list_instances() if item.get("mode") == "production"]
        if production:
            instance = choose_instance(args.endpoint, args.instance)
            client = Client(instance, timeout=args.timeout or 30)
            client.verify()
            return {**instance, "connected": True}, "status", 0
        root = Path(__file__).resolve().parents[2]
        frontend = root / "frontend" / "dist"
        if not (frontend / "index.html").is_file():
            raise CliError("application_not_ready", "The OAW application files are missing.", exit_code=3,
                           hint="Install OAW, or run the source setup once before oaw start.")
        from importlib.util import find_spec
        backend_python = root / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = str(backend_python) if backend_python.is_file() else sys.executable
        if python == sys.executable and find_spec("fastapi") is None:
            raise CliError("application_not_ready", "The OAW backend environment is not installed.", exit_code=3,
                           hint="Run the OAW source setup or use the desktop installation.")
        code = subprocess.call([python, "-B", "-m", "backend.launcher", "--mode", "production",
                                "--open", "--frontend", str(frontend)], cwd=root)
        return None, "status", code
    try:
        instance = choose_instance(args.endpoint, args.instance)
    except CliError as exc:
        if exc.error["code"] == "not_connected" and args.command in {None, "doctor"}:
            if args.command == "doctor":
                return {"cli_version": version(), "python": sys.version.split()[0], "connected": False,
                        "hint": exc.error["hint"]}, "status", 3
            return {"instance": None, "cards": [], "edges": [], "catalog": {}, "runs": []}, "overview", 0
        raise
    waiting = args.command in {"wait", "chat"} or args.command == "run" and args.wait
    client = Client(instance, timeout=30 if waiting else args.timeout or 30)
    client.verify()
    if args.command in {"status", "doctor"}:
        client.request("GET", "/health")
        data = {**instance, "connected": True}
        if args.command == "doctor":
            data.update(cli_version=version(), python=sys.version.split()[0])
        return data, "status", 0
    if args.command is None:
        world = client.request("GET", "/world")
        names = {card["id"]: card["name"] for card in world["nodes"]}
        runs = [{**run, "agent_name": names.get(run["agent_id"], run["agent_id"])}
                for run in client.request("GET", "/runs", limit=5)]
        return {"instance": instance, "cards": world["nodes"], "edges": world["edges"],
                "catalog": client.catalog(), "runs": runs}, "overview", 0
    if args.command == "types":
        catalog = client.catalog()
        if args.group:
            group = args.group.casefold()
            items = [item for item in catalog["node_types"] if group in {
                item.get("deck_id", "").casefold(), item.get("deck_label", "").casefold(),
                item.get("plugin_id", "").casefold()}]
            if group in {"basic", "core"}:
                items = [item for item in catalog["node_types"] if item["id"] in {"agent", "conversation", "text", "image", "sandbox"}]
            if not items:
                groups = {item["deck_id"]: item.get("deck_label", item["deck_id"]) for item in catalog["node_types"]}
                raise CliError("not_found", f"No card group matches '{args.group}'.", exit_code=2,
                               hint="Use oaw types to see the groups.",
                               candidates=[{"id": key, "name": value} for key, value in groups.items()])
            catalog = {**catalog, "node_types": items}
        return catalog, "types" if args.json or args.all or args.group else "type_groups", 0
    if args.command == "models":
        return client.request("GET", "/settings/models"), "models", 0
    if args.command == "chat":
        from .chat import handle_chat
        return handle_chat(client, args)
    if args.command == "schema":
        definition = client.card_type(args.type)
        relations = [item for item in client.catalog()["relationships"] if definition["id"] in
                     item.get("source_types", []) + item.get("target_types", []) or
                     set(definition.get("traits", [])) & set(item.get("source_traits", []) + item.get("target_traits", []))]
        return {"id": definition["id"], "label": definition["label"], "config_schema": definition["config_schema"],
                "default_config": definition["default_config"], "relationships": relations}, "schema", 0
    if args.command == "cards":
        cards = client.cards()
        if args.type:
            selected = client.card_type(args.type)["id"]
            cards = [card for card in cards if card["type"] == selected]
        if args.search:
            cards = [card for card in cards if args.search.casefold() in card["name"].casefold()]
        if args.limit < 0:
            raise CliError("invalid_arguments", "--limit must be zero or greater.", exit_code=2)
        total = len(cards)
        cards = cards[:args.limit] if args.limit else cards
        return {"items": [{key: card[key] for key in ("id", "name", "type", "status", "revision")} for card in cards],
                "total": total}, "cards", 0
    if args.command in {"show", "edit", "remove", "document", "action"}:
        card = client.card(args.card)
        node = "/nodes/" + quote(card["id"], safe="")
        if args.command == "show":
            edges = [edge for edge in client.request("GET", "/edges") if card["id"] in (edge["source"], edge["target"])]
            names = {item["id"]: item["name"] for item in client.cards()}
            edges = [{**edge, "source_name": names.get(edge["source"], edge["source"]),
                      "target_name": names.get(edge["target"], edge["target"])} for edge in edges]
            data = {"card": client.request("GET", node), "connections": edges}
            definition = next((item for item in client.catalog()["node_types"] if item["id"] == card["type"]), {})
            if "core.text" in definition.get("traits", []):
                data["text"] = client.request("GET", "/resources/" + quote(card["id"], safe="") + "/text")
            elif definition.get("has_document"):
                data["document"] = client.request("GET", node + "/document")
            return data, "card", 0
        if args.command == "remove":
            confirm(args, card["name"])
            return client.request("DELETE", node, expected_revision=card["revision"]), "removed", 0
        if args.command == "document":
            return client.request("GET", node + "/document"), "value", 0
        if args.command == "action":
            arguments = read_input(args.input)
            revision = args.revision
            if revision is None:
                revision = client.request("GET", node + "/document")["revision"]
            return client.request("POST", node + "/actions/" + quote(args.action, safe=""),
                                  {"arguments": arguments, "expected_revision": revision}), "value", 0
        content = text_input(args)
        data = read_input(args.input)
        if content is not None:
            if data or args.name:
                raise CliError("invalid_arguments", "Edit text separately from card settings.", exit_code=2)
            path = "/resources/" + quote(card["id"], safe="") + "/text"
            revision = args.revision
            if revision is None:
                revision = client.request("GET", path)["revision"]
            return client.request("PUT", path, {"content": content, "expected_revision": revision}), "value", 0
        if args.name is not None:
            data["name"] = args.name
        if not data:
            raise CliError("invalid_arguments", "Provide --name, --text, --text-file, or --input.", exit_code=2)
        data.setdefault("expected_revision", args.revision if args.revision is not None else card["revision"])
        return client.request("PATCH", node, data), "card", 0
    if args.command == "create":
        definition = client.card_type(args.type)
        content = text_input(args)
        data = read_input(args.input)
        data["type"] = definition["id"]
        if args.name is not None and args.name_argument is not None and args.name != args.name_argument:
            raise CliError("invalid_arguments", "Provide the card name once: NAME or --name.", exit_code=2)
        name = args.name if args.name is not None else args.name_argument
        if name is not None:
            data["name"] = name
        if "name" not in data:
            base = definition["id"].rsplit(".", 1)[-1]
            names = {card["name"].casefold() for card in client.cards()}
            name, suffix = base, 2
            while name.casefold() in names:
                name = f"{base}-{suffix}"
                suffix += 1
            data["name"] = name
        if content is not None:
            data["content"] = content
        if args.at:
            data["position"] = dict(zip(("x", "y"), args.at))
        if "position" not in data:
            cards = [card for card in client.cards() if not card.get("parent_id") and not card.get("equipment")]
            data["position"] = {"x": max((card["position"]["x"] + max(400, card["size"]["width"]) for card in cards), default=0), "y": 0}
        return client.request("POST", "/nodes", data), "card", 0
    if args.command == "connections":
        cards = [{key: card[key] for key in ("id", "name")} for card in client.cards()]
        return {"edges": client.request("GET", "/edges"), "cards": cards}, "connections", 0
    if args.command == "connect":
        source, target = client.card(args.source), client.card(args.target)
        relations = matching_relationships(client, source, target)
        if args.relation and args.relationship and args.relation != args.relationship:
            raise CliError("invalid_arguments", "Provide the relationship once: RELATION or --relationship.", exit_code=2)
        relationship = args.relation or args.relationship
        if relationship:
            exact = [item for item in relations if item["id"] == relationship]
            matches = exact or [item for item in relations if relationship.casefold() in
                               {item["label"].casefold(), item["short_label"].casefold()}]
        else:
            matches = relations
        if not relationship and len(matches) > 1 and not args.json and sys.stdin.isatty() and sys.stdout.isatty():
            from .render import Renderer
            renderer = Renderer()
            choices = [{"id": item["id"], "name": item["label"]} for item in matches]
            renderer.candidates(choices)
            reply = input("Relationship (number or ID): ").strip()
            if reply.isdecimal() and 1 <= int(reply) <= len(matches):
                matches = [matches[int(reply) - 1]]
            else:
                matches = [item for item in matches if reply == item["id"]]
        if len(matches) != 1:
            if not relations:
                raise CliError("invalid_relationship", "These card types cannot connect.", exit_code=2)
            message = f"Relationship '{relationship}' is not available for these cards." if relationship else "Choose how these cards connect."
            example = relations[0]["id"]
            raise CliError("relationship_required", message, exit_code=2,
                           hint=f'oaw connect "{source["name"]}" "{target["name"]}" {example}',
                           candidates=[{"id": item["id"], "name": item["label"]} for item in relations])
        edge = client.request("POST", "/edges", {"source": source["id"], "target": target["id"],
                              "relationship": matches[0]["id"], "direction": args.direction})
        names = {source["id"]: source["name"], target["id"]: target["name"]}
        return {**edge, "source_name": names.get(edge["source"], edge["source"]),
                "target_name": names.get(edge["target"], edge["target"])}, "connected", 0
    if args.command == "disconnect":
        edges = client.request("GET", "/edges")
        exact = [edge for edge in edges if edge["id"] == args.edge]
        edge = resolve_one(exact or [edge for edge in edges if edge["id"].startswith(args.edge)], "connection", args.edge)
        confirm(args, edge["id"])
        return client.request("DELETE", "/edges/" + quote(edge["id"], safe=""), expected_revision=edge["revision"]), "disconnected", 0
    if args.command == "run":
        if (args.prompt is None) == (args.prompt_file is None):
            raise CliError("invalid_arguments", "Provide a prompt or --prompt-file.", exit_code=2)
        prompt = read_text(args.prompt_file) if args.prompt_file else args.prompt
        if not prompt.strip():
            raise CliError("invalid_arguments", "The task cannot be empty.", exit_code=2)
        card = client.card(args.agent)
        traits = client.definition(card).get("traits", [])
        if "core.conversation" in traits or card["type"] == "conversation":
            from .chat import handle_chat
            client.timeout = 30
            return handle_chat(client, SimpleNamespace(card=card["id"], message=prompt, message_file=None,
                               session=None, agent=None, new=args.fresh_context, history=False,
                               timeout=args.timeout, json=args.json, plain=args.plain))
        if "core.agent" not in traits:
            raise CliError("invalid_card_type", f"'{card['name']}' is a {card['type']} card.", exit_code=2,
                           hint="Use an Agent or Conversation card to send a message.")
        accepted = client.request("POST", "/agents/" + quote(card["id"], safe="") + "/run",
                                  {"prompt": prompt, "fresh_context": args.fresh_context})
        if not args.wait:
            return {**accepted, "agent_name": card["name"]}, "submitted", 0
        waiting_notice(args, f"Running {card['name']}  {accepted['run_id']}")
        record = {"run_id": accepted["run_id"], "status": "created"}
        data = wait_result(client, record, args.timeout or 300)
        return data, "result", 0 if data["status"] == "succeeded" else 7
    if args.command == "runs":
        if not 1 <= args.limit <= 200:
            raise CliError("invalid_arguments", "--limit must be between 1 and 200.", exit_code=2)
        agent_id = client.card(args.agent)["id"] if args.agent else None
        names = {card["id"]: card["name"] for card in client.cards()}
        return [{**run, "agent_name": names.get(run["agent_id"], run["agent_id"])}
                for run in client.request("GET", "/runs", limit=args.limit, agent_id=agent_id)], "runs", 0
    if args.command in {"result", "wait", "cancel"}:
        record = client.run(args.run)
        path = "/runs/" + quote(record["run_id"], safe="")
        if args.command == "cancel":
            return client.request("POST", path + "/cancel"), "result", 0
        if args.command == "wait":
            waiting_notice(args, f"Waiting for {record['run_id']}")
            data = wait_result(client, record, args.timeout or 300)
            return data, "result", 0 if data["status"] == "succeeded" else 7
        return client.request("GET", path + "/result"), "result", 0
    raise CliError("invalid_arguments", "Unknown command.", exit_code=2)


def select_fields(data, names):
    fields = names.split(",")
    if isinstance(data, list):
        return [select_fields(item, names) for item in data]
    if isinstance(data, dict) and "items" in data:
        return {**data, "items": select_fields(data["items"], names)}
    if isinstance(data, dict):
        return {field: data[field] for field in fields if field in data}
    return data


def waiting_notice(args, message):
    if not args.json and sys.stderr.isatty():
        from .render import Renderer
        Renderer(stream=sys.stderr, plain=args.plain).value(message)


def emit(data, kind, args):
    if data is None:
        return
    if args.json:
        print(json.dumps(select_fields(data, args.fields) if args.fields else data, ensure_ascii=False, allow_nan=False))
        return
    from .render import Renderer
    renderer = Renderer(plain=args.plain)
    if kind == "cards":
        renderer.cards(data["items"])
        if len(data["items"]) < data["total"]:
            print(f"Showing {len(data['items'])} of {data['total']}. Use --limit 0 for all cards.")
    elif kind == "card":
        if "card" in data:
            card = data["card"]
            if "text" in data and card.get("resource"):
                card = {**card, "resource": {key: value for key, value in card["resource"].items() if key != "preview"}}
            renderer.card(card, connections=data["connections"])
            if "text" in data:
                renderer.value(data["text"]["content"])
            elif "document" in data:
                renderer.value(data["document"]["value"])
        else:
            renderer.card(data)
    elif kind == "connections":
        renderer.connections(data["edges"], data["cards"])
    elif kind == "removed":
        renderer.value(f"Removed {data['name']}.")
    elif kind == "disconnected":
        renderer.value(f"Disconnected {data['id']}.")
    else:
        getattr(renderer, kind)(data)


def main(argv=None):
    # Pipes carry UTF-8 consistently across Windows shells and external agents.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    try:
        # The Pack developer CLI retains its own local/offline arguments.
        if argv and argv[0] == "pack":
            if json_mode:
                raise CliError("invalid_arguments", "Pack commands use their own output format.",
                               hint="Use oaw pack --help.", exit_code=2)
            return run_pack(argv[1:])
        args = build_parser().parse_args(argv, namespace=argparse.Namespace(
            json=False, plain=False, endpoint=None, instance=None, timeout=None, fields=None))
        if args.command == "pack":
            if args.json:
                raise CliError("invalid_arguments", "Pack commands use their own output format.",
                               hint="Use oaw pack --help.", exit_code=2)
            return run_pack(args.arguments)
        if args.fields and not args.json:
            raise CliError("invalid_arguments", "--fields requires --json.", exit_code=2)
        data, kind, exit_code = execute(args)
        emit(data, kind, args)
        return exit_code
    except CliError as exc:
        if json_mode:
            print(json.dumps({"error": exc.error}, ensure_ascii=False))
        else:
            from .render import Renderer
            renderer = Renderer(stream=sys.stderr, plain="--plain" in argv)
            renderer.error(exc.error["message"], exc.error.get("hint"))
            if exc.error.get("candidates"):
                renderer.candidates(exc.error["candidates"])
        return exc.exit_code
    except KeyboardInterrupt:
        if json_mode:
            print(json.dumps({"error": {"code": "interrupted", "message": "Client interrupted; submitted runs remain active."}}))
        else:
            from .render import Renderer
            Renderer(stream=sys.stderr, plain="--plain" in argv).value("Interrupted; submitted runs remain active.")
        return 130
    except BrokenPipeError:
        return 0


def run_pack(arguments):
    try:
        from open_agent_world.pack import main as pack_main
    except ModuleNotFoundError as exc:
        raise CliError("pack_environment_unavailable", "Pack commands need the OAW Python environment.",
                       hint="Use the oaw launcher, or run scripts/setup first.", exit_code=3) from exc
    previous = sys.argv
    try:
        sys.argv = ["oaw pack", *arguments]
        pack_main()
        return 0
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
