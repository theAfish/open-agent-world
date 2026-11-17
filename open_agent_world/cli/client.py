"""Standard-library HTTP client; no backend imports or profile access."""
from __future__ import annotations

import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class CliError(Exception):
    def __init__(self, code, message, *, hint=None, exit_code=1, **details):
        super().__init__(message)
        self.exit_code = exit_code
        self.error = {"code": code, "message": message, **details}
        if hint:
            self.error["hint"] = hint


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint_url(value):
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise CliError("invalid_endpoint", "The server address is invalid.", exit_code=2) from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/api"}):
        raise CliError("invalid_endpoint", "Use an HTTP(S) server address, for example http://127.0.0.1:38473.", exit_code=2)
    try:
        parsed.port
    except ValueError as exc:
        raise CliError("invalid_endpoint", "The server port is invalid.", exit_code=2) from exc
    return f"{parsed.scheme}://{parsed.netloc}"


def choose_instance(endpoint=None, selector=None):
    from open_agent_world.instances import list_instances
    if endpoint and selector:
        raise CliError("invalid_arguments", "Choose either --endpoint or --instance.", exit_code=2)
    explicit = endpoint or (os.environ.get("OPEN_AGENT_WORLD_URL") if not selector else None)
    if explicit:
        return {"url": endpoint_url(explicit), "mode": "connected"}
    instances = list_instances()
    if selector:
        instances = [item for item in instances if item["id"].startswith(selector)
                     or item.get("profile") == selector or item.get("mode") == selector]
    else:
        instances = [item for item in instances if item.get("mode") == "production"]
    if not instances:
        raise CliError("not_connected", "No running OAW instance found.", exit_code=3,
                       hint="Open OAW, or run oaw start.")
    if len(instances) != 1:
        raise CliError("ambiguous_instance", "More than one OAW instance matches.", exit_code=2,
                       hint="Use oaw instances, then --instance ID.",
                       candidates=[{key: item.get(key) for key in ("id", "mode", "profile", "url")} for item in instances])
    return instances[0]


class Client:
    def __init__(self, instance, *, timeout=30):
        self.instance = instance
        self.url = endpoint_url(instance["url"])
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._cards = None
        self._catalog = None

    def request(self, method, path, data=None, **query):
        url = self.url + "/api" + path
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        headers = {"Accept": "application/json"}
        token = os.environ.get("OPEN_AGENT_WORLD_CONTROL_PLANE_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read()
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read())
                error = payload.get("error", {})
            except (ValueError, AttributeError):
                error = {}
            code = error.get("code", "http_error")
            status = exc.code
            raise CliError(code, error.get("message", f"OAW returned HTTP {status}."),
                           exit_code=4 if status in {401, 403} else 5 if status == 409 else 1,
                           **{key: value for key, value in error.items() if key not in {"code", "message"}},
                           http_status=status) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            writing = method not in {"GET", "HEAD"}
            raise CliError("outcome_unknown" if writing else "connection_failed",
                           "The connection ended before OAW confirmed the change." if writing else "Cannot reach OAW.",
                           exit_code=3, retryable=not writing,
                           hint="Check the current state before submitting again." if writing
                           else "Check that OAW is running, then try oaw instances.") from exc
        if not content:
            return None
        try:
            return json.loads(content)
        except ValueError as exc:
            raise CliError("invalid_response", "The server did not return OAW JSON.", exit_code=3,
                           hint="Check --endpoint points to the OAW backend.") from exc

    def verify(self):
        if self.instance.get("id"):
            identity = self.request("GET", "/instance")
            if not identity or identity.get("id") != self.instance["id"]:
                raise CliError("instance_changed", "This address now belongs to a different instance.", exit_code=3,
                               hint="Run oaw instances and select the instance again.")

    def cards(self):
        if self._cards is None:
            self._cards = self.request("GET", "/nodes")
        return self._cards

    def catalog(self):
        if self._catalog is None:
            self._catalog = self.request("GET", "/catalog")
        return self._catalog

    def card(self, selector):
        items = self.cards()
        exact = [item for item in items if item["id"] == selector]
        matches = exact or [item for item in items if item["name"].casefold() == selector.casefold()]
        matches = matches or [item for item in items if item["id"].startswith(selector)]
        if not matches:
            try:
                selected_type = self.card_type(selector)["id"]
            except CliError:
                selected_type = None
            if selected_type:
                matches = [item for item in items if item["type"] == selected_type]
        return resolve_one(matches, "card", selector, items)

    def definition(self, card):
        return next((item for item in self.catalog()["node_types"] if item["id"] == card["type"]), {})

    def card_type(self, selector):
        items = self.catalog()["node_types"]
        exact = [item for item in items if item["id"] == selector]
        matches = exact or [item for item in items if selector.casefold() in {
            item["id"].rsplit(".", 1)[-1].casefold(), item["label"].casefold()}]
        return resolve_one(matches, "card type", selector, items)

    def run(self, selector):
        try:
            return self.request("GET", "/runs/" + quote(selector, safe=""))
        except CliError as exc:
            if exc.error.get("http_status") != 404:
                raise
        items = self.request("GET", "/runs", limit=200)
        matches = [item for item in items if item["run_id"].startswith(selector)]
        return resolve_one(matches, "run", selector, items, id_key="run_id")


def resolve_one(matches, kind, selector, items=(), *, id_key="id"):
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CliError("not_found", f"No {kind} matches '{selector}'.", exit_code=2,
                       hint=f"Use the full {kind} ID or an exact name.")
    raise CliError("ambiguous_selector", f"More than one {kind} matches '{selector}'.", exit_code=2,
                   hint="Use one of the full IDs.",
                   candidates=[{id_key: item[id_key], "name": item.get("name")} for item in matches])
