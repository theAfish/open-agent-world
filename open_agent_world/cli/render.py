"""Small terminal views for OAW. Machine JSON never passes through this module."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata


_ESCAPE = re.compile(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|.)")


def clean(value, *, multiline=False):
    """Render untrusted names and tool output without terminal instructions."""
    text = _ESCAPE.sub("", str(value if value is not None else ""))
    result = []
    for char in text:
        if char == "\n":
            result.append("\n" if multiline else " ")
        elif char == "\t":
            result.append(" ")
        elif not unicodedata.category(char).startswith("C"):
            result.append(char)
    return "".join(result)


def display_width(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
               for char in text)


def _clip(text, width, suffix="…"):
    text = clean(text)
    if display_width(text) <= width:
        return text
    available = max(0, width - display_width(suffix))
    result = ""
    for char in text:
        if display_width(result + char) > available:
            break
        result += char
    return result + (suffix if width else "")


def _pad(text, width):
    return text + " " * max(0, width - display_width(text))


def _wrap(text, width):
    for paragraph in clean(text, multiline=True).split("\n"):
        line = ""
        for char in paragraph:
            if line and display_width(line + char) > width:
                yield line
                line = ""
            line += char
        yield line


def short_ids(items, key="id"):
    """Keep displayed IDs usable as unambiguous CLI selectors."""
    identities = [str(item.get(key, "")) for item in items]
    result = {}
    for identity in identities:
        length = min(8, len(identity))
        while length < len(identity) and any(other != identity and other.startswith(identity[:length]) for other in identities):
            length += 1
        result[identity] = identity[:length]
    return result


class Renderer:
    def __init__(self, stream=None, plain=False, color=None, width=None):
        self.stream = stream if stream is not None else sys.stdout
        tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.plain = plain or not tty or os.environ.get("TERM") == "dumb"
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        try:
            "╭─╮│╰╯…".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            self.plain = True
        self.color = (tty if color is None else color) and not self.plain and "NO_COLOR" not in os.environ
        self.width = max(8, int(width or shutil.get_terminal_size((88, 24)).columns))

    def _style(self, text, code):
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def _write(self, text=""):
        self.stream.write(text + "\n")

    def _line(self, text="", style=None):
        text = clean(text)
        self._write(self._style(text, style) if style else text)

    def _heading(self, text):
        self._line(text, "1;36")

    def _label(self, card, catalog=None):
        type_id = card.get("type", "card")
        item = next((item for item in (catalog or {}).get("node_types", []) if item["id"] == type_id), {})
        return clean(item.get("label") or type_id)

    def _accent(self, card):
        type_id = card.get("type", "")
        return "35" if "agent" in type_id else "33" if "sandbox" in type_id else "36"

    def _box(self, title, lines, width, accent="36"):
        inside = width - 4
        title = _clip(title, max(0, width - 6))
        top = "╭─ " + title + " " + "─" * max(0, width - display_width(title) - 5) + "╮"
        result = [self._style(top, accent)]
        for index, line in enumerate(lines):
            value = _pad(_clip(line, inside), inside)
            if index == 0:
                value = self._style(value, "1")
            result.append(self._style("│", accent) + " " + value + " " + self._style("│", accent))
        result.append(self._style("╰" + "─" * (width - 2) + "╯", accent))
        return result

    def cards(self, cards, catalog=None):
        if not cards:
            self._line("No cards yet.")
            self._line("oaw types   |   oaw create <type> --name <name>")
            return
        if self.plain:
            for card in cards:
                parts = [card.get("id", ""), card.get("name", "Untitled"), self._label(card, catalog)]
                if card.get("status"):
                    parts.append(card["status"])
                self._line("  ".join(str(part) for part in parts))
            return
        identities = short_ids(cards)
        id_width = max(display_width(clean(value)) for value in identities.values())
        type_width = max(4, min(20, max(display_width(self._label(card, catalog)) for card in cards)))
        status_width = max(6, min(12, max(display_width(clean(card.get("status", ""))) for card in cards)))
        available = self.width - id_width - type_width - status_width - 8
        name_width = min(available, 32, max(12, max(display_width(clean(card.get("name", "Untitled"))) for card in cards)))
        if name_width >= 12:
            self._line("  " + _pad("Name", name_width) + "  " + _pad("Type", type_width)
                       + "  " + _pad("Status", status_width) + "  ID", "2")
            for card in cards:
                name = _pad(_clip(card.get("name", "Untitled"), name_width), name_width)
                label = _pad(_clip(self._label(card, catalog), type_width), type_width)
                status = _pad(_clip(card.get("status", ""), status_width), status_width)
                self._write(self._style("▣ ", self._accent(card)) + self._style(name, "1") + "  " + label
                            + "  " + status + "  " + self._style(clean(identities[card.get("id", "")]), "2"))
            return
        for card in cards:
            lines = list(_wrap(card.get("name", "Untitled"), self.width - 2))
            for index, line in enumerate(lines):
                self._line(("▣ " if index == 0 else "  ") + line, "1" if index == 0 else None)
            for value in (self._label(card, catalog) + "  " + clean(card.get("status", "")), identities[card.get("id", "")]):
                for line in _wrap(value, self.width):
                    self._line(line)

    def card(self, card, catalog=None, connections=None):
        width = min(72, self.width)
        identity = card.get("id") if self.plain else card.get("short_id", card.get("id"))
        fields = [("Name", card.get("name", "Untitled")), ("ID", identity), ("Status", card.get("status"))]
        config = card.get("config", {})
        fields.extend((label, config[key]) for key, label in (("model", "Model"), ("runtime", "Runtime")) if config.get(key))
        details = [f"{key}  {clean(value)}" for key, value in fields if value is not None]
        resource = card.get("resource") or {}
        if resource.get("filename"):
            details.append(f"File  {clean(resource['filename'])}")
        preview = resource.get("preview")
        if preview:
            details.extend(["", *list(_wrap(preview, width - 4))[:5]])
        width = min(width, max(40, display_width(self._label(card, catalog)) + 6,
                               *(display_width(line) + 4 for line in details)))
        if self.plain:
            self._line(self._label(card, catalog))
            for line in details:
                self._line(line)
        else:
            for line in self._box(self._label(card, catalog), details, width, self._accent(card)):
                self._write(line)
        if connections:
            self._write()
            self.connections(connections, [card])

    def connections(self, edges, cards):
        names = {card["id"]: clean(card.get("name") or card["id"]) for card in cards}
        if not edges:
            self._line("No connections.")
            return
        identities = short_ids(edges)
        for edge in edges:
            source = edge.get("source_name") or names.get(edge.get("source"), edge.get("source", "?"))
            target = edge.get("target_name") or names.get(edge.get("target"), edge.get("target", "?"))
            relation = edge.get("relationship", "connected")
            arrow = "<->" if edge.get("direction") == "bidirectional" else "->"
            identity = edge.get("id", "") if self.plain else identities[edge.get("id", "")]
            line = f"{identity}  {source}  -- {relation} {arrow}  {target}".strip()
            for wrapped in _wrap(line, self.width) if not self.plain else [line]:
                self._line(wrapped)

    def connected(self, data):
        self._heading("Connected")
        self.connections([data], [])

    def types(self, catalog):
        items = [item for item in catalog.get("node_types", []) if item.get("user_creatable", True)]
        if not items:
            self._line("No card types available.")
            return
        basics = {"agent", "conversation", "text", "image", "sandbox"}
        groups = {}
        for item in items:
            key = "basics" if item["id"] in basics else item.get("deck_id", "other")
            label = "Basics" if key == "basics" else item.get("deck_label") or key
            groups.setdefault(key, (label, []))[1].append(item)
        keys = sorted(groups, key=lambda key: (key != "basics", groups[key][0].casefold()))
        for index, key in enumerate(keys):
            if index:
                self._write()
            label, members = groups[key]
            self._heading(label)
            self._type_rows(members)

    def _type_rows(self, items):
        width = min(36, max(len(item["id"]) for item in items))
        for item in items:
            label = clean(item.get("label", ""))
            identity = clean(item["id"])
            line = "  " + _pad(identity, width) + ("  " + label if label.casefold() != identity.casefold() else "")
            for wrapped in _wrap(line, self.width) if not self.plain else [line]:
                self._line(wrapped)

    def type_groups(self, catalog):
        items = [item for item in catalog.get("node_types", []) if item.get("user_creatable", True)]
        basics = {"agent", "conversation", "text", "image", "sandbox"}
        primary = [item for item in items if item["id"] in basics]
        self._heading("Basics")
        if primary:
            order = {key: index for index, key in enumerate(("agent", "conversation", "text", "image", "sandbox"))}
            self._type_rows(sorted(primary, key=lambda item: order[item["id"]]))
        groups = {}
        for item in items:
            key = item.get("deck_id", "other")
            label, members = groups.setdefault(key, (item.get("deck_label") or key, []))
            members.append(item)
        groups = [(key, label, members) for key, (label, members) in groups.items()
                  if any(item["id"] not in basics for item in members)]
        if groups:
            self._write()
            self._heading("More card groups")
            width = min(28, max(len(key) for key, _, _ in groups))
            for key, label, members in sorted(groups, key=lambda group: group[1].casefold()):
                line = "  " + _pad(clean(key), width) + "  " + clean(label) + f"  ({len(members)})"
                for wrapped in _wrap(line, self.width) if not self.plain else [line]:
                    self._line(wrapped)
            self._write()
            self._line("oaw types GROUP   |   oaw types --all")

    def candidates(self, items):
        for index, item in enumerate(items, 1):
            identity = item.get("id") or item.get("run_id", "")
            label = item.get("name") or item.get("label") or item.get("profile") or item.get("mode", "")
            if clean(label).casefold() == clean(identity).casefold():
                label = ""
            self._line(f"  {index}  " + "  ".join(clean(value) for value in (identity, label, item.get("url")) if value))

    def instances(self, instances):
        if not instances:
            self._line("No running OAW instance. Open OAW first.")
        for instance in instances:
            self._line("  ".join(str(value) for value in (
                instance["id"], instance.get("profile") or instance.get("mode"), instance["url"]) if value))

    def models(self, data):
        default = data.get("default_model")
        if not default:
            self._line("No default model. Open OAW Settings / Models to choose one.")
        available = [(connection, model) for connection in data.get("connections", [])
                     if connection.get("enabled", True) for model in connection.get("models", [])
                     if model.get("enabled", True)]
        for connection, model in available:
            reference = "oaw:model:" + model["id"]
            name = model.get("name") or model.get("model_id") or model["id"]
            label = f"{name}  {connection.get('name', '')}".rstrip()
            if default == reference:
                label += "  (default)"
            if connection.get("auth_mode") == "api_key" and not connection.get("api_key_configured"):
                label += "  (API key needed)"
            self._line(label)
        if default and not available:
            self._line("No enabled models. Open OAW Settings / Models.")

    def schema(self, data):
        self._heading(data.get("label") or data.get("id", "Card"))
        self._line(data.get("id", ""))
        schema = data.get("config_schema", {})

        def field_type(spec):
            if "$ref" in spec:
                name = spec["$ref"].rsplit("/", 1)[-1]
                spec = schema.get("$defs", {}).get(name, {"type": name})
            variants = spec.get("anyOf") or spec.get("oneOf")
            if variants:
                return " | ".join(dict.fromkeys(field_type(item) for item in variants))
            kind = spec.get("type", "any")
            if isinstance(kind, list):
                kind = " | ".join(kind)
            if kind == "array":
                kind = field_type(spec.get("items", {})) + "[]"
            return kind

        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if properties:
            self._write()
            self._heading("Settings")
            for name, spec in properties.items():
                label = f"{name}  {field_type(spec)}"
                if name in required:
                    label += "  required"
                for line in _wrap(label, self.width - 2):
                    self._line("  " + line)
                description = spec.get("description")
                if description:
                    description = _clip(description, 120, "..." if self.plain else "…")
                    for line in _wrap(description, self.width - 4):
                        self._line("    " + line, "2")
        else:
            self._line("No settings.")
        relationships = data.get("relationships", [])
        if relationships:
            self._write()
            self._heading("Connections")
            for item in relationships:
                line = item["id"]
                if item.get("label") and item["label"] != item["id"]:
                    line += "  " + item["label"]
                for wrapped in _wrap(line, self.width - 2):
                    self._line("  " + wrapped)

    def runs(self, runs):
        if not runs:
            self._line("No runs yet.")
            return
        identities = short_ids(runs, "run_id")
        for run in runs:
            identity = run.get("run_id", "") if self.plain else identities[run.get("run_id", "")]
            name = run.get("agent_name") or run.get("agent_id", "")
            line = "  ".join(str(part) for part in (identity, name, run.get("status", "")))
            for wrapped in _wrap(line, self.width) if not self.plain else [line]:
                self._line(wrapped)
            if run.get("error"):
                reason = _clip(run["error"], max(4, self.width - 2), "..." if self.plain else "…")
                self._line("  " + reason, "31")

    def chat(self, data):
        messages = data.get("messages")
        if messages is not None:
            if not messages:
                self._line("No messages yet.")
            for index, message in enumerate(messages):
                if index:
                    self._write()
                self._heading(message.get("sender_name") or message.get("sender_kind", "Message"))
                self.value(message.get("content", ""))
            return
        replies = data.get("replies", [])
        if not replies and data.get("text"):
            self.value(data["text"])
        for index, reply in enumerate(replies):
            if index:
                self._write()
            self._heading(reply.get("agent_name") or data.get("card_name") or "Agent")
            if reply.get("text"):
                self.value(reply["text"])
            if reply.get("error"):
                self.error(reply["error"])

    def result(self, data):
        run = data.get("run", data)
        self._line("  ".join(str(value) for value in (run.get("run_id"), run.get("status")) if value))
        output = data.get("output_text", data.get("text", data.get("output")))
        if output:
            self._write()
            for line in clean(output, multiline=True).split("\n"):
                self._write(line)
        if run.get("error"):
            self.error(run["error"])

    def submitted(self, data):
        run = data.get("run", data)
        name = data.get("agent_name") or run.get("agent_name")
        self._heading(f"Started {name}" if name else "Run started")
        self._line(run.get("run_id", ""))
        self._write()
        self._line(f"oaw wait {run.get('run_id', '')}")

    def status(self, data):
        instance = data.get("instance") or data
        self._heading("OAW")
        if data.get("connected") is False:
            self._line("Not connected.")
            if data.get("cli_version"):
                self._line(f"CLI {data['cli_version']}")
            self._write()
            self._line(data.get("hint") or "Open OAW, then run: oaw doctor")
            return
        parts = [instance.get("mode"), instance.get("profile"), instance.get("version")]
        if any(parts):
            self._line("  ".join(str(part) for part in parts if part))
        if instance.get("url") or instance.get("endpoint"):
            self._line(instance.get("url") or instance["endpoint"])
        if data.get("status"):
            self._line(data["status"])
        if data.get("cli_version"):
            parts = [f"CLI {data['cli_version']}"]
            if data.get("python"):
                parts.append(f"Python {data['python']}")
            self._line("  ".join(parts))

    def overview(self, data):
        instance = data.get("instance")
        if not instance:
            self._heading("OAW")
            self._line("No running instance.")
            self._write()
            self._line("oaw start")
            self._line("oaw --help")
            return
        self.status({"instance": instance})
        cards, edges = data.get("cards", []), data.get("edges", [])
        self._write()
        separator = " | " if self.plain else " · "
        self._line(f"{len(cards)} {'card' if len(cards) == 1 else 'cards'}" + separator
                   + f"{len(edges)} {'connection' if len(edges) == 1 else 'connections'}")
        self._write()
        self.cards(cards[:6], data.get("catalog"))
        if len(cards) > 6:
            self._line(f"{len(cards) - 6} more" + separator + "oaw cards")
        if edges:
            self._write()
            self.connections(edges[:4], cards)
            if len(edges) > 4:
                self._line(f"{len(edges) - 4} more" + separator + "oaw connections")
        active = [run for run in data.get("runs", []) if run.get("status") in {"created", "running", "waiting"}]
        if active:
            self._write()
            self.runs(active[:3])

    def value(self, data):
        if isinstance(data, str):
            for line in clean(data, multiline=True).split("\n"):
                self._write(line)
        else:
            for line in json.dumps(data, ensure_ascii=False, indent=2).split("\n"):
                self._line(line)

    def error(self, message, hint=None):
        self._line(f"Error: {message}", "31")
        if hint:
            self._line(hint)
